from __future__ import annotations

from threading import Event, Lock
from time import monotonic, sleep

from sqlalchemy import text

from app.api.errors import AppError
from app.core.config import get_settings
from app.core.time import utc_now_iso
from app.db.session import get_session_factory
from app.modules.v4.voice_durable_workflow import VoiceDurableWorkflowDriver
from app.modules.v4.voice_provider_action_controller import VoiceProviderActionController
from app.modules.v4.voice_provider_adapters import FakeImmediateProvider
from app.modules.v4.voice_provider_contracts import (
    VoiceExecutionControl,
    VoiceProviderCapabilities,
    VoiceProviderRegistry,
    VoiceProviderSubmission,
    VoiceSynthesisRequest,
)
from app.modules.v4.voice_provider_execution_pool import VoiceProviderExecutionPool
from app.repositories.voice_durable_working import VoiceDurableWorkingRepository
from tests.v4.test_voice_durable_provider_lifecycle import _seed_submitting_task, _wait_for_harvest


class InterruptibleFirstSubmitProvider(FakeImmediateProvider):
    provider_key = "interruptible_first_submit"

    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()
        self._lock = Lock()
        self._current: tuple[str, str] | None = None
        self._reason: str | None = None
        self.calls = 0

    def capabilities(self) -> VoiceProviderCapabilities:
        return VoiceProviderCapabilities(
            execution_model="blocking",
            supports_idempotency=True,
            supports_cancel=True,
            supports_execution_interrupt=True,
        )

    def submit(self, request: VoiceSynthesisRequest, control: VoiceExecutionControl) -> VoiceProviderSubmission:
        self.calls += 1
        if self.calls > 1:
            return super().submit(request, control)
        with self._lock:
            self._current = (request.segment_task_id, request.execution_attempt_token)
        self.started.set()
        assert self.release.wait(timeout=10)
        with self._lock:
            reason = self._reason
        if reason == "timeout":
            raise AppError("TTS_PROVIDER_TIMEOUT", "controlled timeout", 504, retryable=True)
        if reason == "cancelled":
            raise AppError("VOICE_OPERATION_CANCELLED", "controlled cancellation", 409, retryable=False)
        return super().submit(request, control)

    def interrupt_execution(self, *, segment_task_id: str, execution_attempt_token: str, reason: str) -> bool:
        with self._lock:
            if self._current != (segment_task_id, execution_attempt_token):
                return False
            self._reason = reason
        self.release.set()
        return True


class DelayedRegistrationCancelProvider(FakeImmediateProvider):
    provider_key = "delayed_registration_cancel"

    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()
        self.calls = 0

    def capabilities(self) -> VoiceProviderCapabilities:
        return VoiceProviderCapabilities(
            execution_model="blocking",
            supports_idempotency=True,
            supports_cancel=True,
            supports_execution_interrupt=True,
        )

    def submit(self, request: VoiceSynthesisRequest, control: VoiceExecutionControl) -> VoiceProviderSubmission:
        self.calls += 1
        if self.calls > 1:
            return super().submit(request, control)
        self.started.set()
        assert self.release.wait(timeout=10)
        if control.cancellation_requested():
            raise AppError("VOICE_OPERATION_CANCELLED", "controlled cancellation", 409, retryable=False)
        return super().submit(request, control)

    def interrupt_execution(self, *, segment_task_id: str, execution_attempt_token: str, reason: str) -> bool:
        # Simulates the pre-registration race: the action owns the provider slot,
        # but there is not yet an interruptible subprocess handle.
        return False


def _controller(provider, *, timeout_seconds: int = 1) -> VoiceProviderActionController:
    registry = VoiceProviderRegistry()
    registry.register(provider)
    settings = get_settings().model_copy(update={"tts_provider_timeout_seconds": timeout_seconds})
    return VoiceProviderActionController(
        session_factory=get_session_factory(),
        registry=registry,
        pool=VoiceProviderExecutionPool(max_workers=1, max_outstanding=1),
        settings=settings,
    )


def test_hard_watchdog_interrupts_stuck_submit_and_schedules_retry() -> None:
    provider = InterruptibleFirstSubmitProvider()
    task_id, _ = _seed_submitting_task(provider)
    controller = _controller(provider, timeout_seconds=1)
    try:
        assert controller.dispatch_submit(task_id).changed
        assert provider.started.wait(timeout=5)
        with get_session_factory()() as session:
            session.execute(
                text("UPDATE voice_segment_tasks SET execution_started_at='2000-01-01T00:00:00+00:00' WHERE voice_segment_task_id=:task_id"),
                {"task_id": task_id},
            )
            session.commit()

        first = controller.harvest_submit(task_id)
        assert first.state == "running"
        settled = _wait_for_harvest(lambda: controller.harvest_submit(task_id))
        assert settled.state == "queued"
        assert controller.pool.outstanding_count == 0
        with get_session_factory()() as session:
            row = session.execute(
                text("SELECT status,retry_count,error_code,next_reconcile_at FROM voice_segment_tasks WHERE voice_segment_task_id=:task_id"),
                {"task_id": task_id},
            ).mappings().one()
            assert row["status"] == "queued"
            assert row["retry_count"] == 1
            assert row["error_code"] == "TTS_PROVIDER_TIMEOUT"
            assert row["next_reconcile_at"] is not None
    finally:
        provider.release.set()
        controller.close()


def test_cancel_retires_occupied_submit_slot_so_next_generation_can_start_without_host_restart() -> None:
    provider = InterruptibleFirstSubmitProvider()
    first_task, _ = _seed_submitting_task(provider)
    controller = _controller(provider, timeout_seconds=60)
    try:
        assert controller.dispatch_submit(first_task).changed
        assert provider.started.wait(timeout=5)
        assert controller.pool.outstanding_count == 1
        old_context = controller._context_required(first_task)

        with get_session_factory()() as session:
            repository = VoiceDurableWorkingRepository(session)
            result = VoiceDurableWorkflowDriver(repository).cancel_operation(
                operation_id=old_context.operation_id,
                now_iso=utc_now_iso(),
            )
            assert result["status"] == "cancelled"
            session.commit()

        retired = controller.interrupt_and_retire_submit(first_task, reason="cancelled", settle_grace_seconds=2.0)
        assert retired.state == "retired"
        assert controller.pool.outstanding_count == 0

        second_task, _ = _seed_submitting_task(provider)
        dispatched = controller.dispatch_submit(second_task)
        assert dispatched.changed is True
        completed = _wait_for_harvest(lambda: controller.harvest_submit(second_task))
        assert completed.state == "materializing"
        assert controller.pool.outstanding_count == 0
    finally:
        provider.release.set()
        controller.close()


def test_cancel_race_before_interrupt_registration_is_reaped_after_settle() -> None:
    provider = DelayedRegistrationCancelProvider()
    task_id, _ = _seed_submitting_task(provider)
    controller = _controller(provider, timeout_seconds=60)
    try:
        assert controller.dispatch_submit(task_id).changed
        assert provider.started.wait(timeout=5)
        context = controller._context_required(task_id)
        with get_session_factory()() as session:
            repository = VoiceDurableWorkingRepository(session)
            result = VoiceDurableWorkflowDriver(repository).cancel_operation(
                operation_id=context.operation_id,
                now_iso=utc_now_iso(),
            )
            assert result["status"] == "cancelled"
            session.commit()

        retired = controller.interrupt_and_retire_submit(
            task_id,
            reason="cancelled",
            settle_grace_seconds=0.0,
        )
        assert retired.state == "still_running"
        assert controller.pool.outstanding_count == 1

        provider.release.set()
        deadline = monotonic() + 5.0
        while monotonic() < deadline and controller.pool.outstanding_count:
            controller.reap_background_actions()
            sleep(0.01)
        assert controller.pool.outstanding_count == 0

        second_task, _ = _seed_submitting_task(provider)
        assert controller.dispatch_submit(second_task).changed is True
        completed = _wait_for_harvest(lambda: controller.harvest_submit(second_task))
        assert completed.state == "materializing"
    finally:
        provider.release.set()
        controller.close()
