#!/usr/bin/env python3
"""Validate the active single-flow contract tree and OpenAPI authority."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import yaml
from jsonschema.validators import validator_for

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SCHEMA_ROOT = ROOT / "contracts" / "schemas"
OPENAPI_PATH = ROOT / "contracts" / "openapi" / "youtube_ai_studio_api.openapi.yaml"
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


class UniqueKeyLoader(yaml.SafeLoader):
    """YAML loader that rejects duplicate mapping keys."""


def _construct_unique_mapping(loader: UniqueKeyLoader, node: yaml.nodes.MappingNode, deep: bool = False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping", node.start_mark,
                f"found duplicate key {key!r}", key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


def load_yaml(path: Path):
    return yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader)


FORBIDDEN_CREATIVE_PREFIXES = (
    "/api/v3/projects/{project_id}/story-analyzer",
    "/api/v3/projects/{project_id}/style-bible",
    "/api/v3/projects/{project_id}/scene-breakdown",
    "/api/v3/projects/{project_id}/visual-planner",
    "/api/v3/projects/{project_id}/visual-plan",
    "/api/v3/projects/{project_id}/prompt-bundle",
    "/api/v3/projects/{project_id}/scene-assets",
    "/api/v3/projects/{project_id}/voice",
    "/api/v3/projects/{project_id}/assembly",
    "/api/v3/projects/{project_id}/render",
)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"JSON root must be an object: {path}")
    return value


def validate_json_schemas() -> int:
    count = 0
    for path in sorted(SCHEMA_ROOT.rglob("*.schema.json")):
        schema = _load_json(path)
        validator_for(schema).check_schema(schema)
        count += 1
    if not count:
        raise AssertionError("No JSON schemas found")
    if (SCHEMA_ROOT / "v3").exists():
        raise AssertionError("Legacy schema directory still exists")
    return count


def validate_openapi() -> int:
    spec = load_yaml(OPENAPI_PATH)
    paths = spec.get("paths", {})
    operation_ids: set[str] = set()
    count = 0
    for path, methods in paths.items():
        if path.startswith(FORBIDDEN_CREATIVE_PREFIXES):
            raise AssertionError(f"Legacy creative path remains active: {path}")
        for method, operation in (methods or {}).items():
            if method.lower() not in HTTP_METHODS:
                continue
            operation_id = operation.get("operationId")
            if not operation_id:
                raise AssertionError(f"Missing operationId: {method} {path}")
            if operation_id in operation_ids:
                raise AssertionError(f"Duplicate operationId: {operation_id}")
            operation_ids.add(operation_id)
            # Runtime-generated OpenAPI is the active authority and does not emit
            # the former hand-maintained x-implementation-status extension.  If
            # an explicit marker is present, it may not advertise an inactive
            # operation, but absence of the marker is valid.
            implementation_status = operation.get("x-implementation-status")
            if implementation_status is not None and implementation_status != "implemented":
                raise AssertionError(f"Non-implemented active operation: {operation_id}")
            count += 1
    return count


def validate_contract_registries() -> tuple[int, int]:
    from app.modules.platform.contracts import PlatformContractValidator
    from app.modules.v4.contracts import V4ContractValidator

    platform = PlatformContractValidator()
    v4 = V4ContractValidator()
    platform_count = len(list(platform.schemas_dir.glob("*.schema.json")))
    v4_count = len(list(v4.schemas_dir.glob("*.schema.json")))
    if not platform_count or not v4_count:
        raise AssertionError("Contract registry is empty")
    return platform_count, v4_count


def main() -> int:
    schema_count = validate_json_schemas()
    operation_count = validate_openapi()
    platform_count, v4_count = validate_contract_registries()
    if (ROOT / "contracts" / "prompts" / "v3").exists():
        raise AssertionError("Legacy prompt directory still exists")
    print(
        f"validated schemas={schema_count} operations={operation_count} "
        f"platform_registry={platform_count} v4_registry={v4_count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
