from __future__ import annotations

from copy import deepcopy

import pytest

from app.api.errors import AppError
from app.core.config import get_settings
from app.db.models import Project
from app.db.session import build_session_factory
from app.modules.v4.prompt_composer_service import PromptComposerService
from app.modules.v4.scene_breakdown_service import SceneBreakdownServiceV4
from app.modules.v4.visual_plan_assembler import (
    NO_BEAT_COLOR_OVERRIDE,
    NO_BEAT_LIGHTING_OVERRIDE,
)
from tests.v4.combined_scene_breakdown_support import (
    generate_combined_pair,
)


def test_combined_projection_preserves_llm_visual_decisions_and_filters_null_markers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = generate_combined_pair(monkeypatch, approve=True)
    visual = result["visual_plan"]
    beats = [beat for scene in visual["scenes"] for beat in scene["beat_plans"]]
    assert beats
    assert all(beat["lighting_direction"] == NO_BEAT_LIGHTING_OVERRIDE for beat in beats)
    assert all(beat["color_emphasis"] == NO_BEAT_COLOR_OVERRIDE for beat in beats)

    factory = build_session_factory(get_settings())
    with factory() as session:
        project = session.get(Project, result["project_id"])
        assert project is not None
        compiled = PromptComposerService(session).get_current_prompt_bundle(result["project_id"])
        assert project.prompt_bundle_artifact_id == compiled["artifact_id"]
    visual_beat_count = sum(len(scene["beat_plans"]) for scene in visual["scenes"])
    artifact = compiled["artifact"]
    assert artifact["revision_operation"] == "automatic_compilation"
    assert artifact["status"] == "approved"
    assert artifact["options"]["aspect_ratio"] == "16:9"
    assert artifact["schema_version"] == "3.0"
    assert artifact["source_beat_count"] == visual_beat_count
    assert artifact["prompt_item_count"] == visual_beat_count
    assert len(artifact["prompt_items"]) == visual_beat_count
    assert {item["beat_id"] for item in artifact["prompt_items"]} == {
        beat["beat_id"] for beat in beats
    }
    prompts = "\n".join(item["image_prompt"] for item in artifact["prompt_items"])
    assert NO_BEAT_LIGHTING_OVERRIDE not in prompts
    assert NO_BEAT_COLOR_OVERRIDE not in prompts
    assert "None" not in prompts


def test_visual_plan_is_exact_projection_of_current_scene_plan_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = generate_combined_pair(monkeypatch)
    scene = result["scene_plan"]
    visual = result["visual_plan"]
    assert visual["source_scene_plan_revision_id"] == scene["scene_plan_revision_id"]
    scene_beats = [beat["beat_id"] for item in scene["scenes"] for beat in item["beats"]]
    visual_beats = [beat["beat_id"] for item in visual["scenes"] for beat in item["beat_plans"]]
    assert visual_beats == scene_beats


def test_read_rejects_mismatched_pair_even_when_each_artifact_is_schema_valid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = generate_combined_pair(monkeypatch)
    second = generate_combined_pair(monkeypatch)
    factory = build_session_factory(get_settings())
    with factory() as session:
        project = session.get(Project, first["project_id"])
        assert project is not None
        project.visual_plan_artifact_id = second["visual_plan_artifact_id"]
        session.commit()
    with factory() as session:
        with pytest.raises(AppError) as exc:
            SceneBreakdownServiceV4(session).get_current_scene_plan(first["project_id"])
    assert exc.value.error_code in {"SCENE_VISUAL_PLAN_PAIR_INVALID", "VISUAL_PLAN_INVALID"}


def test_visual_plan_continuity_projection_is_deterministic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = generate_combined_pair(monkeypatch)
    for scene in result["visual_plan"]["scenes"]:
        assert scene["scene_continuity_notes"] == "Beat continuity is represented by continuity_with_previous."
        for beat in scene["beat_plans"]:
            assert beat["motion_guidance"] == f"Camera movement: {beat['camera_movement']}."
            assert beat["continuity_notes"] == f"Continuity relation: {beat['continuity_with_previous']}."
            assert "approved lighting" not in beat["lighting_direction"].lower()
            assert "approved palette" not in beat["color_emphasis"].lower()
