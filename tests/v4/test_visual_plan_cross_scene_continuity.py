from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.api.visual_plan_schemas_v4 import VisualPlanModel


def _beat(
    *,
    scene_id: str,
    beat_id: str,
    beat_order: int,
    relation: str,
    group: str,
    previous_image_required: bool | None = None,
) -> dict:
    if previous_image_required is None:
        previous_image_required = relation == "match_composition"
    return {
        "beat_id": beat_id,
        "visual_context_ref": "ctx_001",
        "representation_family": "character_environment_lived",
        "selected_character_refs": [],
        "selected_location_refs": [],
        "selected_prop_refs": [],
        "selected_recurring_visual_subject_refs": [],
        "selected_motif_refs": [],
        "selected_native_visual_reference_ids": [],
        "visual_concept": "Visible state",
        "subject_action": "Subject state",
        "environment_emphasis": "Environment state",
        "framing": "medium",
        "camera_angle": "eye_level",
        "camera_movement": "static",
        "composition": "Subject centered in the current environment.",
        "lighting_direction": "No beat-specific lighting override.",
        "color_emphasis": "No beat-specific color override.",
        "motion_guidance": "Camera movement: static.",
        "continuity_with_previous": relation,
        "previous_beat_image_reference_required": previous_image_required,
        "continuity_notes": f"Continuity relation: {relation}.",
        "production_notes": "Keep the approved style.",
        "negative_requirements": [],
        "visible_text": [],
        "scene_id": scene_id,
        "beat_order": beat_order,
        "local_beat_order": 1,
        "script_quote": f"Beat {beat_order}",
        "resolved_script_span": [beat_order - 1, beat_order],
        "computed_duration_seconds": 1.0,
        "visual_priority": "normal",
        "is_narrative_marker": False,
        "narrative_marker_refs": [],
        "narrative_marker_types": [],
        "continuity_group_id": group,
        "selected_reference_bindings": [],
    }


def _plan(second_relation: str = "same_setup", second_group: str = "continuity_001") -> dict:
    return {
        "artifact_type": "visual_plan",
        "schema_version": "3.0",
        "project_id": "project_000000000001",
        "visual_plan_revision_id": "artifact_000000000001",
        "parent_visual_plan_revision_id": None,
        "revision_operation": "full_generation",
        "source_script_revision_id": "artifact_000000000002",
        "source_script_analysis_revision_id": "artifact_000000000003",
        "source_style_bible_revision_id": "artifact_000000000004",
        "source_scene_plan_revision_id": "artifact_000000000005",
        "source_visual_reference_approval_set_artifact_id": None,
        "source_run_artifact_id": "artifact_000000000006",
        "status": "draft_ready",
        "scene_count": 2,
        "beat_count": 2,
        "scenes": [
            {
                "scene_id": "scene_001",
                "scene_order": 1,
                "section_id": "sec_001",
                "scene_continuity_notes": "Global Beat continuity.",
                "beat_plans": [
                    _beat(
                        scene_id="scene_001",
                        beat_id="beat_001",
                        beat_order=1,
                        relation="new_setup",
                        group="continuity_001",
                    )
                ],
            },
            {
                "scene_id": "scene_002",
                "scene_order": 2,
                "section_id": "sec_001",
                "scene_continuity_notes": "Global Beat continuity.",
                "beat_plans": [
                    _beat(
                        scene_id="scene_002",
                        beat_id="beat_002",
                        beat_order=2,
                        relation=second_relation,
                        group=second_group,
                    )
                ],
            },
        ],
        "backend_qc": {
            "scene_coverage_status": "passed",
            "beat_coverage_status": "passed",
            "order_status": "passed",
            "source_identity_status": "passed",
            "referential_integrity_status": "passed",
            "treatment_validity_status": "passed",
            "continuity_status": "passed",
            "forbidden_field_status": "passed",
            "reasoning_leakage_status": "passed",
            "missing_scene_ids": [],
            "duplicate_scene_ids": [],
            "missing_beat_ids": [],
            "duplicate_beat_ids": [],
            "reordered_beat_ids": [],
            "unknown_ref_errors": [],
            "invalid_treatment_errors": [],
            "continuity_warnings": [],
            "forbidden_field_errors": [],
            "reasoning_leakage_errors": [],
            "representation_family_qc": {
                "total_beat_count": 2,
                "counts": {
                    "character_environment_lived": 2,
                    "object_evidence_closeup": 0,
                    "text_diagram_editorial": 0,
                },
                "percentages": {
                    "character_environment_lived": 100.0,
                    "object_evidence_closeup": 0.0,
                    "text_diagram_editorial": 0.0,
                },
                "complete_visual_plan": True,
            },
            "warnings": [],
        },
    }


def test_visual_plan_continuity_group_may_span_scene_boundary() -> None:
    parsed = VisualPlanModel.model_validate(_plan())
    assert parsed.scenes[0].beat_plans[0].continuity_group_id == "continuity_001"
    assert parsed.scenes[1].beat_plans[0].continuity_group_id == "continuity_001"


def test_visual_plan_new_setup_starts_new_group_across_scene_boundary() -> None:
    parsed = VisualPlanModel.model_validate(_plan("new_setup", "continuity_002"))
    assert parsed.scenes[1].beat_plans[0].continuity_group_id == "continuity_002"


def test_visual_plan_rejects_first_global_beat_without_new_setup() -> None:
    payload = _plan()
    payload["scenes"][0]["beat_plans"][0]["continuity_with_previous"] = "same_setup"
    with pytest.raises(ValidationError):
        VisualPlanModel.model_validate(payload)


def test_visual_plan_rejects_group_change_without_new_setup() -> None:
    with pytest.raises(ValidationError):
        VisualPlanModel.model_validate(_plan("same_setup", "continuity_002"))


@pytest.mark.parametrize(
    ("relation", "required"),
    [
        ("new_setup", True),
        ("match_composition", False),
    ],
)
def test_visual_plan_rejects_invalid_previous_image_dependency_matrix(relation: str, required: bool) -> None:
    payload = _plan(relation, "continuity_002" if relation == "new_setup" else "continuity_001")
    payload["scenes"][1]["beat_plans"][0]["previous_beat_image_reference_required"] = required
    with pytest.raises(ValidationError):
        VisualPlanModel.model_validate(payload)


@pytest.mark.parametrize(
    ("relation", "required"),
    [
        ("new_setup", False),
        ("same_setup", False),
        ("same_setup", True),
        ("match_subject", False),
        ("match_subject", True),
        ("match_location", False),
        ("match_location", True),
        ("graphic_sequence", False),
        ("graphic_sequence", True),
        ("match_composition", True),
    ],
)
def test_visual_plan_accepts_valid_previous_image_dependency_matrix(relation: str, required: bool) -> None:
    payload = _plan(relation, "continuity_002" if relation == "new_setup" else "continuity_001")
    payload["scenes"][1]["beat_plans"][0]["previous_beat_image_reference_required"] = required
    parsed = VisualPlanModel.model_validate(payload)
    assert parsed.scenes[1].beat_plans[0].previous_beat_image_reference_required is required


def test_visual_plan_requires_previous_image_dependency_field() -> None:
    payload = _plan()
    del payload["scenes"][1]["beat_plans"][0]["previous_beat_image_reference_required"]
    with pytest.raises(ValidationError):
        VisualPlanModel.model_validate(payload)
