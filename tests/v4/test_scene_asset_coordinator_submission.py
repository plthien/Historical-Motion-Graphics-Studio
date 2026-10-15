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
from app.db.scene_asset_operation_beat import SceneAssetOperationBeat
from app.db.scene_asset_operation_task import SceneAssetOperationTask
from app.db.scene_asset_prompt_revision import SceneAssetPromptRevision
from app.db.scene_asset_working_operation import SceneAssetWorkingOperation
from app.db.session import create_sqlalchemy_engine
from app.modules.v4.scene_asset_coordinator import SceneAssetCoordinator


NOW = "2026-08-16T10:00:00+00:00"


def _artifact(artifact_id: str) -> Artifact:
    return Artifact(
        artifact_id=artifact_id,
        logical_id=f"logical_{artifact_id}",
        version_no=1,
        is_current=True,
        artifact_type=ArtifactType.JSON,
        artifact_role=None,
        path=f"tests/{artifact_id}",
        content_hash=f"hash_{artifact_id}",
        sha256=f"sha_{artifact_id}",
        mime_type="application/json",
        size_bytes=1,
        status=ArtifactStatus.READY,
        created_at=NOW,
    )


def _build_factory(tmp_path: Path) -> sessionmaker[Session]:
    engine = create_sqlalchemy_engine(f"sqlite:///{(tmp_path / 'coordinator.sqlite3').resolve()}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def _seed_two_beat_operation(factory: sessionmaker[Session]) -> None:
    with factory() as session:
        session.add(
            Channel(
                channel_id="channel_v2",
                name="V2",
                language="en",
                niche="test",
                audience="test",
                channel_dna_json={},
                channel_context_json={},
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.add_all([_artifact("artifact_source"), _artifact("artifact_prompt")])
        session.flush()
        session.add(
            Project(
                project_id="project_v2",
                channel_id="channel_v2",
                name="V2",
                description=None,
                channel_context_json={},
                channel_dna_json={},
                script_artifact_id="artifact_source",
                script_analysis_artifact_id="artifact_source",
                style_bible_artifact_id="artifact_source",
                scene_plan_artifact_id="artifact_source",
                visual_plan_artifact_id="artifact_source",
                prompt_bundle_artifact_id="artifact_prompt",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.flush()
        for index in (1, 2):
            session.add(
                SceneAssetPromptRevision(
                    prompt_revision_id=f"scene_asset_prompt_revision_{index:012x}",
                    project_id="project_v2",
                    scene_id=f"scene_{index:03d}",
                    beat_id=f"beat_{index:03d}",
                    revision_no=1,
                    revision_kind="compiled",
                    is_current=True,
                    source_prompt_bundle_artifact_id="artifact_prompt",
                    source_prompt_item_id=f"item_{index}",
                    source_prompt_hash=f"prompt_hash_{index}",
                    image_prompt=f"prompt {index}",
                    negative_prompt="",
                    created_at=NOW,
                )
            )
        session.flush()
        session.add(
            SceneAssetWorkingOperation(
                operation_id="operation_v2",
                project_id="project_v2",
                operation_type="generate_all",
                status="queued",
                source_script_artifact_id="artifact_source",
                source_script_analysis_artifact_id="artifact_source",
                source_style_bible_artifact_id="artifact_source",
                source_scene_plan_artifact_id="artifact_source",
                source_visual_plan_artifact_id="artifact_source",
                source_prompt_bundle_artifact_id="artifact_prompt",
                source_reference_approval_set_artifact_id=None,
                provider_settings_snapshot_json={"provider": "glabs_webhook"},
                provider_settings_snapshot_hash="settings_hash",
                total_beat_count=2,
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
            )
        )
        session.flush()
        for index in (1, 2):
            beat_id = f"beat_{index:03d}"
            scene_id = f"scene_{index:03d}"
            operation_beat_id = f"operation_beat_{index:03d}"
            session.add(
                SceneAssetOperationBeat(
                    operation_beat_id=operation_beat_id,
                    operation_id="operation_v2",
                    project_id="project_v2",
                    scene_id=scene_id,
                    beat_id=beat_id,
                    version=1,
                    status="queued",
                    candidate_count=1,
                    prompt_revision_id=f"scene_asset_prompt_revision_{index:012x}",
                    prompt_bundle_artifact_id="artifact_prompt",
                    prompt_item_id=f"item_{index}",
                    prompt_hash=f"prompt_hash_{index}",
                    image_prompt=f"prompt {index}",
                    negative_prompt="",
                    generation_options_json={"aspect_ratio": "16:9"},
                    generation_options_hash=f"options_{index}",
                    reference_bindings_json=[],
                    reference_bindings_hash=f"refs_{index}",
                    continuity_snapshot_json=None,
                    error_code=None,
                    error_message=None,
                    error_details_json=None,
                    created_at=NOW,
                    updated_at=NOW,
                    completed_at=None,
                )
            )
            session.flush()
            session.add(
                SceneAssetBeatState(
                    project_id="project_v2",
                    beat_id=beat_id,
                    scene_id=scene_id,
                    version=1,
                    resolution_status="not_generated",
                    review_status="unreviewed",
                    current_draft_candidate_id=None,
                    selected_candidate_id=None,
                    active_operation_beat_id=operation_beat_id,
                    continuity_source_scene_id=None,
                    continuity_source_beat_id=None,
                    continuity_source_candidate_id=None,
                    last_error_json=None,
                    last_progress_at=NOW,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            session.add(
                SceneAssetOperationTask(
                    operation_task_id=f"task_{index:03d}",
                    operation_beat_id=operation_beat_id,
                    candidate_count=1,
                    candidate_index=1,
                    status="queued",
                    attempt_no=1,
                    media_task_id=None,
                    submit_attempt_token=None,
                    submit_started_at=None,
                    next_reconcile_at=NOW,
                    error_code=None,
                    error_message=None,
                    error_details_json=None,
                    created_at=NOW,
                    updated_at=NOW,
                    completed_at=None,
                )
            )
        session.commit()


def test_prepare_submission_persists_before_io_and_releases_operation_for_other_beats(tmp_path: Path) -> None:
    factory = _build_factory(tmp_path)
    _seed_two_beat_operation(factory)
    coordinator = SceneAssetCoordinator(factory)

    first_lease = coordinator.claim_due_operation(owner="coordinator_a", now_iso=NOW, lease_seconds=30)
    assert first_lease is not None
    first_action = coordinator.plan_next_action(first_lease, now_iso=NOW)
    assert first_action.action == "submit"
    assert first_action.operation_task_id == "task_001"

    prepared = coordinator.prepare_submission(
        first_lease,
        operation_task_id="task_001",
        now_iso=NOW,
    )
    assert prepared.operation_task_id == "task_001"
    assert prepared.submit_attempt_token.startswith("scene_asset_submit_")

    with factory() as session:
        operation = session.get(SceneAssetWorkingOperation, "operation_v2")
        first_task = session.get(SceneAssetOperationTask, "task_001")
        first_beat = session.get(SceneAssetOperationBeat, "operation_beat_001")
        assert operation is not None and operation.status == "running"
        assert operation.reconcile_token is None and operation.reconcile_owner is None
        assert first_task is not None and first_task.status == "submitting"
        assert first_task.submit_attempt_token == prepared.submit_attempt_token
        assert first_beat is not None and first_beat.status == "running"

    with pytest.raises(AppError) as stale_lease:
        coordinator.renew_lease(first_lease, now_iso=NOW, lease_seconds=30)
    assert stale_lease.value.error_code == "SCENE_ASSET_V2_RECONCILE_LEASE_LOST"

    second_lease = coordinator.claim_due_operation(owner="coordinator_b", now_iso=NOW, lease_seconds=30)
    assert second_lease is not None
    second_action = coordinator.plan_next_action(second_lease, now_iso=NOW)
    assert second_action.action == "submit"
    assert second_action.operation_task_id == "task_002"
    coordinator.release_lease(second_lease, now_iso=NOW)
