from __future__ import annotations

from threading import Event

from sqlalchemy import text

from app.db.session import get_session_factory
from app.modules.v4.voice_job_projection import VoiceJobProjectionService
from app.modules.v4.voice_provider_contracts import VoiceProviderReconcileResult, VoiceProviderRegistry
from app.repositories.voice_durable_working import VoiceDurableWorkingRepository
from app.voice_coordinator_runner import VoiceCoordinatorRunner
from tests.v4.test_voice_durable_submission_recovery import AmbiguousNonIdempotentProvider
from tests.v4.test_voice_durable_provider_lifecycle import _seed_submitting_task


class ControlledAmbiguousProvider(AmbiguousNonIdempotentProvider):
    provider_key = "controlled_ambiguous_non_idempotent"

    def __init__(self) -> None:
        super().__init__()
        self.reconcile_calls = 0
        self.reconciled = Event()

    def reconcile_submission(self, request, handle, control) -> VoiceProviderReconcileResult:
        self.reconcile_calls += 1
        self.reconciled.set()
        return VoiceProviderReconcileResult(status="ambiguous")


def test_submit_outcome_unknown_is_parked_and_not_reclaimed_or_replayed() -> None:
    provider = ControlledAmbiguousProvider()
    task_id, _ = _seed_submitting_task(provider)
    due = "1900-01-01T00:00:00+00:00"
    with get_session_factory()() as session:
        operation_id = str(
            session.execute(
                text("SELECT operation_id FROM voice_segment_tasks WHERE voice_segment_task_id=:task_id"),
                {"task_id": task_id},
            ).scalar_one()
        )
        session.execute(
            text(
                """
                UPDATE voice_generation_operations
                SET next_reconcile_at='2099-01-01T00:00:00+00:00',
                    reconcile_owner=NULL, reconcile_token=NULL, reconcile_lease_expires_at=NULL
                WHERE operation_id<>:operation_id
                  AND status IN ('queued','running','finalizing')
                """
            ),
            {"operation_id": operation_id},
        )
        session.execute(
            text(
                """
                UPDATE voice_segment_tasks
                SET submit_started_at='2000-01-01T00:00:00+00:00'
                WHERE voice_segment_task_id=:task_id
                """
            ),
            {"task_id": task_id},
        )
        session.execute(
            text(
                """
                UPDATE voice_generation_operations
                SET status='running', next_reconcile_at=:due,
                    reconcile_owner=NULL, reconcile_token=NULL,
                    reconcile_lease_expires_at=NULL
                WHERE operation_id=:operation_id
                """
            ),
            {"operation_id": operation_id, "due": due},
        )
        session.commit()

    registry = VoiceProviderRegistry()
    registry.register(provider)
    runner = VoiceCoordinatorRunner(
        get_session_factory(),
        registry=registry,
        stale_submit_seconds=1,
        provider_pool_workers=1,
        provider_pool_max_outstanding=1,
    )
    try:
        # First turn detects stale submitting state and dispatches reconciliation.
        assert runner.run_once() is True
        assert provider.reconciled.wait(timeout=5)
        assert provider.reconcile_calls == 1
        assert provider.submit_calls == 0

        # Second turn harvests the ambiguous reconciliation and parks the durable operation.
        assert runner.run_once() is True
        with get_session_factory()() as session:
            repository = VoiceDurableWorkingRepository(session)
            task = repository.get_task(task_id)
            operation = repository.get_operation(operation_id)
            assert task is not None and operation is not None
            assert task.status == "submit_outcome_unknown"
            assert task.next_reconcile_at is None
            assert operation.status == "running"
            assert operation.next_reconcile_at is None
            status = VoiceJobProjectionService(session).active_status(operation_id=operation_id)
            assert status.action == "submission_outcome_unknown"
            assert status.problem is not None
            assert status.problem["error_code"] == "VOICE_PROVIDER_SUBMISSION_OUTCOME_UNKNOWN"

        # NULL is parked for Voice, not due-now. Repeated host turns must do no
        # provider work and must not claim/release the same operation in a hot loop.
        for _ in range(8):
            assert runner.run_once() is False
        assert provider.reconcile_calls == 1
        assert provider.submit_calls == 0

        with get_session_factory()() as session:
            repository = VoiceDurableWorkingRepository(session)
            task = repository.get_task(task_id)
            operation = repository.get_operation(operation_id)
            assert task is not None and task.status == "submit_outcome_unknown"
            assert operation is not None and operation.next_reconcile_at is None
    finally:
        runner.close()
