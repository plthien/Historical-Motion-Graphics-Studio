from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from app.api.errors import AppError
from app.core.ids import validate_artifact_id


class DuplicateJsonKeyError(ValueError):
    def __init__(self, key: str) -> None:
        super().__init__(f"Duplicate JSON object key: {key}")
        self.key = key


def parse_external_json(result_json: str, *, max_bytes: int = 10_000_000) -> dict[str, Any]:
    if not isinstance(result_json, str) or not result_json.strip():
        raise AppError(
            "EXTERNAL_RESULT_EMPTY",
            "Imported JSON text must not be empty.",
            422,
            retryable=False,
            details={"errors": [{"path": "$", "code": "empty_json", "message": "Imported JSON text is empty."}]},
        )
    encoded = result_json.encode("utf-8")
    if len(encoded) > max_bytes:
        raise AppError(
            "EXTERNAL_RESULT_TOO_LARGE",
            "Imported JSON exceeds the supported size.",
            413,
            retryable=False,
            details={"errors": [{"path": "$", "code": "json_too_large", "message": f"Maximum size is {max_bytes} bytes."}]},
        )

    def object_pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise DuplicateJsonKeyError(key)
            result[key] = value
        return result

    try:
        payload = json.loads(result_json, object_pairs_hook=object_pairs_hook)
    except DuplicateJsonKeyError as exc:
        raise AppError(
            "EXTERNAL_RESULT_DUPLICATE_KEY",
            "Imported JSON contains a duplicate object key.",
            422,
            retryable=False,
            details={"errors": [{"path": "$", "code": "duplicate_key", "message": str(exc), "key": exc.key}]},
        ) from exc
    except json.JSONDecodeError as exc:
        raise AppError(
            "EXTERNAL_RESULT_JSON_INVALID",
            "Imported result is not valid JSON.",
            422,
            retryable=False,
            details={"errors": [{"path": "$", "code": "json_parse_error", "message": exc.msg, "line": exc.lineno, "column": exc.colno}]},
        ) from exc
    if not isinstance(payload, dict):
        raise AppError(
            "EXTERNAL_RESULT_ROOT_INVALID",
            "Imported JSON root must be an object.",
            422,
            retryable=False,
            details={"errors": [{"path": "$", "code": "root_not_object", "message": "Expected a JSON object root."}]},
        )
    return payload


def _schema_id_index(schema_search_dirs: list[Path]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for directory in schema_search_dirs:
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.schema.json")):
            schema = json.loads(path.read_text(encoding="utf-8"))
            schema_id = schema.get("$id")
            if isinstance(schema_id, str) and schema_id:
                index[schema_id] = schema
    return index


def bundle_prompt_schema(
    output_schema: dict[str, Any],
    *,
    schema_search_dirs: list[Path] | None = None,
) -> dict[str, Any]:
    """Return a prompt-facing, self-contained schema without project-external refs or IDs.

    Runtime validators keep using their original schema graph. This copy exists only so an
    external authoring model can see every required output definition inside one prompt.
    """

    root = deepcopy(output_schema)
    default_schema_root = Path(__file__).resolve().parents[2] / "contracts" / "schemas"
    search_dirs = [Path(path) for path in schema_search_dirs] if schema_search_dirs is not None else [default_schema_root]
    schema_index = _schema_id_index(search_dirs)
    bundled_by_id: dict[str, str] = {}
    bundled_docs: dict[str, dict[str, Any]] = {}
    root_defs = root.setdefault("$defs", {})
    if not isinstance(root_defs, dict):
        raise AppError(
            "AUTHORING_PROMPT_SCHEMA_INVALID",
            "Prompt-facing schema has a non-object $defs value.",
            500,
            retryable=False,
        )

    def next_bundle_key() -> str:
        index = 1
        while True:
            key = f"bundled_{index:03d}"
            if key not in root_defs and key not in bundled_docs:
                return key
            index += 1

    def rewrite_ref(ref: str, *, local_prefix: str) -> str:
        if ref.startswith("#"):
            return f"{local_prefix}{ref[1:]}" if local_prefix else ref
        document_id, separator, fragment = ref.partition("#")
        target = schema_index.get(document_id)
        if target is None:
            raise AppError(
                "AUTHORING_PROMPT_SCHEMA_EXTERNAL_REF_UNRESOLVED",
                "External authoring output schema contains a reference that cannot be bundled into the prompt.",
                500,
                retryable=False,
                details={"ref": ref},
            )
        bundle_key = bundled_by_id.get(document_id)
        if bundle_key is None:
            bundle_key = next_bundle_key()
            bundled_by_id[document_id] = bundle_key
            bundle_prefix = f"#/$defs/{bundle_key}"
            bundled_docs[bundle_key] = transform(deepcopy(target), local_prefix=bundle_prefix)
        suffix = f"/{fragment.lstrip('/')}" if separator and fragment else ""
        return f"#/$defs/{bundle_key}{suffix}"

    def transform(value: Any, *, local_prefix: str) -> Any:
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for key, item in value.items():
                if key == "$id":
                    continue
                if key == "$ref" and isinstance(item, str):
                    result[key] = rewrite_ref(item, local_prefix=local_prefix)
                else:
                    result[key] = transform(item, local_prefix=local_prefix)
            return result
        if isinstance(value, list):
            return [transform(item, local_prefix=local_prefix) for item in value]
        return value

    transformed_root = transform(root, local_prefix="")
    transformed_defs = transformed_root.setdefault("$defs", {})
    if not isinstance(transformed_defs, dict):
        raise AppError(
            "AUTHORING_PROMPT_SCHEMA_INVALID",
            "Prompt-facing schema has a non-object $defs value.",
            500,
            retryable=False,
        )
    for key, document in bundled_docs.items():
        transformed_defs[key] = document
    return transformed_root


def render_external_prompt(
    *,
    stage: str,
    prompt_version: str,
    system_prompt: str,
    user_prompt: str,
    output_schema_name: str,
    output_schema: dict[str, Any],
    artifact_ids: dict[str, str],
    output_filename: str,
    schema_search_dirs: list[Path] | None = None,
) -> dict[str, Any]:
    prompt_schema = bundle_prompt_schema(output_schema, schema_search_dirs=schema_search_dirs)
    prompt_text = (
        f"# External Authoring Task: {stage}\n\n"
        f"## Reserved Artifact IDs\n\n```json\n{json.dumps(artifact_ids, ensure_ascii=False, indent=2)}\n```\n\n"
        "Copy these exact Artifact ID values into the matching identity fields in the output JSON. "
        "Do not modify, regenerate, omit, or replace any reserved Artifact ID.\n\n"
        f"## Instructions\n\n{system_prompt.strip()}\n\n"
        f"## Request\n\n{user_prompt.strip()}\n\n"
        "## Required JSON Schema\n\n"
        f"```json\n{json.dumps(prompt_schema, ensure_ascii=False, indent=2)}\n```\n\n"
        "## Required Delivery\n\n"
        f"Create and attach/export a UTF-8 JSON file named `{output_filename}`. "
        "The file must contain exactly one complete JSON object matching the schema, with no Markdown fences, comments, commentary, or extra text. "
        "If the current ChatGPT interface cannot attach a file, return the same pure JSON object directly as the entire response."
    )
    return {
        "prompt_text": prompt_text,
        "artifact_ids": deepcopy(artifact_ids),
        "output_schema_name": output_schema_name,
        "prompt_version": prompt_version,
    }


def extract_reserved_artifact_ids(payload: dict[str, Any], *, stage: str) -> tuple[str, str | None]:
    if stage == "topic_ideas":
        raw_primary = payload.get("artifact_id")
        primary_path = "$.artifact_id"
        raw_paired = None
        paired_path = None
    else:
        identity = payload.get("artifact_ids")
        if not isinstance(identity, dict):
            raise AppError(
                "AUTHORING_ARTIFACT_ID_MISSING",
                "The imported JSON is missing its reserved Artifact IDs.",
                422,
                retryable=False,
                details={"errors": [{"path": "$.artifact_ids", "code": "authoring_artifact_id_missing", "message": "Copy the exact Artifact IDs from the prompt into the output JSON."}]},
            )
        if stage == "script_and_analysis":
            raw_primary = identity.get("script")
            raw_paired = identity.get("analysis")
            primary_path = "$.artifact_ids.script"
            paired_path = "$.artifact_ids.analysis"
        elif stage == "scene_breakdown":
            raw_primary = identity.get("scene_plan")
            raw_paired = identity.get("visual_plan")
            primary_path = "$.artifact_ids.scene_plan"
            paired_path = "$.artifact_ids.visual_plan"
        else:
            raise ValueError(f"Unsupported external authoring stage: {stage}")

    def require_id(value: Any, path: str) -> str:
        try:
            return validate_artifact_id(value)
        except ValueError as exc:
            raise AppError(
                "AUTHORING_ARTIFACT_ID_MISSING",
                "The imported JSON does not contain a valid reserved Artifact ID.",
                422,
                retryable=False,
                details={"errors": [{"path": path, "code": "authoring_artifact_id_missing", "message": "Expected an Artifact ID copied exactly from the prompt."}]},
            ) from exc

    primary = require_id(raw_primary, primary_path)
    paired = require_id(raw_paired, paired_path) if paired_path is not None else None
    return primary, paired


def require_source_values(
    *,
    expected: dict[str, Any],
    current: dict[str, Any],
    fields: list[str],
    message: str,
) -> None:
    changed = [field for field in fields if expected.get(field) != current.get(field)]
    if not changed:
        return
    raise AppError(
        "AUTHORING_SOURCE_CHANGED",
        message,
        409,
        retryable=False,
        details={
            "errors": [
                {
                    "path": f"$.source.{field}",
                    "code": "authoring_source_changed",
                    "message": "The upstream project value changed after this prompt was built.",
                }
                for field in changed
            ],
            "changed_fields": changed,
        },
    )


def validation_success(*, summary: dict[str, Any] | None = None, warnings: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"valid": True, "errors": [], "warnings": warnings or [], "summary": summary or {}}


def validation_from_error(error: AppError) -> dict[str, Any]:
    details = error.details if isinstance(error.details, dict) else {}
    raw_errors = details.get("errors")
    errors: list[dict[str, Any]] = []
    if isinstance(raw_errors, list):
        for item in raw_errors:
            if isinstance(item, dict):
                errors.append(
                    {
                        "code": str(item.get("code") or item.get("keyword") or error.error_code),
                        "path": str(item.get("path") or "$"),
                        "message": str(item.get("message") or error.message),
                    }
                )
    if not errors:
        errors.append({"code": error.error_code, "path": "$", "message": error.message})
    return {"valid": False, "errors": errors, "warnings": [], "summary": {}}
