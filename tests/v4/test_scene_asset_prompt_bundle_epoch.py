from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.api.errors import AppError
from app.db import models as db_models  # noqa: F401
from app.db import scene_asset_working_models as working_models  # noqa: F401
from app.db.base import Base
from app.db.enums import ArtifactStatus, ArtifactType
from app.db.models import Artifact, Channel, Project
from app.db.scene_asset_beat_state import SceneAssetBeatState
from app.db.scene_asset_candidate import SceneAssetCandidate
from app.db.scene_asset_prompt_revision import SceneAssetPromptRevision
from app.db.scene_asset_working_operation import SceneAssetWorkingOperation
from app.db.session import create_sqlalchemy_engine
from app.modules.v4.scene_asset_prompt_revision import SceneAssetPromptRevisionService
from app.modules.v4.scene_asset_workspace_v2 import SceneAssetWorkspaceV2Service
from app.modules.v4.scene_asset_workspace_v2_runtime import (
    SceneAssetWorkspaceV2Service as RuntimeSceneAssetWorkspaceV2Service,
)


NOW = "2026-09-28T01:00:00+00:00"
PROJECT_ID = "project_epoch"
OLD_BUNDLE = "artifact_prompt_old"


def _factory(tmp_path: Path) -> sessionmaker[Session]:
    engine = create_sqlalchemy_engine(f"sqlite:///{(tmp_path / 'scene_asset_epoch.sqlite3').resolve()}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def _artifact(artifact_id: str, *, image: bool = False) -> Artifact:
    return Artifact(
        artifact_id=artifact_id,
        logical_id=f"project/{PROJECT_ID}/scene_assets/{artifact_id}" if image else f"logical/{artifact_id}",
        version_no=1,
        is_current=True,
        artifact_type=ArtifactType.IMAGE if image else ArtifactType.JSON,
        artifact_role=None,
        path=f"tests/{artifact_id}.{'png' if image else 'json'}",
        content_hash=f"hash_{artifact_id}",
        sha256=(f"sha_{artifact_id}" + "0" * 64)[:64] if image else f"sha_{artifact_id}",
        mime_type="image/png" if image else "application/json",
        size_bytes=1,
        width=1280 if image else None,
        height=720 if image else None,
        status=ArtifactStatus.READY,
        created_at=NOW,
    )


def _item(beat_id: str, *, scene_id: str, prompt_item_id: str | None = None, prompt_hash: str | None = None) -> dict:
    return {
        "beat_id": beat_id,
        "scene_id": scene_id,
        "prompt_item_id": prompt_item_id or f"prompt_{beat_id}",
        "prompt_hash": prompt_hash or f"hash_{beat_id}",
        "beat_order": 1,
        "local_beat_order": 1,
        "image_prompt": f"prompt {beat_id}",
        "negative_prompt": "",
    }


def _bundle(bundle_id: str, items: list[dict], *, operation: str = "automatic_compilation", target: str | None = None) -> dict:
    return {
        "status": "approved",
        "prompt_bundle_revision_id": bundle_id,
        "revision_operation": operation,
        "operation_target_beat_id": target,
        "prompt_items": items,
    }


def _visual_beat(beat_id: str) -> dict:
    return {
        "beat_id": beat_id,
        "computed_duration_seconds": 2,
        **{
            key: "value"
            for key in (
                "visual_concept",
                "subject_action",
                "environment_emphasis",
                "framing",
                "camera_angle",
                "camera_movement",
                "composition",
                "lighting_direction",
                "color_emphasis",
                "motion_guidance",
                "continuity_with_previous",
                "continuity_notes",
                "production_notes",
            )
        },
        "negative_requirements": [],
        "visible_text": [],
    }


def _seed(session: Session, *, beat_ids: tuple[str, ...] = ("beat_001", "beat_002")) -> None:
    artifact_ids = {
        "artifact_script",
        "artifact_analysis",
        "artifact_style",
        "artifact_scene",
        "artifact_visual",
        OLD_BUNDLE,
        "artifact_prompt_new",
        "artifact_prompt_newer",
        *{f"artifact_image_old_{beat_id}" for beat_id in beat_ids},
    }
    session.add(Channel(
        channel_id="channel_epoch",
        name="Epoch",
        language="en",
        niche="test",
        audience="test",
        channel_dna_json={},
        channel_context_json={},
        created_at=NOW,
        updated_at=NOW,
    ))
    session.add_all([_artifact(artifact_id, image=artifact_id.startswith("artifact_image_old_")) for artifact_id in artifact_ids])
    session.flush()
    session.add(Project(
        project_id=PROJECT_ID,
        channel_id="channel_epoch",
        name="Epoch",
        description=None,
        channel_context_json={},
        channel_dna_json={},
        script_artifact_id="artifact_script",
        script_analysis_artifact_id="artifact_analysis",
        style_bible_artifact_id="artifact_style",
        scene_plan_artifact_id="artifact_scene",
        visual_plan_artifact_id="artifact_visual",
        prompt_bundle_artifact_id=OLD_BUNDLE,
        created_at=NOW,
        updated_at=NOW,
    ))
    session.flush()
    for beat_id in beat_ids:
        session.add(SceneAssetPromptRevision(
            prompt_revision_id=f"revision_old_{beat_id}",
            project_id=PROJECT_ID,
            scene_id=f"scene_old_{beat_id}",
            beat_id=beat_id,
            revision_no=1,
            revision_kind="compiled",
            is_current=True,
            source_prompt_bundle_artifact_id=OLD_BUNDLE,
            source_prompt_item_id=f"prompt_{beat_id}",
            source_prompt_hash=f"hash_{beat_id}",
            image_prompt=f"old prompt {beat_id}",
            negative_prompt="",
            created_at=NOW,
        ))
    session.flush()
    for beat_id in beat_ids:
        session.add(SceneAssetCandidate(
            candidate_id=f"candidate_old_{beat_id}",
            project_id=PROJECT_ID,
            scene_id=f"scene_old_{beat_id}",
            beat_id=beat_id,
            artifact_id=f"artifact_image_old_{beat_id}",
            source_kind="uploaded",
            operation_beat_id=None,
            operation_task_id=None,
            source_prompt_revision_id=f"revision_old_{beat_id}",
            source_prompt_bundle_artifact_id=OLD_BUNDLE,
            source_prompt_item_id=f"prompt_{beat_id}",
            source_prompt_hash=f"hash_{beat_id}",
            continuity_snapshot_json=None,
            created_at=NOW,
        ))
    session.flush()
    for beat_id in beat_ids:
        session.add(SceneAssetBeatState(
            project_id=PROJECT_ID,
            beat_id=beat_id,
            scene_id=f"scene_old_{beat_id}",
            version=4,
            resolution_status="approved",
            review_status="approved",
            current_draft_candidate_id=f"candidate_old_{beat_id}",
            selected_candidate_id=f"candidate_old_{beat_id}",
            active_operation_beat_id=None,
            continuity_source_scene_id=None,
            continuity_source_beat_id=None,
            continuity_source_candidate_id=None,
            last_error_json={"error_code": "OLD_ERROR"},
            last_progress_at="old-progress",
            created_at=NOW,
            updated_at=NOW,
        ))
    session.commit()


def _publish(session: Session, items: list[dict], *, bundle_id: str = "artifact_prompt_new", operation: str = "automatic_compilation", target: str | None = None) -> None:
    project = session.get(Project, PROJECT_ID)
    assert project is not None
    project.prompt_bundle_artifact_id = bundle_id
    session.flush()
    SceneAssetPromptRevisionService(session).publish_approved_bundle(
        PROJECT_ID,
        _bundle(bundle_id, items, operation=operation, target=target),
    )
    session.commit()


def test_full_prompt_bundle_resets_state_and_updates_scene_identity(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    with factory() as session:
        _seed(session)
        _publish(session, [_item("beat_001", scene_id="scene_new_beat_001")])

        state = session.get(SceneAssetBeatState, (PROJECT_ID, "beat_001"))
        assert state is not None
        assert state.scene_id == "scene_new_beat_001"
        assert state.version == 5
        assert state.resolution_status == "not_generated"
        assert state.review_status == "unreviewed"
        assert state.current_draft_candidate_id is None
        assert state.selected_candidate_id is None
        assert state.active_operation_beat_id is None
        assert state.continuity_source_candidate_id is None
        assert state.last_error_json is None
        assert state.last_progress_at is None


def test_historical_candidate_is_hidden_and_does_not_block_generation(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    with factory() as session:
        _seed(session, beat_ids=("beat_001",))
        _publish(session, [_item("beat_001", scene_id="scene_new")])
        project = session.get(Project, PROJECT_ID)
        assert project is not None
        prompt_item = _item("beat_001", scene_id="scene_new")
        visual_beat = _visual_beat("beat_001")
        service = SceneAssetWorkspaceV2Service(session)
        projected = service._project_beat(project, prompt_item, visual_beat, None, session.get(SceneAssetBeatState, (PROJECT_ID, "beat_001")), False)
        actions = service._workspace_actions_payload(
            project,
            {"scenes": [{"beat_plans": [visual_beat]}]},
            {"prompt_items": [prompt_item]},
        )

        assert session.get(SceneAssetCandidate, "candidate_old_beat_001") is not None
        assert projected["candidates"] == []
        assert projected["can_generate"] is True
        assert projected["can_regenerate"] is False
        assert projected["can_review"] is False
        assert projected["can_approve"] is False
        assert actions["can_generate_all"] is True


def test_active_generation_blocks_full_epoch_without_resetting_state(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    with factory() as session:
        _seed(session, beat_ids=("beat_001",))
        session.add(SceneAssetWorkingOperation(
            operation_id="operation_active",
            project_id=PROJECT_ID,
            operation_type="generate_beat",
            status="running",
            source_script_artifact_id="artifact_script",
            source_script_analysis_artifact_id="artifact_analysis",
            source_style_bible_artifact_id="artifact_style",
            source_scene_plan_artifact_id="artifact_scene",
            source_visual_plan_artifact_id="artifact_visual",
            source_prompt_bundle_artifact_id=OLD_BUNDLE,
            source_reference_approval_set_artifact_id=None,
            provider_settings_snapshot_json={},
            provider_settings_snapshot_hash="provider",
            total_beat_count=1,
            completed_count=0,
            failed_count=0,
            cancelled_count=0,
            next_reconcile_at=NOW,
            last_progress_at=NOW,
            reconcile_owner=None,
            reconcile_token=None,
            reconcile_lease_expires_at=None,
            cancel_requested_at=None,
            cancelled_at=None,
            request_json={},
            result_json={},
            error_code=None,
            error_message=None,
            error_details_json=None,
            created_at=NOW,
            updated_at=NOW,
            completed_at=None,
        ))
        session.commit()

        with pytest.raises(AppError) as exc:
            _publish(session, [_item("beat_001", scene_id="scene_new")])

        assert exc.value.error_code == "SCENE_ASSET_GENERATION_ACTIVE"
        session.rollback()
        state = session.get(SceneAssetBeatState, (PROJECT_ID, "beat_001"))
        assert state is not None
        assert state.resolution_status == "approved"
        assert session.query(SceneAssetPromptRevision).filter_by(project_id=PROJECT_ID, beat_id="beat_001").count() == 1


@pytest.mark.parametrize("operation", ["manual_edit", "beat_recompilation"])
def test_beat_local_prompt_publication_does_not_reset_unrelated_state(tmp_path: Path, operation: str) -> None:
    factory = _factory(tmp_path)
    with factory() as session:
        _seed(session)
        beat_two_before = session.get(SceneAssetBeatState, (PROJECT_ID, "beat_002"))
        assert beat_two_before is not None
        _publish(
            session,
            [_item("beat_001", scene_id="scene_local")],
            operation=operation,
            target="beat_001",
        )

        beat_one = session.get(SceneAssetBeatState, (PROJECT_ID, "beat_001"))
        beat_two = session.get(SceneAssetBeatState, (PROJECT_ID, "beat_002"))
        assert beat_one is not None and beat_two is not None
        assert beat_one.resolution_status == "approved"
        assert beat_one.selected_candidate_id == "candidate_old_beat_001"
        assert beat_two.version == beat_two_before.version
        assert beat_two.selected_candidate_id == beat_two_before.selected_candidate_id


@pytest.mark.parametrize("operation", ["manual_edit", "beat_recompilation"])
def test_runtime_projection_preserves_sibling_candidate_by_beat_prompt_revision(
    tmp_path: Path,
    operation: str,
) -> None:
    factory = _factory(tmp_path)
    with factory() as session:
        _seed(session)
        _publish(
            session,
            [_item("beat_001", scene_id="scene_local", prompt_hash="hash_local")],
            operation=operation,
            target="beat_001",
        )
        project = session.get(Project, PROJECT_ID)
        sibling_state = session.get(SceneAssetBeatState, (PROJECT_ID, "beat_002"))
        sibling_candidate = session.get(SceneAssetCandidate, "candidate_old_beat_002")
        sibling_artifact = session.get(Artifact, "artifact_image_old_beat_002")
        assert project is not None
        assert sibling_state is not None
        assert sibling_candidate is not None
        assert sibling_artifact is not None
        assert project.prompt_bundle_artifact_id == "artifact_prompt_new"
        assert sibling_candidate.source_prompt_bundle_artifact_id == OLD_BUNDLE

        service = RuntimeSceneAssetWorkspaceV2Service(session)
        service._projection_candidates_by_beat = {"beat_002": [sibling_candidate]}
        service._projection_artifacts_by_id = {sibling_artifact.artifact_id: sibling_artifact}
        sibling_prompt_item = _item("beat_002", scene_id="scene_old_beat_002")
        sibling_prompt_item["image_prompt"] = "old prompt beat_002"
        projected = service._project_beat(
            project,
            sibling_prompt_item,
            _visual_beat("beat_002"),
            None,
            sibling_state,
            False,
        )

        assert [candidate["candidate_id"] for candidate in projected["candidates"]] == [
            "candidate_old_beat_002"
        ]
        assert projected["current_draft_candidate_id"] == "candidate_old_beat_002"
        assert projected["selected_candidate_id"] == "candidate_old_beat_002"
        assert projected["can_generate"] is False
        assert projected["can_regenerate"] is False


def test_removed_beat_is_reset_and_reintroduced_beat_starts_clean(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    with factory() as session:
        _seed(session)
        _publish(session, [_item("beat_001", scene_id="scene_new_beat_001")])

        removed = session.get(SceneAssetBeatState, (PROJECT_ID, "beat_002"))
        assert removed is not None
        assert removed.resolution_status == "not_generated"
        assert removed.selected_candidate_id is None
        assert removed.version == 5

        _publish(
            session,
            [
                _item("beat_001", scene_id="scene_newer_beat_001"),
                _item("beat_002", scene_id="scene_reintroduced_beat_002"),
            ],
            bundle_id="artifact_prompt_newer",
        )
        reintroduced = session.get(SceneAssetBeatState, (PROJECT_ID, "beat_002"))
        assert reintroduced is not None
        assert reintroduced.scene_id == "scene_reintroduced_beat_002"
        assert reintroduced.resolution_status == "not_generated"
        assert reintroduced.review_status == "unreviewed"
        assert reintroduced.selected_candidate_id is None
        assert reintroduced.current_draft_candidate_id is None
