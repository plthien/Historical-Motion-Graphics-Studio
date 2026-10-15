from __future__ import annotations

import inspect
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.api.errors import AppError
from app.db import models as db_models
from app.db import scene_asset_working_models as working_models  # noqa: F401
from app.db.base import Base
from app.db.enums import ArtifactStatus, ArtifactType
from app.db.models import Artifact, Channel, MediaBatch, MediaTask, Project
from app.db.scene_asset_beat_state import SceneAssetBeatState
from app.db.scene_asset_candidate import SceneAssetCandidate
from app.db.scene_asset_operation_beat import SceneAssetOperationBeat
from app.db.scene_asset_operation_task import SceneAssetOperationTask
from app.db.scene_asset_prompt_revision import SceneAssetPromptRevision
from app.db.scene_asset_working_operation import SceneAssetWorkingOperation
from app.db.session import create_sqlalchemy_engine
from app.modules.v4.scene_asset_coordinator import SceneAssetCoordinator
from app.modules.v4.scene_asset_working_state import SceneAssetWorkingStateService


T0 = datetime(2026, 8, 16, 10, 0, tzinfo=UTC)
NOW = T0.isoformat()


def _at(seconds: int) -> str:
    return (T0 + timedelta(seconds=seconds)).isoformat()


@dataclass(frozen=True, slots=True)
class RuntimeDb:
    factory: sessionmaker[Session]
    database_path: Path


@pytest.fixture
def runtime_db(tmp_path: Path) -> RuntimeDb:
    database_path = tmp_path / "scene_asset_v2_runtime.sqlite3"
    engine = create_sqlalchemy_engine(f"sqlite:///{database_path.resolve()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    try:
        yield RuntimeDb(factory=factory, database_path=database_path)
    finally:
        engine.dispose()


def _artifact(artifact_id: str, *, artifact_type: ArtifactType = ArtifactType.JSON) -> Artifact:
    return Artifact(
        artifact_id=artifact_id,
        logical_id=f"logical_{artifact_id}",
        version_no=1,
        is_current=True,
        artifact_type=artifact_type,
        artifact_role=None,
        path=f"tests/{artifact_id}",
        content_hash=f"hash_{artifact_id}",
        sha256=f"sha_{artifact_id}",
        mime_type="application/json" if artifact_type == ArtifactType.JSON else "image/png",
        size_bytes=1,
        status=ArtifactStatus.READY,
        created_at=NOW,
    )


def _seed_foundation(session: Session, *, project_id: str = "project_v2") -> tuple[str, str]:
    channel_id = f"channel_{project_id}"
    source_artifact_id = f"artifact_source_{project_id}"
    prompt_artifact_id = f"artifact_prompt_{project_id}"
    session.add(
        Channel(
            channel_id=channel_id,
            name="V2 Test",
            language="en",
            niche="test",
            audience="test",
            channel_dna_json={},
            channel_context_json={},
            created_at=NOW,
            updated_at=NOW,
        )
    )
    session.add_all([_artifact(source_artifact_id), _artifact(prompt_artifact_id)])
    session.flush()
    session.add(
        Project(
            project_id=project_id,
            channel_id=channel_id,
            name="Scene Assets V2",
            description=None,
            channel_context_json={},
            channel_dna_json={},
            script_artifact_id=source_artifact_id,
            script_analysis_artifact_id=source_artifact_id,
            style_bible_artifact_id=source_artifact_id,
            scene_plan_artifact_id=source_artifact_id,
            visual_plan_artifact_id=source_artifact_id,
            prompt_bundle_artifact_id=prompt_artifact_id,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    session.flush()
    return source_artifact_id, prompt_artifact_id


def _seed_operation(
    session: Session,
    *,
    project_id: str = "project_v2",
    operation_id: str = "operation_v2",
    beat_count: int = 1,
    candidate_count: int = 1,
    operation_status: str = "queued",
    task_status: str = "queued",
) -> dict[str, list[str] | str]:
    source_artifact_id, prompt_artifact_id = _seed_foundation(session, project_id=project_id)
    for beat_index in range(1, beat_count + 1):
        beat_id = f"beat_{beat_index:03d}"
        session.add(
            SceneAssetPromptRevision(
                prompt_revision_id=f"scene_asset_prompt_revision_{beat_index:012x}",
                project_id=project_id,
                scene_id=f"scene_{beat_index:03d}",
                beat_id=beat_id,
                revision_no=1,
                revision_kind="compiled",
                is_current=True,
                source_prompt_bundle_artifact_id=prompt_artifact_id,
                source_prompt_item_id=f"prompt_item_{beat_id}",
                source_prompt_hash=f"prompt_hash_{beat_id}",
                image_prompt=f"image prompt {beat_id}",
                negative_prompt="",
                created_at=NOW,
            )
        )
    session.flush()
    session.add(
        SceneAssetWorkingOperation(
            operation_id=operation_id,
            project_id=project_id,
            operation_type="generate_all",
            status=operation_status,
            source_script_artifact_id=source_artifact_id,
            source_script_analysis_artifact_id=source_artifact_id,
            source_style_bible_artifact_id=source_artifact_id,
            source_scene_plan_artifact_id=source_artifact_id,
            source_visual_plan_artifact_id=source_artifact_id,
            source_prompt_bundle_artifact_id=prompt_artifact_id,
            source_reference_approval_set_artifact_id=None,
            provider_settings_snapshot_json={"provider": "glabs_webhook", "model": "test"},
            provider_settings_snapshot_hash="provider_settings_hash",
            total_beat_count=beat_count,
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

    beat_ids: list[str] = []
    operation_beat_ids: list[str] = []
    task_ids: list[str] = []
    for beat_index in range(1, beat_count + 1):
        beat_id = f"beat_{beat_index:03d}"
        scene_id = f"scene_{beat_index:03d}"
        operation_beat_id = f"operation_beat_{beat_index:03d}"
        beat_ids.append(beat_id)
        operation_beat_ids.append(operation_beat_id)
        beat_status = "queued" if task_status == "queued" else "running"
        session.add(
            SceneAssetOperationBeat(
                operation_beat_id=operation_beat_id,
                operation_id=operation_id,
                project_id=project_id,
                scene_id=scene_id,
                beat_id=beat_id,
                version=1,
                status=beat_status,
                candidate_count=candidate_count,
                prompt_revision_id=f"scene_asset_prompt_revision_{beat_index:012x}",
                prompt_bundle_artifact_id=prompt_artifact_id,
                prompt_item_id=f"prompt_item_{beat_id}",
                prompt_hash=f"prompt_hash_{beat_id}",
                image_prompt=f"image prompt {beat_id}",
                negative_prompt="",
                generation_options_json={"aspect_ratio": "16:9"},
                generation_options_hash=f"options_hash_{beat_id}",
                reference_bindings_json=[],
                reference_bindings_hash=f"bindings_hash_{beat_id}",
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
                project_id=project_id,
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
        for candidate_index in range(1, candidate_count + 1):
            task_id = f"task_{beat_index:03d}_{candidate_index:02d}"
            task_ids.append(task_id)
            submit_token = f"submit_{beat_index}_{candidate_index}" if task_status == "submitting" else None
            session.add(
                SceneAssetOperationTask(
                    operation_task_id=task_id,
                    operation_beat_id=operation_beat_id,
                    candidate_count=candidate_count,
                    candidate_index=candidate_index,
                    status=task_status,
                    attempt_no=1,
                    media_task_id=None,
                    submit_attempt_token=submit_token,
                    submit_started_at=NOW if task_status == "submitting" else None,
                    next_reconcile_at=NOW if task_status in {"queued", "waiting_provider"} else None,
                    error_code=None,
                    error_message=None,
                    error_details_json=None,
                    created_at=NOW,
                    updated_at=NOW,
                    completed_at=None,
                )
            )
    session.commit()
    return {
        "project_id": project_id,
        "operation_id": operation_id,
        "beat_ids": beat_ids,
        "operation_beat_ids": operation_beat_ids,
        "task_ids": task_ids,
        "prompt_artifact_id": prompt_artifact_id,
    }


def _add_media_task(session: Session, *, project_id: str, media_task_id: str, entity_id: str) -> None:
    batch_id = f"batch_{media_task_id}"
    session.add(
        MediaBatch(
            batch_id=batch_id,
            purpose="scene_assets",
            project_id=project_id,
            style_bible_revision_id=None,
            domain_revision_id=None,
            total=1,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    session.flush()
    session.add(
        MediaTask(
            media_task_id=media_task_id,
            batch_id=batch_id,
            purpose="scene_assets",
            project_id=project_id,
            style_bible_revision_id=None,
            domain_revision_id=None,
            entity_type="scene_asset_beat",
            entity_id=entity_id,
            candidate_index=1,
            status="queued",
            external_provider="glabs_webhook",
            external_task_id="deadbeef",
            provider_status="pending",
            provider_submitted_at=NOW,
            prompt="test",
            negative_prompt=None,
            model="test",
            aspect_ratio="16:9",
            upscale_json=[],
            reference_images_json=None,
            metadata_json=None,
            result_json=[],
            selected=False,
            locked=False,
            created_at=NOW,
            updated_at=NOW,
            completed_at=None,
            poll_count=0,
            poll_revision=0,
        )
    )
    session.flush()


def _materialize_candidate(session: Session, *, seed: dict[str, list[str] | str], candidate_id: str) -> str:
    project_id = str(seed["project_id"])
    operation_beat_id = str(seed["operation_beat_ids"][0])
    task_id = str(seed["task_ids"][0])
    beat = session.get(SceneAssetOperationBeat, operation_beat_id)
    assert beat is not None
    artifact_id = f"artifact_{candidate_id}"
    session.add(_artifact(artifact_id, artifact_type=ArtifactType.IMAGE))
    session.flush()
    session.add(
        SceneAssetCandidate(
            candidate_id=candidate_id,
            project_id=project_id,
            scene_id=beat.scene_id,
            beat_id=beat.beat_id,
            artifact_id=artifact_id,
            source_kind="generated",
            operation_beat_id=operation_beat_id,
            operation_task_id=task_id,
            source_prompt_revision_id=beat.prompt_revision_id,
            source_prompt_bundle_artifact_id=beat.prompt_bundle_artifact_id,
            source_prompt_item_id=beat.prompt_item_id,
            source_prompt_hash=beat.prompt_hash,
            continuity_snapshot_json=beat.continuity_snapshot_json,
            created_at=NOW,
        )
    )
    session.flush()
    return candidate_id


def test_same_due_operation_has_one_reconcile_lease_winner(runtime_db: RuntimeDb) -> None:
    with runtime_db.factory() as session:
        _seed_operation(session)
    coordinator = SceneAssetCoordinator(runtime_db.factory)
    barrier = Barrier(2)

    def claim(owner: str):
        barrier.wait()
        return coordinator.claim_due_operation(owner=owner, now_iso=NOW, lease_seconds=30)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ("coordinator_a", "coordinator_b")))
    winners = [result for result in results if result is not None]
    assert len(winners) == 1

    with runtime_db.factory() as session:
        operation = session.get(SceneAssetWorkingOperation, "operation_v2")
        assert operation is not None
        assert operation.reconcile_token == winners[0].token
        assert operation.reconcile_owner == winners[0].owner


def test_expired_lease_takeover_fences_old_owner_and_recovers_ambiguous_submit(runtime_db: RuntimeDb) -> None:
    continuity_snapshot = {
        "relation": "same_setup",
        "source_beat_id": "beat_previous",
        "source_asset_artifact_id": "artifact_previous",
        "source_asset_sha256": "sha_previous",
        "mime_type": "image/png",
        "source_kind": "approved",
    }
    with runtime_db.factory() as session:
        _seed_operation(session, operation_status="running", task_status="submitting")
        beat = session.get(SceneAssetOperationBeat, "operation_beat_001")
        assert beat is not None
        beat.continuity_snapshot_json = continuity_snapshot
        session.commit()
    coordinator = SceneAssetCoordinator(runtime_db.factory)
    old_lease = coordinator.claim_due_operation(owner="old", now_iso=NOW, lease_seconds=1)
    assert old_lease is not None
    new_lease = coordinator.claim_due_operation(owner="new", now_iso=_at(2), lease_seconds=30)
    assert new_lease is not None
    assert new_lease.token != old_lease.token

    with pytest.raises(AppError) as renew_error:
        coordinator.renew_lease(old_lease, now_iso=_at(2), lease_seconds=30)
    assert renew_error.value.error_code == "SCENE_ASSET_V2_RECONCILE_LEASE_LOST"

    with pytest.raises(AppError) as stale_recovery_error:
        coordinator.recover_stale_submissions(old_lease, stale_before_iso=_at(1), now_iso=_at(2))
    assert stale_recovery_error.value.error_code == "SCENE_ASSET_V2_RECONCILE_LEASE_LOST"

    assert coordinator.recover_stale_submissions(
        new_lease,
        stale_before_iso=_at(1),
        now_iso=_at(2),
    ) == 1
    with runtime_db.factory() as session:
        task = session.get(SceneAssetOperationTask, "task_001_01")
        beat = session.get(SceneAssetOperationBeat, "operation_beat_001")
        state = session.get(SceneAssetBeatState, ("project_v2", "beat_001"))
        operation = session.get(SceneAssetWorkingOperation, "operation_v2")
        assert task is not None and task.status == "submit_outcome_unknown"
        assert beat is not None and beat.status == "failed"
        assert beat.continuity_snapshot_json == continuity_snapshot
        assert state is not None and state.active_operation_beat_id is None
        assert state.resolution_status == "failed"
        assert operation is not None and operation.status == "finalizing"

    assert coordinator.refresh_local_aggregate(new_lease, now_iso=_at(3)) == "failed"
    assert coordinator.release_lease(new_lease, now_iso=_at(3)) is True


def test_same_beat_version_cas_conflicts_but_different_beats_do_not(runtime_db: RuntimeDb) -> None:
    with runtime_db.factory() as session:
        _seed_operation(session, beat_count=2)

    session_a = runtime_db.factory()
    session_b = runtime_db.factory()
    try:
        state_a = session_a.get(SceneAssetBeatState, ("project_v2", "beat_001"))
        state_b = session_b.get(SceneAssetBeatState, ("project_v2", "beat_001"))
        assert state_a is not None and state_b is not None
        version = state_a.version
        SceneAssetWorkingStateService(session_a).compare_and_set_beat_state(
            project_id="project_v2",
            beat_id="beat_001",
            expected_version=version,
            values={"last_progress_at": _at(1)},
            now_iso=_at(1),
        )
        session_a.commit()
        with pytest.raises(AppError) as conflict:
            SceneAssetWorkingStateService(session_b).compare_and_set_beat_state(
                project_id="project_v2",
                beat_id="beat_001",
                expected_version=version,
                values={"last_progress_at": _at(2)},
                now_iso=_at(2),
            )
        assert conflict.value.error_code == "SCENE_ASSET_V2_BEAT_STATE_CONFLICT"
        session_b.rollback()
    finally:
        session_a.close()
        session_b.close()

    session_c = runtime_db.factory()
    session_d = runtime_db.factory()
    try:
        state_c = session_c.get(SceneAssetBeatState, ("project_v2", "beat_001"))
        state_d = session_d.get(SceneAssetBeatState, ("project_v2", "beat_002"))
        assert state_c is not None and state_d is not None
        SceneAssetWorkingStateService(session_c).compare_and_set_beat_state(
            project_id="project_v2",
            beat_id="beat_001",
            expected_version=state_c.version,
            values={"last_progress_at": _at(3)},
            now_iso=_at(3),
        )
        session_c.commit()
        SceneAssetWorkingStateService(session_d).compare_and_set_beat_state(
            project_id="project_v2",
            beat_id="beat_002",
            expected_version=state_d.version,
            values={"last_progress_at": _at(4)},
            now_iso=_at(4),
        )
        session_d.commit()
    finally:
        session_c.close()
        session_d.close()


def test_local_task_lifecycle_materializes_candidate_and_finalizes_operation(runtime_db: RuntimeDb) -> None:
    with runtime_db.factory() as session:
        seed = _seed_operation(session)
        _add_media_task(session, project_id="project_v2", media_task_id="media_task_v2", entity_id="beat_001")
        state = SceneAssetWorkingStateService(session)
        state.begin_task_submission(
            operation_task_id="task_001_01",
            submit_attempt_token="submit_attempt_v2",
            now_iso=_at(1),
        )
        assert state.refresh_operation_beat(operation_beat_id="operation_beat_001", now_iso=_at(1)) == "running"
        assert state.refresh_operation(operation_id="operation_v2", now_iso=_at(1)) == "running"
        state.mark_task_waiting_provider(
            operation_task_id="task_001_01",
            media_task_id="media_task_v2",
            now_iso=_at(2),
            next_reconcile_at=_at(3),
        )
        state.mark_task_downloading(operation_task_id="task_001_01", now_iso=_at(3))
        state.mark_task_materializing(operation_task_id="task_001_01", now_iso=_at(4))
        fence = state.assert_task_can_materialize(operation_task_id="task_001_01")
        candidate_id = _materialize_candidate(session, seed=seed, candidate_id="candidate_v2")
        state.compare_and_set_beat_state(
            project_id=fence.project_id,
            beat_id=fence.beat_id,
            expected_version=fence.beat_state_version,
            values={
                "resolution_status": "candidate_ready",
                "review_status": "current_draft",
                "current_draft_candidate_id": candidate_id,
                "last_progress_at": _at(4),
            },
            now_iso=_at(4),
        )
        state.mark_task_completed(operation_task_id="task_001_01", now_iso=_at(5))
        assert state.refresh_operation_beat(operation_beat_id="operation_beat_001", now_iso=_at(5)) == "candidate_ready"
        assert state.refresh_operation(operation_id="operation_v2", now_iso=_at(5)) == "finalizing"
        assert state.refresh_operation(operation_id="operation_v2", now_iso=_at(6)) == "completed"
        session.commit()

    with runtime_db.factory() as session:
        operation = session.get(SceneAssetWorkingOperation, "operation_v2")
        beat = session.get(SceneAssetOperationBeat, "operation_beat_001")
        task = session.get(SceneAssetOperationTask, "task_001_01")
        state = session.get(SceneAssetBeatState, ("project_v2", "beat_001"))
        assert operation is not None and operation.status == "completed" and operation.completed_count == 1
        assert beat is not None and beat.status == "candidate_ready"
        assert task is not None and task.status == "completed"
        assert state is not None
        assert state.resolution_status == "candidate_ready"
        assert state.current_draft_candidate_id == "candidate_v2"
        assert state.active_operation_beat_id is None


def test_cancel_is_local_idempotent_preserves_completed_output_and_fences_late_result(runtime_db: RuntimeDb) -> None:
    with runtime_db.factory() as session:
        seed = _seed_operation(session, beat_count=2, operation_status="running", task_status="materializing")
        # Beat 1 already produced a reviewable candidate; beat 2 remains in-flight.
        candidate_id = _materialize_candidate(session, seed=seed, candidate_id="candidate_preserved")
        task1 = session.get(SceneAssetOperationTask, "task_001_01")
        beat1 = session.get(SceneAssetOperationBeat, "operation_beat_001")
        state1 = session.get(SceneAssetBeatState, ("project_v2", "beat_001"))
        assert task1 is not None and beat1 is not None and state1 is not None
        task1.status = "completed"
        task1.completed_at = _at(1)
        beat1.status = "candidate_ready"
        beat1.completed_at = _at(1)
        state1.resolution_status = "candidate_ready"
        state1.review_status = "current_draft"
        state1.current_draft_candidate_id = candidate_id
        state1.active_operation_beat_id = None
        operation = session.get(SceneAssetWorkingOperation, "operation_v2")
        assert operation is not None
        operation.completed_count = 1
        session.commit()

    coordinator = SceneAssetCoordinator(runtime_db.factory)
    lease = coordinator.claim_due_operation(owner="before_cancel", now_iso=_at(1), lease_seconds=30)
    assert lease is not None
    result = coordinator.cancel_operation(operation_id="operation_v2", now_iso=_at(2))
    assert result["status"] == "cancelled"
    assert result["completed_count"] == 1
    assert result["cancelled_count"] == 1
    assert coordinator.release_lease(lease, now_iso=_at(2), require_current=False) is False

    repeated = coordinator.cancel_operation(operation_id="operation_v2", now_iso=_at(3))
    assert repeated["already_cancelled"] is True

    with runtime_db.factory() as session:
        operation = session.get(SceneAssetWorkingOperation, "operation_v2")
        state1 = session.get(SceneAssetBeatState, ("project_v2", "beat_001"))
        state2 = session.get(SceneAssetBeatState, ("project_v2", "beat_002"))
        task2 = session.get(SceneAssetOperationTask, "task_002_01")
        beat2 = session.get(SceneAssetOperationBeat, "operation_beat_002")
        assert operation is not None and operation.status == "cancelled"
        assert operation.reconcile_token is None and operation.next_reconcile_at is None
        assert state1 is not None
        assert state1.resolution_status == "candidate_ready"
        assert state1.current_draft_candidate_id == "candidate_preserved"
        assert state2 is not None and state2.active_operation_beat_id is None
        assert task2 is not None and task2.status == "cancelled"
        assert beat2 is not None and beat2.status == "cancelled"
        with pytest.raises(AppError) as late_result:
            SceneAssetWorkingStateService(session).assert_task_can_materialize(operation_task_id="task_002_01")
        assert late_result.value.error_code == "SCENE_ASSET_V2_LATE_RESULT_FENCED"
        candidate_count = session.scalar(
            select(func.count()).select_from(SceneAssetCandidate).where(SceneAssetCandidate.beat_id == "beat_002")
        )
        assert int(candidate_count or 0) == 0


def test_coordinator_skips_inflight_submit_and_can_schedule_other_beat(runtime_db: RuntimeDb) -> None:
    with runtime_db.factory() as session:
        _seed_operation(session, beat_count=2, operation_status="running", task_status="queued")
        task1 = session.get(SceneAssetOperationTask, "task_001_01")
        beat1 = session.get(SceneAssetOperationBeat, "operation_beat_001")
        assert task1 is not None and beat1 is not None
        task1.status = "submitting"
        task1.submit_attempt_token = "submit_inflight"
        task1.submit_started_at = NOW
        task1.next_reconcile_at = None
        beat1.status = "running"
        session.commit()

    coordinator = SceneAssetCoordinator(runtime_db.factory)
    lease = coordinator.claim_due_operation(owner="coordinator", now_iso=NOW, lease_seconds=30)
    assert lease is not None
    action = coordinator.plan_next_action(lease, now_iso=NOW)
    assert action.action == "submit"
    assert action.operation_task_id == "task_002_01"
    coordinator.release_lease(lease, now_iso=NOW)


def test_invalid_task_transition_and_completion_without_candidate_are_rejected(runtime_db: RuntimeDb) -> None:
    with runtime_db.factory() as session:
        _seed_operation(session, operation_status="running", task_status="queued")
        state = SceneAssetWorkingStateService(session)
        with pytest.raises(AppError) as invalid:
            state.mark_task_downloading(operation_task_id="task_001_01", now_iso=_at(1))
        assert invalid.value.error_code == "SCENE_ASSET_V2_STATE_TRANSITION_INVALID"
        session.rollback()

    with runtime_db.factory() as session:
        task = session.get(SceneAssetOperationTask, "task_001_01")
        assert task is not None
        task.status = "materializing"
        beat = session.get(SceneAssetOperationBeat, "operation_beat_001")
        assert beat is not None
        beat.status = "running"
        session.commit()
        with pytest.raises(AppError) as missing_candidate:
            SceneAssetWorkingStateService(session).mark_task_completed(
                operation_task_id="task_001_01",
                now_iso=_at(2),
            )
        assert missing_candidate.value.error_code == "SCENE_ASSET_V2_STATE_INTEGRITY_INVALID"
        session.rollback()


def test_phase_b_coordinator_has_no_provider_queue_or_legacy_job_dependency() -> None:
    source = inspect.getsource(SceneAssetCoordinator)
    assert "app.modules.media" not in source
    assert "app.services.jobs" not in source
    assert "SceneAssetOperation" not in source
    assert "MediaBatch(" not in source
    assert "queue.enqueue" not in source
