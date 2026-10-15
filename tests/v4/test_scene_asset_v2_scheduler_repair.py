from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from app.db import models as db_models  # noqa: F401
from app.db import scene_asset_working_models as working_models  # noqa: F401
from app.db.base import Base
from app.db.enums import ArtifactStatus, ArtifactType
from app.db.models import Artifact, Channel, Project
from app.db.scene_asset_working_operation import SceneAssetWorkingOperation
from app.db.session import create_sqlalchemy_engine
from app.modules.v4.scene_asset_coordinator import SceneAssetCoordinator
from app.scene_asset_coordinator_runner import SceneAssetCoordinatorRunner


NOW = "2026-08-17T05:00:00+00:00"
FUTURE = "2099-08-17T05:00:00+00:00"


def _factory(tmp_path: Path) -> sessionmaker[Session]:
    engine = create_sqlalchemy_engine(f"sqlite:///{(tmp_path / 'scene_asset_scheduler_repair.sqlite3').resolve()}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def _artifact() -> Artifact:
    return Artifact(
        artifact_id="artifact_scheduler_source",
        logical_id="logical/artifact_scheduler_source",
        version_no=1,
        is_current=True,
        artifact_type=ArtifactType.JSON,
        artifact_role=None,
        path="tests/artifact_scheduler_source.json",
        content_hash="hash_scheduler_source",
        sha256="sha_scheduler_source",
        mime_type="application/json",
        size_bytes=1,
        width=None,
        height=None,
        status=ArtifactStatus.READY,
        created_at=NOW,
    )


def _operation(
    operation_id: str,
    *,
    status: str,
    next_reconcile_at: str | None,
    reconcile_owner: str | None = None,
    reconcile_token: str | None = None,
    reconcile_lease_expires_at: str | None = None,
) -> SceneAssetWorkingOperation:
    return SceneAssetWorkingOperation(
        operation_id=operation_id,
        project_id="project_scheduler_repair",
        operation_type="generate_beat",
        status=status,
        source_script_artifact_id="artifact_scheduler_source",
        source_script_analysis_artifact_id="artifact_scheduler_source",
        source_style_bible_artifact_id="artifact_scheduler_source",
        source_scene_plan_artifact_id="artifact_scheduler_source",
        source_visual_plan_artifact_id="artifact_scheduler_source",
        source_prompt_bundle_artifact_id="artifact_scheduler_source",
        source_reference_approval_set_artifact_id=None,
        provider_settings_snapshot_json={},
        provider_settings_snapshot_hash="hash_provider_settings",
        total_beat_count=1,
        completed_count=0,
        failed_count=0,
        cancelled_count=0,
        next_reconcile_at=next_reconcile_at,
        last_progress_at=NOW,
        reconcile_owner=reconcile_owner,
        reconcile_token=reconcile_token,
        reconcile_lease_expires_at=reconcile_lease_expires_at,
        cancel_requested_at=None,
        cancelled_at=None,
        request_json={},
        result_json={},
        error_code=None,
        error_message=None,
        error_details_json=None,
        created_at=NOW,
        updated_at=NOW,
        completed_at=NOW if status == "completed" else None,
    )


def _seed(factory: sessionmaker[Session]) -> None:
    with factory() as session:
        session.add(
            Channel(
                channel_id="channel_scheduler_repair",
                name="Scheduler Repair",
                language="en",
                niche="test",
                audience="test",
                channel_dna_json={},
                channel_context_json={},
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.add(_artifact())
        session.flush()
        session.add(
            Project(
                project_id="project_scheduler_repair",
                channel_id="channel_scheduler_repair",
                name="Scheduler Repair",
                description=None,
                channel_context_json={},
                channel_dna_json={},
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.flush()
        session.add_all(
            [
                _operation("operation_stalled", status="running", next_reconcile_at=None),
                _operation("operation_terminal", status="completed", next_reconcile_at=None),
                _operation(
                    "operation_live_lease",
                    status="running",
                    next_reconcile_at=None,
                    reconcile_owner="other-coordinator",
                    reconcile_token="live-token",
                    reconcile_lease_expires_at=FUTURE,
                ),
            ]
        )
        session.commit()


def test_runner_repairs_only_lease_free_unscheduled_active_operations(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    _seed(factory)

    runner = SceneAssetCoordinatorRunner(factory, owner="scheduler-repair-test")
    repaired = runner._repair_unscheduled_active_operations()

    assert repaired == 1
    with factory() as session:
        stalled = session.get(SceneAssetWorkingOperation, "operation_stalled")
        terminal = session.get(SceneAssetWorkingOperation, "operation_terminal")
        live_lease = session.get(SceneAssetWorkingOperation, "operation_live_lease")
        assert stalled is not None and stalled.next_reconcile_at is not None
        assert terminal is not None and terminal.next_reconcile_at is None
        assert live_lease is not None and live_lease.next_reconcile_at is None

    lease = SceneAssetCoordinator(factory).claim_due_operation(
        owner="scheduler-repair-claim",
        now_iso=FUTURE,
        lease_seconds=30,
    )
    assert lease is not None
    assert lease.operation_id == "operation_stalled"
