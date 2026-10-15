from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.api.scene_asset_manifest_v2_schemas import SceneAssetPublishedManifestV2


def _treatment() -> dict:
    return {
        "visual_concept": "one visual state",
        "subject_action": "subject acts",
        "environment_emphasis": "environment",
        "framing": "wide",
        "camera_angle": "eye_level",
        "camera_movement": "static",
        "composition": "centered",
        "lighting_direction": "side light",
        "color_emphasis": "earth tones",
        "motion_guidance": "still",
        "continuity_with_previous": "new_setup",
        "continuity_notes": "none",
        "production_notes": "none",
        "negative_requirements": [],
        "visible_text": [],
    }


def _generation_lineage() -> dict:
    return {
        "scene_asset_operation_id": "scene_asset_operation_abcdef123456",
        "scene_plan_artifact_id": "artifact_111111111111",
        "visual_plan_artifact_id": "artifact_222222222222",
        "prompt_bundle_artifact_id": "artifact_333333333333",
        "prompt_item_id": "prompt_001",
        "prompt_hash": "sha256:" + "a" * 64,
        "visual_reference_approval_set_artifact_id": None,
        "reference_binding_snapshot_hash": "sha256:" + "b" * 64,
        "generation_settings_snapshot_hash": "sha256:" + "c" * 64,
    }


def _entry(*, source_kind: str = "generated") -> dict:
    return {
        "scene_id": "scene_001",
        "beat_id": "beat_001",
        "beat_order": 1,
        "local_beat_order": 1,
        "continuity_group_id": "continuity_001",
        "computed_duration_seconds": 3.5,
        "resolution_status": "approved",
        "review_status": "approved",
        "selected_candidate_id": "scene_asset_candidate_abcdef123456",
        "selected_asset_artifact_id": "artifact_444444444444",
        "candidate_asset_artifact_ids": ["artifact_444444444444"],
        "source_kind": source_kind,
        "source_prompt_bundle_artifact_id": "artifact_333333333333",
        "source_prompt_item_id": "prompt_001",
        "source_prompt_hash": "sha256:" + "a" * 64,
        "media_task_ids": ["media_task_abcdef123456"] if source_kind == "generated" else [],
        "width": 1280,
        "height": 720,
        "mime_type": "image/png",
        "sha256": "d" * 64,
        "generation_lineage": _generation_lineage() if source_kind == "generated" else None,
        "continuity_source": None,
        "treatment": _treatment(),
        "reference_bindings": [],
        "error": None,
    }


def _manifest(*, source_kind: str = "generated") -> dict:
    return {
        "artifact_type": "scene_asset_manifest",
        "schema_version": "4.0",
        "project_id": "project_123456789abc",
        "scene_asset_manifest_revision_id": "artifact_555555555555",
        "parent_scene_asset_manifest_revision_id": None,
        "revision_operation": "approval",
        "source_script_revision_id": "artifact_666666666666",
        "source_script_analysis_revision_id": "artifact_777777777777",
        "source_style_bible_revision_id": "artifact_888888888888",
        "source_scene_plan_revision_id": "artifact_111111111111",
        "source_visual_plan_revision_id": "artifact_222222222222",
        "source_prompt_bundle_revision_id": "artifact_333333333333",
        "source_visual_reference_approval_set_artifact_id": None,
        "source_operation_id": None,
        "status": "approved",
        "scene_count": 1,
        "beat_count": 1,
        "approved_asset_count": 1,
        "failed_beat_count": 0,
        "scenes": [{"scene_id": "scene_001", "scene_order": 1, "entries": [_entry(source_kind=source_kind)]}],
        "backend_qc": {
            "coverage_status": "passed",
            "ordering_status": "passed",
            "prompt_bundle_parity_status": "passed",
            "asset_integrity_status": "passed",
            "lineage_status": "passed",
            "approval_eligibility_status": "passed",
            "issues": [],
            "warnings": [],
        },
    }


def test_generated_published_candidate_requires_generation_lineage() -> None:
    payload = _manifest(source_kind="generated")
    payload["scenes"][0]["entries"][0]["generation_lineage"] = None
    with pytest.raises(ValidationError):
        SceneAssetPublishedManifestV2.model_validate(payload)


def test_uploaded_published_candidate_must_not_fake_generation_lineage() -> None:
    payload = _manifest(source_kind="uploaded")
    payload["scenes"][0]["entries"][0]["generation_lineage"] = _generation_lineage()
    with pytest.raises(ValidationError):
        SceneAssetPublishedManifestV2.model_validate(payload)


def test_uploaded_published_candidate_is_valid_without_provider_lineage() -> None:
    value = SceneAssetPublishedManifestV2.model_validate(_manifest(source_kind="uploaded"))
    entry = value.scenes[0].entries[0]
    assert entry.source_kind == "uploaded"
    assert entry.generation_lineage is None
    assert entry.media_task_ids == []


def test_approval_snapshot_requires_exact_selected_candidate_membership() -> None:
    payload = deepcopy(_manifest())
    payload["scenes"][0]["entries"][0]["candidate_asset_artifact_ids"] = ["artifact_999999999999"]
    with pytest.raises(ValidationError):
        SceneAssetPublishedManifestV2.model_validate(payload)


def test_manifest_is_approval_only() -> None:
    payload = _manifest()
    payload["status"] = "partial"
    with pytest.raises(ValidationError):
        SceneAssetPublishedManifestV2.model_validate(payload)
