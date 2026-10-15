from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

from app.api.errors import AppError
from app.db import models as _models  # noqa: F401
from app.db import scene_asset_working_models as _scene_asset_models  # noqa: F401
from app.db import visual_reference_working_models as _visual_reference_models  # noqa: F401
from app.db.base import Base
from app.db.enums import ArtifactStatus, ArtifactType
from app.db.models import Artifact, Channel, Project
from app.db.session import create_sqlalchemy_engine
from app.db.visual_reference_anchor_state import VisualReferenceAnchorState
from app.db.visual_reference_operation_anchor import VisualReferenceOperationAnchor
from app.db.visual_reference_operation_task import VisualReferenceOperationTask
from app.db.visual_reference_prompt_revision import VisualReferencePromptRevision
from app.db.visual_reference_working_operation import VisualReferenceWorkingOperation
from app.modules.v4.visual_reference_coordinator import VisualReferenceCoordinator
from app.modules.v4.visual_reference_working_state import VisualReferenceWorkingStateService

NOW = "2026-08-18T01:00:00+00:00"


def _factory(tmp_path: Path):
    engine = create_sqlalchemy_engine(f"sqlite:///{(tmp_path / 'visual_reference_state.sqlite3').resolve()}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def _seed(factory) -> None:
    with factory() as session:
        session.add(Channel(
            channel_id="channel_vr",
            name="VR",
            language="en",
            niche="test",
            audience="test",
            channel_dna_json={},
            channel_context_json={},
            created_at=NOW,
            updated_at=NOW,
        ))
        session.flush()
        session.add(Project(
            project_id="project_vr",
            channel_id="channel_vr",
            name="Visual Reference V2",
            description=None,
            channel_context_json={},
            channel_dna_json={},
            created_at=NOW,
            updated_at=NOW,
        ))
        session.add(Artifact(
            artifact_id="artifact_style",
            logical_id="style",
            version_no=1,
            is_current=True,
            superseded_by_artifact_id=None,
            artifact_type=ArtifactType.JSON,
            artifact_role=None,
            path="style.json",
            content_hash="style_hash",
            sha256="style_hash",
            mime_type="application/json",
            size_bytes=1,
            width=None,
            height=None,
            duration_sec=None,
            provider_result_url=None,
            provider_source=None,
            source_job_id=None,
            status=ArtifactStatus.READY,
            created_at=NOW,
            deleted_at=None,
        ))
        session.flush()
        session.add(VisualReferencePromptRevision(
            prompt_revision_id="vr_prompt_1",
            project_id="project_vr",
            anchor_type="character",
            anchor_id="char_001",
            revision_no=1,
            revision_kind="compiled",
            is_current=1,
            source_style_bible_artifact_id="artifact_style",
            source_definition_hash="definition_hash",
            prompt="draw character",
            negative_prompt="text",
            created_at=NOW,
        ))
        session.add(VisualReferenceWorkingOperation(
            operation_id="vr_operation_1",
            project_id="project_vr",
            operation_type="generate_anchor",
            status="queued",
            source_style_bible_artifact_id="artifact_style",
            expected_visual_reference_set_artifact_id=None,
            provider_settings_snapshot_json={"provider": "test"},
            provider_settings_snapshot_hash="provider_hash",
            total_anchor_count=1,
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
        session.flush()
        session.add(VisualReferenceOperationAnchor(
            operation_anchor_id="vr_anchor_op_1",
            operation_id="vr_operation_1",
            project_id="project_vr",
            anchor_type="character",
            anchor_id="char_001",
            version=1,
            status="queued",
            candidate_count=1,
            prompt_revision_id="vr_prompt_1",
            prompt="draw character",
            negative_prompt="text",
            generation_options_json={"aspect_ratio": "1:1"},
            generation_options_hash="options_hash",
            source_definition_hash="definition_hash",
            error_code=None,
            error_message=None,
            error_details_json=None,
            created_at=NOW,
            updated_at=NOW,
            completed_at=None,
        ))
        session.add(VisualReferenceOperationTask(
            operation_task_id="vr_task_1",
            operation_anchor_id="vr_anchor_op_1",
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
        ))
        session.flush()
        session.add(VisualReferenceAnchorState(
            project_id="project_vr",
            anchor_type="character",
            anchor_id="char_001",
            version=1,
            resolution_status="not_generated",
            review_status="unreviewed",
            current_prompt_revision_id="vr_prompt_1",
            selected_candidate_id=None,
            approved_candidate_id=None,
            active_operation_anchor_id=None,
            last_error_json=None,
            last_progress_at=None,
            created_at=NOW,
            updated_at=NOW,
        ))
        session.commit()


def test_anchor_state_compare_and_set_rejects_stale_version(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    _seed(factory)
    with factory() as session:
        state = VisualReferenceWorkingStateService(session)
        version = state.compare_and_set_anchor_state(
            project_id="project_vr",
            anchor_type="character",
            anchor_id="char_001",
            expected_version=1,
            values={"last_progress_at": NOW},
            now_iso=NOW,
        )
        assert version == 2
        session.commit()

    with factory() as session:
        state = VisualReferenceWorkingStateService(session)
        with pytest.raises(AppError) as raised:
            state.compare_and_set_anchor_state(
                project_id="project_vr",
                anchor_type="character",
                anchor_id="char_001",
                expected_version=1,
                values={"last_progress_at": NOW},
                now_iso=NOW,
            )
        assert raised.value.error_code == "VISUAL_REFERENCE_V2_ANCHOR_STATE_CONFLICT"


def test_shared_coordinator_plans_anchor_task_and_fences_submit_intent(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    _seed(factory)
    with factory() as session:
        state = VisualReferenceWorkingStateService(session)
        state.claim_anchor_for_operation(operation_anchor_id="vr_anchor_op_1", expected_version=1, now_iso=NOW)
        session.commit()

    coordinator = VisualReferenceCoordinator(factory)
    lease = coordinator.claim_due_operation(owner="vr_runner", now_iso=NOW, lease_seconds=30)
    assert lease is not None
    action = coordinator.plan_next_action(lease, now_iso=NOW)
    assert action.action == "submit"
    assert action.operation_anchor_id == "vr_anchor_op_1"
    assert action.operation_task_id == "vr_task_1"

    prepared = coordinator.prepare_submission(lease, operation_task_id="vr_task_1", now_iso=NOW)
    assert prepared.operation_anchor_id == "vr_anchor_op_1"
    assert prepared.submit_attempt_token.startswith("visual_reference_submit_")
    with factory() as session:
        task = session.get(VisualReferenceOperationTask, "vr_task_1")
        assert task is not None
        assert task.status == "submitting"
        assert task.submit_started_at == NOW
        operation = session.get(VisualReferenceWorkingOperation, "vr_operation_1")
        assert operation is not None
        assert operation.reconcile_token is None
