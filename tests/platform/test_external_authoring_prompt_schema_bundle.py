from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.core.external_authoring import bundle_prompt_schema, render_external_prompt


ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "contracts" / "schemas"


def _walk(value: Any):
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _local_refs(schema: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for value in _walk(schema):
        if isinstance(value, dict) and isinstance(value.get("$ref"), str):
            refs.append(value["$ref"])
    return refs


def _resolve_local_pointer(schema: dict[str, Any], ref: str) -> Any:
    assert ref.startswith("#")
    fragment = ref[1:]
    if not fragment:
        return schema
    assert fragment.startswith("/")
    current: Any = schema
    for raw_part in fragment[1:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        assert isinstance(current, dict), f"Reference walks through non-object: {ref}"
        assert part in current, f"Unresolved local schema reference: {ref}"
        current = current[part]
    return current


@pytest.mark.parametrize(
    ("relative_path", "required_tokens"),
    [
        (
            "platform/topic_ideas_llm_output.schema.json",
            ("raw_topic_idea", "recommended_pacing_mode", "pacing_mode_reason"),
        ),
        (
            "platform/script_and_analysis_external_output_v2.schema.json",
            ("spoken_overrides", "delivery_cues", "continuity_catalog", "narrative_markers"),
        ),
        (
            "v4/scene_breakdown_full_llm_output_v1.schema.json",
                ("beats", "visual_concept", "continuity_with_previous"),
        ),
        (
            "v4/scene_breakdown_scene_llm_output_v1.schema.json",
            ("meaning_now", "visible_state_now", "delta_now", "continuity_with_previous"),
        ),
        (
            "v4/scene_breakdown_visual_llm_output_v2.schema.json",
            ("visual_concept", "subject_action", "environment_emphasis", "composition"),
        ),
    ],
)
def test_prompt_schema_bundle_is_self_contained(relative_path: str, required_tokens: tuple[str, ...]) -> None:
    source = json.loads((SCHEMAS / relative_path).read_text(encoding="utf-8"))
    bundled = bundle_prompt_schema(source)
    serialized = json.dumps(bundled, ensure_ascii=False)

    assert "youtube-ai-studio.local/contracts/schemas" not in serialized
    assert "contracts/schemas/" not in serialized
    assert all(not (isinstance(value, dict) and "$id" in value) for value in _walk(bundled))

    refs = _local_refs(bundled)
    assert all(ref.startswith("#") for ref in refs)
    for ref in refs:
        _resolve_local_pointer(bundled, ref)

    for token in required_tokens:
        assert token in serialized


def test_render_external_prompt_embeds_only_the_bundled_schema() -> None:
    source = json.loads(
        (SCHEMAS / "platform" / "script_and_analysis_external_output_v2.schema.json").read_text(
            encoding="utf-8"
        )
    )
    package = render_external_prompt(
        stage="script_and_analysis",
        prompt_version="test",
        system_prompt="System",
        user_prompt="User",
        output_schema_name="script_and_analysis_external_output_v2.schema.json",
        output_schema=source,
        artifact_ids={"script": "artifact_111111111111", "analysis": "artifact_222222222222"},
        output_filename="script_and_analysis.json",
    )
    prompt = package["prompt_text"]
    schema_text = prompt.split("## Required JSON Schema\n\n```json\n", 1)[1].split("\n```\n\n## Required Delivery", 1)[0]
    embedded = json.loads(schema_text)

    assert "youtube-ai-studio.local/contracts/schemas" not in schema_text
    assert "$id" not in schema_text
    assert "spoken_overrides" in schema_text
    assert "continuity_catalog" in schema_text
    for ref in _local_refs(embedded):
        _resolve_local_pointer(embedded, ref)
