from __future__ import annotations

from pathlib import Path
from threading import Event

from sqlalchemy import text

from app.core.time import utc_now_iso
from app.db.session import get_session_factory
from app.modules.v4.voice_provider_action_controller import VoiceProviderActionController as CanonicalController
from app.modules.v4.voice_provider_adapters import FakeImmediateProvider
from app.modules.v4.voice_provider_contracts import (
    VoiceProviderCancelResult,
    VoiceProviderCapabilities,
    VoiceProviderRegistry,
)
from app.modules.v4.voice_provider_execution_pool import VoiceProviderExecutionPool
from app.repositories.voice_durable_working import VoiceDurableWorkingRepository
from app.repositories.voice_provider_execution import VoiceProviderExecutionRepository
from app.voice_coordinator_runner import VoiceProviderActionController as RunnerController
from tests.v4.test_voice_durable_provider_lifecycle import _seed_submitting_task


class CancelTrackingProvider(FakeImmediateProvider):
    provider_key = "cancel_tracking"

    def __init__(self) -> None:
        self.cancel_called = Event()

    def capabilities(self) -> VoiceProviderCapabilities:
        return VoiceProviderCapabilities(
            execution_model="blocking",
            supports_idempotency=True,
            supports_cancel=True,
        )

    def cancel(self, handle, control) -> VoiceProviderCancelResult:
        # A durable cancel is expected to be visible through the same DB-backed
        # execution control used by production adapters.
        assert control.cancellation_requested() is True
        self.cancel_called.set()
        return VoiceProviderCancelResult(status="accepted")


def test_cancel_requested_fences_provider_result_before_materialization() -> None:
    provider = FakeImmediateProvider()
    task_id, execution_token = _seed_submitting_task(provider)
    with get_session_factory()() as session:
        operation_id = str(
            session.execute(
                text("SELECT operation_id FROM voice_segment_tasks WHERE voice_segment_task_id=:task_id"),
                {"task_id": task_id},
            ).scalar_one()
        )
        repository = VoiceDurableWorkingRepository(session)
        assert repository.request_cancel(operation_id=operation_id, now_iso=utc_now_iso())
        session.commit()

    with get_session_factory()() as session:
        repository = VoiceProviderExecutionRepository(session)
        changed = repository.persist_completed_provider_result(
            task_id=task_id,
            execution_attempt_token=execution_token,
            provider_task_id="late-provider-result",
            result_metadata={"durable_provider_result": {"audio_file_path": "/tmp/never-authoritative.wav"}},
            now_iso=utc_now_iso(),
        )
        assert changed is False
        session.rollback()

    with get_session_factory()() as session:
        row = session.execute(
            text(
                "SELECT status, provider_result_metadata_json FROM voice_segment_tasks "
                "WHERE voice_segment_task_id=:task_id"
            ),
            {"task_id": task_id},
        ).mappings().one()
        assert row["status"] == "submitting"
        assert row["provider_result_metadata_json"] is None


def test_best_effort_provider_cancel_runs_after_durable_cancel_is_persisted() -> None:
    provider = CancelTrackingProvider()
    task_id, _execution_token = _seed_submitting_task(provider)
    with get_session_factory()() as session:
        operation_id = str(
            session.execute(
                text("SELECT operation_id FROM voice_segment_tasks WHERE voice_segment_task_id=:task_id"),
                {"task_id": task_id},
            ).scalar_one()
        )
        repository = VoiceDurableWorkingRepository(session)
        assert repository.request_cancel(operation_id=operation_id, now_iso=utc_now_iso())
        session.commit()

    registry = VoiceProviderRegistry()
    registry.register(provider)
    controller = CanonicalController(
        session_factory=get_session_factory(),
        registry=registry,
        pool=VoiceProviderExecutionPool(max_workers=1, max_outstanding=1),
    )
    try:
        dispatched = controller.best_effort_cancel(task_id)
        assert dispatched.changed is True
        assert dispatched.state == "running"
        assert provider.cancel_called.wait(timeout=5)
    finally:
        controller.close()


def test_production_runner_uses_the_single_canonical_provider_controller() -> None:
    assert RunnerController is CanonicalController
    assert not Path("app/modules/v4/voice_provider_controller.py").exists()
