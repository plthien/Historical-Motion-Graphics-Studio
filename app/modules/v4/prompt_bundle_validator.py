from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from pydantic import ValidationError

from app.api.prompt_composer_schemas_v4 import PromptBundleModel, PromptBundleValidationReport, PromptComposerInput
from app.modules.v4.prompt_composer_input_builder import PromptComposerError

FORBIDDEN_EXACT = {
    "provider", "provider_type", "model", "api_key", "authorization", "seed", "sampler", "cfg", "cfg_scale",
    "steps", "provider_request", "provider_response", "task_id", "media_task_id", "generated_asset_id",
    "download_url", "local_path", "video_prompt", "motion_prompt", "reasoning", "analysis", "explanation", "chain_of_thought",
}


def _issue(path: str, code: str, message: str) -> dict[str, str]:
    return {"path": path, "code": code, "message": message}


def _scan_forbidden(value: Any, path: str = "$") -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key.lower() in FORBIDDEN_EXACT:
                issues.append(_issue(child_path, "PROMPT_COMPOSER_FIELD_FORBIDDEN", "Provider, task, asset, or reasoning fields are forbidden."))
            issues.extend(_scan_forbidden(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            issues.extend(_scan_forbidden(child, f"{path}[{index}]"))
    return issues


def _duplicates(values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicate: list[str] = []
    for value in values:
        if value in seen and value not in duplicate:
            duplicate.append(value)
        seen.add(value)
    return duplicate


def _empty_report() -> dict[str, Any]:
    return {
        **{key: "passed" for key in (
            "lineage_status", "beat_coverage_status", "order_status", "source_identity_status",
            "referential_integrity_status", "prompt_content_status", "negative_prompt_status",
            "generation_options_status", "reference_binding_status", "prompt_hash_status",
            "forbidden_field_status",
        )},
        "missing_prompt_beat_ids": [], "duplicate_prompt_beat_ids": [], "unexpected_prompt_beat_ids": [],
        "order_errors": [], "unknown_reference_errors": [], "prompt_content_errors": [],
        "negative_prompt_errors": [], "generation_option_errors": [], "reference_binding_errors": [],
        "prompt_hash_errors": [], "forbidden_field_errors": [], "warnings": [],
    }

def _without_revision_metadata(bundle: dict[str, Any]) -> dict[str, Any]:
    semantic = deepcopy(bundle)
    for field in (
        "prompt_bundle_revision_id",
        "parent_prompt_bundle_revision_id",
        "revision_operation",
        "operation_target_beat_id",
        "status",
        "backend_qc",
    ):
        semantic.pop(field, None)
    return semantic


def _prompt_item_by_beat(bundle: dict[str, Any], beat_id: str) -> dict[str, Any]:
    matches = [item for item in bundle.get("prompt_items", []) if isinstance(item, dict) and item.get("beat_id") == beat_id]
    if len(matches) != 1:
        raise PromptComposerError(
            "PROMPT_BUNDLE_VALIDATION_FAILED",
            "Prompt Bundle target beat must resolve to exactly one prompt item.",
        )
    return matches[0]


def _report_for_child(*, warning: str | None = None) -> dict[str, Any]:
    report = _empty_report()
    if warning:
        report["warnings"].append(warning)
    return report


def _assert_equal(actual: Any, expected: Any, *, code: str, message: str) -> None:
    if actual != expected:
        raise PromptComposerError(code, message)


def _assert_prompt_text_hygiene(value: Any, *, code: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise PromptComposerError(code, "Prompt text must be non-empty.")
    if any(ord(character) < 32 and character not in "\t\n\r" for character in value) or "\x7f" in value:
        raise PromptComposerError(code, "Prompt text contains disallowed control characters.")
    stripped = value.strip()
    if "```" in value or (stripped.startswith("{") and stripped.endswith("}")) or (stripped.startswith("[") and stripped.endswith("]")):
        raise PromptComposerError(code, "Prompt text cannot be code-fenced JSON or structured provider syntax.")
    forbidden = ("--ar ", "--seed", "cfg_scale", "sampler=", "steps=", "model=", "provider=")
    if any(token in value.lower() for token in forbidden):
        raise PromptComposerError(code, "Prompt text cannot include provider-specific option syntax.")


_REFERENCE_TOKEN_PATTERN = re.compile(r"@([A-Za-z][A-Za-z0-9_]*)\b")


def _assert_reference_tag_contract(prompt_item: dict[str, Any]) -> None:
    required = {str(binding.get("anchor_id")) for binding in prompt_item.get("reference_bindings", [])}
    requirement = prompt_item.get("continuity_reference_requirement")
    if isinstance(requirement, dict):
        required.add("prev_beat")
    actual = set(_REFERENCE_TOKEN_PATTERN.findall(str(prompt_item.get("image_prompt") or "")))
    if actual != required:
        raise PromptComposerError(
            "PROMPT_BUNDLE_REFERENCE_SET_MISMATCH",
            "Edited image prompt reference tags must exactly match the beat reference contract.",
            details={"required_reference_tags": sorted(required), "prompt_reference_tags": sorted(actual)},
        )


def _assert_same_lineage(candidate: dict[str, Any], parent: dict[str, Any]) -> None:
    for field in (
        "project_id",
        "source_script_revision_id",
        "source_script_analysis_revision_id",
        "source_style_bible_revision_id",
        "source_scene_plan_revision_id",
        "source_visual_plan_revision_id",
        "source_visual_reference_approval_set_artifact_id",
    ):
        _assert_equal(candidate.get(field), parent.get(field), code="PROMPT_BUNDLE_VALIDATION_FAILED", message=f"Child revision must preserve {field}.")


def _assert_parent_link(candidate: dict[str, Any], parent: dict[str, Any]) -> None:
    parent_id = parent.get("prompt_bundle_revision_id")
    if parent.get("parent_prompt_bundle_revision_id") == parent_id:
        raise PromptComposerError("PROMPT_BUNDLE_VALIDATION_FAILED", "Prompt Bundle parent lineage is self-referential.")
    if candidate.get("prompt_bundle_revision_id") == parent_id:
        raise PromptComposerError("PROMPT_BUNDLE_VALIDATION_FAILED", "Prompt Bundle cannot be its own parent.")
    _assert_equal(candidate.get("parent_prompt_bundle_revision_id"), parent_id, code="PROMPT_BUNDLE_VALIDATION_FAILED", message="Child revision must record the direct parent revision.")
    _assert_same_lineage(candidate, parent)


def _validate_child_contract(candidate: dict[str, Any], parent: dict[str, Any], *, operation: str, target_beat_id: str | None) -> None:
    _assert_parent_link(candidate, parent)
    _assert_equal(candidate.get("revision_operation"), operation, code="PROMPT_BUNDLE_VALIDATION_FAILED", message="Child revision operation is invalid.")
    _assert_equal(candidate.get("operation_target_beat_id"), target_beat_id, code="PROMPT_BUNDLE_VALIDATION_FAILED", message="Child revision target metadata is invalid.")
    expected_status = "approved" if operation in {"approval", "beat_recompilation", "manual_edit"} else "draft_ready"
    _assert_equal(candidate.get("status"), expected_status, code="PROMPT_BUNDLE_VALIDATION_FAILED", message="Child revision status is invalid for the operation.")
    for field in (
        "artifact_type",
        "schema_version",
        "project_id",
        "source_script_revision_id",
        "source_script_analysis_revision_id",
        "source_style_bible_revision_id",
        "source_scene_plan_revision_id",
        "source_visual_plan_revision_id",
        "source_visual_reference_approval_set_artifact_id",
        "source_beat_count",
        "prompt_item_count",
        "options",
    ):
        _assert_equal(candidate.get(field), parent.get(field), code="PROMPT_BUNDLE_VALIDATION_FAILED", message=f"Child revision must preserve {field}.")


def validate_prompt_bundle_revision(
    *,
    operation: str,
    bounded_input: dict[str, Any] | None,
    candidate: dict[str, Any],
    parent: dict[str, Any] | None = None,
    canonical_candidate: dict[str, Any] | None = None,
    target_beat_id: str | None = None,
) -> dict[str, Any]:
    """Validate a Prompt Bundle revision for the operation that created it.

    Canonical compilation uses the full deterministic validator. Child
    revisions are parent-equivalence checks with operation-specific allowed
    differences, producing a fresh QA report instead of inheriting parent QA.
    """
    if operation in {"full_compilation", "automatic_compilation"}:
        if bounded_input is None:
            raise PromptComposerError("PROMPT_COMPOSER_INPUT_INVALID", "Canonical validation requires bounded input.")
        return validate_prompt_bundle(prompt_composer_input=bounded_input, prompt_bundle=candidate, allow_missing_backend_qc=True)

    if parent is None:
        raise PromptComposerError("PROMPT_BUNDLE_VALIDATION_FAILED", "Child revision validation requires a parent.")

    if operation == "manual_edit":
        if target_beat_id is None:
            raise PromptComposerError("PROMPT_BUNDLE_VALIDATION_FAILED", "Manual edit requires a target beat.")
        _validate_child_contract(candidate, parent, operation=operation, target_beat_id=target_beat_id)
        parent_target = _prompt_item_by_beat(parent, target_beat_id)
        candidate_target = _prompt_item_by_beat(candidate, target_beat_id)
        for field in (
            "prompt_item_id",
            "scene_id",
            "beat_id",
            "beat_order",
            "local_beat_order",
            "continuity_group_id",
            "visual_priority",
            "target_asset_role",
            "generation_options",
            "reference_bindings",
            "continuity_reference_requirement",
            "source_provenance",
        ):
            _assert_equal(candidate_target.get(field), parent_target.get(field), code="PROMPT_BUNDLE_MANUAL_EDIT_INVALID", message=f"Manual edit cannot alter {field}.")
        _assert_equal(candidate_target.get("prompt_origin"), "manual_edit", code="PROMPT_BUNDLE_MANUAL_EDIT_INVALID", message="Manual edit target must record manual_edit origin.")
        _assert_prompt_text_hygiene(candidate_target.get("image_prompt"), code="PROMPT_BUNDLE_MANUAL_EDIT_INVALID")
        _assert_prompt_text_hygiene(candidate_target.get("negative_prompt"), code="PROMPT_BUNDLE_MANUAL_EDIT_INVALID")
        _assert_reference_tag_contract(candidate_target)
        from app.modules.v4.prompt_composer import compute_prompt_hash
        _assert_equal(
            candidate_target.get("prompt_hash"),
            compute_prompt_hash(
                image_prompt=candidate_target["image_prompt"],
                negative_prompt=candidate_target["negative_prompt"],
                generation_options=candidate_target["generation_options"],
                reference_bindings=candidate_target["reference_bindings"],
            ),
            code="PROMPT_BUNDLE_MANUAL_EDIT_INVALID",
            message="Manual edit prompt hash must match the edited canonical material.",
        )
        _assert_equal(len(candidate.get("prompt_items", [])), len(parent.get("prompt_items", [])), code="PROMPT_BUNDLE_MANUAL_EDIT_INVALID", message="Manual edit cannot alter prompt count.")
        for parent_item, candidate_item in zip(parent.get("prompt_items", []), candidate.get("prompt_items", []), strict=True):
            if parent_item.get("beat_id") == target_beat_id:
                continue
            _assert_equal(candidate_item, parent_item, code="PROMPT_BUNDLE_MANUAL_EDIT_INVALID", message="Manual edit cannot alter unrelated prompts.")
        return _report_for_child(warning="Manual prompt text differs from deterministic canonical composition by producer edit.")

    if operation == "approval":
        _validate_child_contract(candidate, parent, operation=operation, target_beat_id=None)
        if parent.get("status") != "draft_ready":
            raise PromptComposerError("PROMPT_BUNDLE_STATUS_INVALID", "Only draft-ready Prompt Bundles can be approved.")
        _assert_equal(_without_revision_metadata(candidate), _without_revision_metadata(parent), code="PROMPT_BUNDLE_VALIDATION_FAILED", message="Approval must preserve parent semantic content exactly.")
        return _report_for_child()

    if operation == "beat_recompilation":
        if target_beat_id is None or canonical_candidate is None:
            raise PromptComposerError("PROMPT_BUNDLE_VALIDATION_FAILED", "Beat recompilation requires a target beat and canonical candidate.")
        _validate_child_contract(candidate, parent, operation=operation, target_beat_id=target_beat_id)
        parent_target = _prompt_item_by_beat(parent, target_beat_id)
        candidate_target = _prompt_item_by_beat(candidate, target_beat_id)
        canonical_target = _prompt_item_by_beat(canonical_candidate, target_beat_id)
        _assert_equal(candidate_target.get("prompt_origin"), "compiled", code="PROMPT_BUNDLE_VALIDATION_FAILED", message="Beat recompilation target must restore compiled origin.")
        _assert_equal(candidate_target.get("prompt_item_id"), parent_target.get("prompt_item_id"), code="PROMPT_BUNDLE_VALIDATION_FAILED", message="Beat recompilation must preserve target prompt item ID.")
        for field in (
            "scene_id",
            "beat_id",
            "beat_order",
            "local_beat_order",
            "continuity_group_id",
            "visual_priority",
            "target_asset_role",
            "generation_options",
            "reference_bindings",
            "continuity_reference_requirement",
            "source_provenance",
        ):
            _assert_equal(candidate_target.get(field), parent_target.get(field), code="PROMPT_BUNDLE_VALIDATION_FAILED", message=f"Beat recompilation cannot alter target {field}.")
        _assert_equal(candidate_target.get("image_prompt"), canonical_target.get("image_prompt"), code="PROMPT_BUNDLE_VALIDATION_FAILED", message="Beat recompilation target image prompt must be canonical.")
        _assert_equal(candidate_target.get("negative_prompt"), canonical_target.get("negative_prompt"), code="PROMPT_BUNDLE_VALIDATION_FAILED", message="Beat recompilation target negative prompt must be canonical.")
        _assert_equal(candidate_target.get("prompt_hash"), canonical_target.get("prompt_hash"), code="PROMPT_BUNDLE_VALIDATION_FAILED", message="Beat recompilation target prompt hash must be canonical.")
        _assert_equal(len(candidate.get("prompt_items", [])), len(parent.get("prompt_items", [])), code="PROMPT_BUNDLE_VALIDATION_FAILED", message="Beat recompilation cannot alter prompt count.")
        for parent_item, candidate_item in zip(parent.get("prompt_items", []), candidate.get("prompt_items", []), strict=True):
            if parent_item.get("beat_id") == target_beat_id:
                continue
            _assert_equal(candidate_item, parent_item, code="PROMPT_BUNDLE_VALIDATION_FAILED", message="Beat recompilation cannot alter unrelated prompts.")
        return _report_for_child()

    raise PromptComposerError("PROMPT_BUNDLE_VALIDATION_FAILED", "Unsupported Prompt Bundle revision operation.")


def _assert_backend_qc_matches(candidate: dict[str, Any], recomputed_report: dict[str, Any]) -> dict[str, Any]:
    try:
        authoritative = PromptBundleValidationReport.model_validate(deepcopy(recomputed_report)).model_dump(mode="json")
        stored = PromptBundleModel.model_validate(deepcopy(candidate)).backend_qc.model_dump(mode="json")
    except ValidationError as exc:
        raise PromptComposerError("PROMPT_BUNDLE_VALIDATION_FAILED", "Prompt Bundle contract is invalid.") from exc
    if stored != authoritative:
        raise PromptComposerError(
            "PROMPT_BUNDLE_VALIDATION_FAILED",
            "Prompt Bundle backend_qc does not match deterministic validation.",
        )
    return authoritative


def validate_persisted_prompt_bundle(
    *,
    bounded_input: dict[str, Any],
    candidate: dict[str, Any],
    parent: dict[str, Any] | None = None,
    canonical_candidate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a stored Prompt Bundle at the persisted trust boundary.

    Stored ``backend_qc`` is derived data. This function recomputes the
    operation-aware report and requires exact structured equality, including
    warnings, before a current or parent-eligible bundle is trusted.
    """
    operation = candidate.get("revision_operation")
    target_beat_id = candidate.get("operation_target_beat_id")
    parent_id = candidate.get("parent_prompt_bundle_revision_id")

    if operation == "automatic_compilation":
        if target_beat_id is not None or parent_id is not None or parent is not None:
            raise PromptComposerError("PROMPT_BUNDLE_VALIDATION_FAILED", "Automatic compilation must be a root revision without a target.")
        if candidate.get("status") != "approved":
            raise PromptComposerError("PROMPT_BUNDLE_STATUS_INVALID", "Automatic compilation must be approved.")
    elif operation == "full_compilation":
        if target_beat_id is not None:
            raise PromptComposerError("PROMPT_BUNDLE_VALIDATION_FAILED", "Full compilation cannot target a beat.")
        if candidate.get("status") != "draft_ready":
            raise PromptComposerError("PROMPT_BUNDLE_STATUS_INVALID", "Full compilation must remain draft-ready.")
        if parent_id is None:
            if parent is not None:
                raise PromptComposerError("PROMPT_BUNDLE_VALIDATION_FAILED", "Initial compilation cannot include a parent.")
        elif parent is None:
            raise PromptComposerError("PROMPT_BUNDLE_VALIDATION_FAILED", "Full recompilation requires its direct parent.")
        else:
            _assert_parent_link(candidate, parent)

    elif parent is None:
        raise PromptComposerError("PROMPT_BUNDLE_VALIDATION_FAILED", "Persisted child revision requires its direct parent.")

    report = validate_prompt_bundle_revision(
        operation=str(operation),
        bounded_input=bounded_input if operation in {"full_compilation", "automatic_compilation"} else None,
        candidate=candidate,
        parent=parent,
        canonical_candidate=canonical_candidate,
        target_beat_id=target_beat_id if isinstance(target_beat_id, str) else None,
    )
    _assert_backend_qc_matches(candidate, report)
    return candidate


def _validate_prompt_bundle(
    *,
    prompt_composer_input: dict[str, Any],
    prompt_bundle: dict[str, Any],
    allow_missing_backend_qc: bool = False,
) -> dict[str, Any]:
    try:
        parsed_input = PromptComposerInput.model_validate(deepcopy(prompt_composer_input)).model_dump(mode="json")
    except ValidationError as exc:
        raise PromptComposerError("PROMPT_COMPOSER_INPUT_INVALID", "Prompt Composer input is invalid.") from exc
    report = _empty_report()
    forbidden = _scan_forbidden(prompt_composer_input) + _scan_forbidden(prompt_bundle)
    if forbidden:
        report["forbidden_field_status"] = "failed"
        report["forbidden_field_errors"] = forbidden

    source_ids = [beat["beat_id"] for beat in parsed_input["visual_beats"]]
    items = prompt_bundle.get("prompt_items") if isinstance(prompt_bundle, dict) else None
    if not isinstance(items, list):
        raise PromptComposerError("PROMPT_BUNDLE_STRUCTURE_INVALID", "Prompt Bundle prompt_items must be an array.")
    prompt_ids = [item.get("beat_id") for item in items if isinstance(item, dict) and isinstance(item.get("beat_id"), str)]
    report["missing_prompt_beat_ids"] = [beat_id for beat_id in source_ids if beat_id not in prompt_ids]
    report["duplicate_prompt_beat_ids"] = _duplicates(prompt_ids)
    report["unexpected_prompt_beat_ids"] = [beat_id for beat_id in prompt_ids if beat_id not in source_ids]
    if report["missing_prompt_beat_ids"] or report["duplicate_prompt_beat_ids"] or report["unexpected_prompt_beat_ids"]:
        report["beat_coverage_status"] = "failed"

    expected_order = [beat["beat_order"] for beat in parsed_input["visual_beats"]]
    actual_order = [item.get("beat_order") for item in items if isinstance(item, dict)]
    if actual_order != expected_order:
        report["order_status"] = "failed"
        report["order_errors"].append(_issue("$.prompt_items", "PROMPT_ITEM_ORDER_MISMATCH", "Prompt items must preserve every source beat in exact order."))

    source_by_id = {beat["beat_id"]: beat for beat in parsed_input["visual_beats"]}
    from app.modules.v4.prompt_composer import compose_image_prompt, compose_negative_prompt, compute_prompt_hash, _continuity_reference_requirement
    if prompt_bundle.get("options") != parsed_input["options"]:
        report["generation_options_status"] = "failed"
        report["generation_option_errors"].append(_issue("$.options", "PROMPT_OPTIONS_MISMATCH", "Prompt Bundle options must equal the bounded input options."))
    if prompt_bundle.get("source_beat_count") != len(source_ids) or prompt_bundle.get("prompt_item_count") != len(items):
        report["beat_coverage_status"] = "failed"

    for index, item in enumerate(items):
        if not isinstance(item, dict):
            report["source_identity_status"] = "failed"
            report["prompt_content_errors"].append(_issue(f"$.prompt_items[{index}]", "PROMPT_ITEM_INVALID", "Prompt item must be an object."))
            continue
        source = source_by_id.get(item.get("beat_id"))
        if source:
            for field in ("scene_id", "beat_id", "beat_order", "local_beat_order", "continuity_group_id", "visual_priority"):
                if item.get(field) != source.get(field):
                    report["source_identity_status"] = "failed"
                    report["prompt_content_errors"].append(_issue(f"$.prompt_items[{index}].{field}", "PROMPT_ITEM_SOURCE_IDENTITY_MISMATCH", "Prompt item source identity must be copied exactly."))
            if item.get("target_asset_role") != "beat_visual":
                report["source_identity_status"] = "failed"
                report["prompt_content_errors"].append(_issue(f"$.prompt_items[{index}].target_asset_role", "PROMPT_ITEM_ROLE_INVALID", "Prompt item target role must be beat_visual."))
            if item.get("generation_options") != parsed_input["options"]:
                report["generation_options_status"] = "failed"
                report["generation_option_errors"].append(_issue(f"$.prompt_items[{index}].generation_options", "PROMPT_OPTIONS_MISMATCH", "Prompt item options must equal top-level options."))
            expected_provenance = {
                "representation_family": source["representation_family"],
                "selected_character_refs": source["selected_character_refs"],
                "selected_location_refs": source["selected_location_refs"],
                "selected_prop_refs": source["selected_prop_refs"],
                "selected_recurring_visual_subject_refs": source["selected_recurring_visual_subject_refs"],
                "selected_motif_refs": source["selected_motif_refs"],
                "selected_native_visual_reference_ids": source["selected_native_visual_reference_ids"],
            }
            if item.get("source_provenance") != expected_provenance:
                report["referential_integrity_status"] = "failed"
                report["unknown_reference_errors"].append(_issue(f"$.prompt_items[{index}].source_provenance", "PROMPT_PROVENANCE_MISMATCH", "Prompt provenance must copy source references exactly."))
            if item.get("reference_bindings") != source.get("selected_reference_bindings"):
                report["reference_binding_status"] = "failed"
                report["reference_binding_errors"].append(_issue(f"$.prompt_items[{index}].reference_bindings", "PROMPT_BINDINGS_MISMATCH", "Reference bindings must be copied exactly."))
            previous_source = parsed_input["visual_beats"][index - 1] if index > 0 else None
            try:
                expected_requirement = _continuity_reference_requirement(source, previous_source)
            except Exception as exc:  # noqa: BLE001
                raise PromptComposerError("PROMPT_COMPOSER_INPUT_INVALID", "Prompt continuity requirement could not be resolved safely.") from exc
            if item.get("continuity_reference_requirement") != expected_requirement:
                report["referential_integrity_status"] = "failed"
                report["unknown_reference_errors"].append(_issue(f"$.prompt_items[{index}].continuity_reference_requirement", "PROMPT_CONTINUITY_REQUIREMENT_MISMATCH", "Continuity reference requirement must match source beat continuity."))
            try:
                expected_image = compose_image_prompt(source, parsed_input)
                expected_negative = compose_negative_prompt(source, parsed_input)
            except Exception as exc:  # noqa: BLE001
                raise PromptComposerError("PROMPT_COMPOSER_INPUT_INVALID", "Prompt source could not be recomposed safely.") from exc
            if item.get("image_prompt") != expected_image:
                report["prompt_content_status"] = "failed"
                report["prompt_content_errors"].append(_issue(f"$.prompt_items[{index}].image_prompt", "IMAGE_PROMPT_CANONICAL_MISMATCH", "Image prompt must equal deterministic canonical composition."))
            if item.get("negative_prompt") != expected_negative:
                report["negative_prompt_status"] = "failed"
                report["negative_prompt_errors"].append(_issue(f"$.prompt_items[{index}].negative_prompt", "NEGATIVE_PROMPT_CANONICAL_MISMATCH", "Negative prompt must equal deterministic canonical composition."))
            expected_hash = compute_prompt_hash(
                image_prompt=expected_image,
                negative_prompt=expected_negative,
                generation_options=parsed_input["options"],
                reference_bindings=source.get("selected_reference_bindings") or [],
            )
            if item.get("prompt_hash") != expected_hash:
                report["prompt_hash_status"] = "failed"
                report["prompt_hash_errors"].append(_issue(f"$.prompt_items[{index}].prompt_hash", "PROMPT_HASH_CANONICAL_MISMATCH", "Prompt hash must match deterministic prompt, settings, and reference snapshots."))
        if not isinstance(item.get("image_prompt"), str) or not item["image_prompt"].strip():
            report["prompt_content_status"] = "failed"
            report["prompt_content_errors"].append(_issue(f"$.prompt_items[{index}].image_prompt", "IMAGE_PROMPT_EMPTY", "Image prompt must be non-empty."))
        if not isinstance(item.get("negative_prompt"), str) or not item["negative_prompt"].strip():
            report["negative_prompt_status"] = "failed"
            report["negative_prompt_errors"].append(_issue(f"$.prompt_items[{index}].negative_prompt", "NEGATIVE_PROMPT_EMPTY", "Negative prompt must be non-empty."))

    lineage_pairs = (
        ("project_id", "project_id"),
        ("source_script_revision_id", "source_script_revision_id"),
        ("source_script_analysis_revision_id", "source_script_analysis_revision_id"),
        ("source_style_bible_revision_id", "source_style_bible_revision_id"),
        ("source_scene_plan_revision_id", "source_scene_plan_revision_id"),
        ("source_visual_plan_revision_id", "source_visual_plan_revision_id"),
        ("source_visual_reference_approval_set_artifact_id", "source_visual_reference_approval_set_artifact_id"),
        ("prompt_bundle_revision_id", "prompt_bundle_revision_id"),
    )
    for input_key, bundle_key in lineage_pairs:
        if prompt_bundle.get(bundle_key) != parsed_input.get(input_key):
            report["lineage_status"] = "failed"

    critical = [key for key, value in report.items() if key.endswith("_status") and value == "failed"]
    if critical:
        raise PromptComposerError("PROMPT_BUNDLE_VALIDATION_FAILED", "Prompt Bundle semantic validation failed.", details={"validation_report": report})

    if not allow_missing_backend_qc:
        try:
            parsed_bundle = PromptBundleModel.model_validate(deepcopy(prompt_bundle))
            if parsed_bundle.backend_qc.model_dump(mode="json") != report:
                raise PromptComposerError("PROMPT_BUNDLE_VALIDATION_FAILED", "Prompt Bundle backend_qc does not match deterministic validation.")
        except ValidationError as exc:
            raise PromptComposerError("PROMPT_BUNDLE_VALIDATION_FAILED", "Prompt Bundle contract is invalid.") from exc
    return report

def validate_prompt_bundle(
    *,
    prompt_composer_input: dict[str, Any],
    prompt_bundle: dict[str, Any],
    allow_missing_backend_qc: bool = False,
) -> dict[str, Any]:
    try:
        return _validate_prompt_bundle(
            prompt_composer_input=prompt_composer_input,
            prompt_bundle=prompt_bundle,
            allow_missing_backend_qc=allow_missing_backend_qc,
        )
    except PromptComposerError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PromptComposerError(
            "PROMPT_BUNDLE_STRUCTURE_INVALID",
            "Prompt Bundle could not be validated safely.",
        ) from exc
