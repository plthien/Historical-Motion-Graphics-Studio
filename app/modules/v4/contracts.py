from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from app.api.errors import AppError
from app.core.config import get_settings


class V4ContractValidator:
    """Strict runtime validator for contracts/schemas/v4."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or get_settings().root_dir
        self.schemas_dir = self.root / "contracts" / "schemas" / "v4"
        self._schema_cache: dict[str, dict[str, Any]] = {}
        self._registry_cache: Registry | None = None
        self._validator_cache: dict[str, Draft202012Validator] = {}

    def load_schema(self, name: str) -> dict[str, Any]:
        cached = self._schema_cache.get(name)
        if cached is not None:
            return cached
        path = self.schemas_dir / name
        if not path.is_file():
            raise AppError("V4_SCHEMA_MISSING", f"V4 schema '{name}' was not found.", 500, retryable=False)
        schema = json.loads(path.read_text(encoding="utf-8"))
        self._schema_cache[name] = schema
        return schema

    def _registry(self) -> Registry:
        if self._registry_cache is not None:
            return self._registry_cache
        registry = Registry()
        for path in sorted(self.schemas_dir.glob("*.schema.json")):
            schema = json.loads(path.read_text(encoding="utf-8"))
            schema_id = schema.get("$id")
            if isinstance(schema_id, str) and schema_id:
                registry = registry.with_resource(schema_id, Resource.from_contents(schema))
        self._registry_cache = registry
        return registry

    def validate(self, name: str, payload: dict[str, Any], *, error_code: str) -> dict[str, Any]:
        validator = self._validator_cache.get(name)
        if validator is None:
            validator = Draft202012Validator(self.load_schema(name), registry=self._registry())
            self._validator_cache[name] = validator
        issues = sorted(validator.iter_errors(payload), key=lambda issue: list(issue.absolute_path))
        if issues:
            raise AppError(
                error_code,
                f"Payload failed V4 schema validation against {name}.",
                422,
                retryable=False,
                details={"errors": [self._issue_to_dict(issue) for issue in issues]},
            )
        return payload


    def validate_schema(
        self,
        name: str,
        schema: dict[str, Any],
        payload: dict[str, Any],
        *,
        error_code: str,
    ) -> dict[str, Any]:
        validator = Draft202012Validator(schema, registry=self._registry())
        issues = sorted(validator.iter_errors(payload), key=lambda issue: list(issue.absolute_path))
        if issues:
            raise AppError(
                error_code,
                f"Payload failed V4 schema validation against {name}.",
                422,
                retryable=False,
                details={"errors": [self._issue_to_dict(issue) for issue in issues]},
            )
        return payload

    @staticmethod
    def _issue_to_dict(issue: Any) -> dict[str, Any]:
        path = "$" + "".join(f"[{part}]" if isinstance(part, int) else f".{part}" for part in issue.absolute_path)
        if issue.validator == "additionalProperties":
            match = re.search(r"'([^']+)' was unexpected", issue.message)
            if match:
                path = f"{path}.{match.group(1)}" if path != "$" else f"$.{match.group(1)}"
        return {"path": path, "message": issue.message}
