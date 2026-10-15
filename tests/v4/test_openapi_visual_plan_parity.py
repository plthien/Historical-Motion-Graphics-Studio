from __future__ import annotations

from pathlib import Path
import json

import yaml

from app.main import create_app
from app.api.visual_plan_schemas_v4 import VisualPlanReadResponse


ROOT = Path(__file__).resolve().parents[2]
STATIC_OPENAPI = ROOT / "contracts" / "openapi" / "youtube_ai_studio_api.openapi.yaml"
STATIC_READ_SCHEMA = ROOT / "contracts" / "schemas" / "v4" / "visual_plan_read_response_v1.schema.json"


OLD_VISUAL_PLAN_FIELDS = {
    "effective_character_refs",
    "effective_location_ref",
    "prop_refs",
    "recurring_visual_subject_refs",
    "motif_refs",
    "reference_bindings",
}


def _schemas() -> tuple[dict, dict]:
    runtime = create_app().openapi()["components"]["schemas"]
    static = yaml.safe_load(STATIC_OPENAPI.read_text(encoding="utf-8"))["components"]["schemas"]
    return runtime, static


def test_static_openapi_visual_plan_beat_matches_runtime_selected_refs() -> None:
    runtime, static = _schemas()
    runtime_beat = runtime["VisualPlanBeat"]
    static_beat = static["VisualPlanBeat"]

    assert set(static_beat["properties"]) == set(runtime_beat["properties"])
    assert set(static_beat["required"]) == set(runtime_beat["required"])
    assert OLD_VISUAL_PLAN_FIELDS.isdisjoint(static_beat["properties"])
    assert OLD_VISUAL_PLAN_FIELDS.isdisjoint(static_beat["required"])

    for field in (
        "selected_character_refs",
        "selected_prop_refs",
        "selected_recurring_visual_subject_refs",
        "selected_motif_refs",
        "selected_native_visual_reference_ids",
        "selected_reference_bindings",
    ):
        assert static_beat["properties"][field]["type"] == "array"
        assert static_beat["properties"][field] == runtime_beat["properties"][field]

    assert static_beat["properties"]["selected_location_refs"] == runtime_beat["properties"]["selected_location_refs"]
    assert static_beat["properties"]["representation_family"] == runtime_beat["properties"]["representation_family"]

    binding_items = static_beat["properties"]["selected_reference_bindings"]["items"]
    assert binding_items["discriminator"]["propertyName"] == "anchor_type"
    assert binding_items == runtime_beat["properties"]["selected_reference_bindings"]["items"]


def test_static_openapi_prompt_source_provenance_matches_runtime_selected_refs() -> None:
    runtime, static = _schemas()
    runtime_provenance = runtime["PromptSourceProvenance"]
    static_provenance = static["PromptSourceProvenance"]

    assert set(static_provenance["properties"]) == set(runtime_provenance["properties"])
    assert set(static_provenance["required"]) == set(runtime_provenance["required"])
    assert OLD_VISUAL_PLAN_FIELDS.isdisjoint(static_provenance["properties"])
    for field, shape in runtime_provenance["properties"].items():
        assert static_provenance["properties"][field] == shape


def test_static_visual_plan_read_schema_matches_runtime_model() -> None:
    static = json.loads(STATIC_READ_SCHEMA.read_text(encoding="utf-8"))
    runtime = VisualPlanReadResponse.model_json_schema()

    assert static == runtime
    beat = static["$defs"]["VisualPlanBeat"]
    assert "visual_context_ref" in beat["properties"]
    assert "visual_context_ref" in beat["required"]
