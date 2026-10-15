from __future__ import annotations

import uuid

from sqlalchemy import text

from app.core.time import utc_now_iso
from app.db.session import get_session_factory
from app.modules.v4.voice_provider_action_controller import VoiceProviderActionController
from app.modules.v4.voice_provider_adapters import FakeAsyncProvider, FakeImmediateProvider
from app.modules.v4.voice_provider_contracts import (
    VoiceExecutionControl,
    VoiceProviderCapabilities,
    VoiceProviderHandle,
    VoiceProviderReconcileResult,
    VoiceProviderRegistry,
    VoiceProviderSubmission,
    VoiceSynthesisRequest,
)
from app.modules.v4.voice_provider_execution_pool import VoiceProviderExecutionPool
from app.repositories.voice_durable_working import VoiceDurableWorkingRepository
from tests.v4.test_voice_durable_provider_lifecycle import _seed_submitting_task, _wait_for_harvest


class CountingAsyncProvider(FakeAsyncProvider):
    provider_key = "fake_async_recovery"

    def __init__(self) -> None:
        super().__init__()
        self.submit_calls = 0

    def submit(self, request: VoiceSynthesisRequest, control: VoiceExecutionControl) -> VoiceProviderSubmission:
        self.submit_calls += 1
        return super().submit(request, control)


class AmbiguousNonIdempotentProvider(FakeImmediateProvider):
    provider_key = "fake_ambiguous_non_idempotent"

    def __init__(self) -> None:
        self.submit_calls = 0

    def capabilities(self) -> VoiceProviderCapabilities:
        return VoiceProviderCapabilities(
            execution_model="synchronous",
            supports_idempotency=False,
            supports_submission_lookup=False,
            supports_cancel=False,
        )

    def submit(self, request: VoiceSynthesisRequest, control: VoiceExecutionControl) -> VoiceProviderSubmission:
        self.submit_calls += 1
        return super().submit(request, control)

    def reconcile_submission(
        self,
        request: VoiceSynthesisRequest,
        handle: VoiceProviderHandle | None,
        control: VoiceExecutionControl,
    ) -> VoiceProviderReconcileResult:
        return VoiceProviderReconcileResult(status="ambiguous")


def _controller(provider) -> VoiceProviderActionController:
    registry = VoiceProviderRegistry()
    registry.register(provider)
    return VoiceProviderActionController(
        session_factory=get_session_factory(),
        registry=registry,
        pool=VoiceProviderExecutionPool(max_workers=2),
        poll_interval_seconds=1,
    )


def test_stale_submitting_is_discoverable_and_safe_replay_keeps_idempotency_identity() -> None:
    provider = FakeImmediateProvider()
    task_id, original_execution_token = _seed_submitting_task(provider)
    controller = _controller(provider)
    try:
        original_context = controller._context_required(task_id)
        original_key = controller._request(original_context).idempotency_key
        with get_session_factory()() as session:
            session.execute(
                text(
                    "UPDATE voice_segment_tasks SET submit_started_at = :stale_at "
                    "WHERE voice_segment_task_id = :task_id"
                ),
                {"task_id": task_id, "stale_at": "2000-01-01T00:00:00Z"},
            )
            session.commit()
            repository = VoiceDurableWorkingRepository(session)
            stale = repository.list_stale_submitting_tasks(
                operation_id=original_context.operation_id,
                stale_before_iso=utc_now_iso(),
            )
            assert [item.voice_segment_task_id for item in stale] == [task_id]

        controller.dispatch_reconcile_submission(task_id)
        reconciled = _wait_for_harvest(lambda: controller.harvest_reconcile_submission(task_id))
        assert reconciled.state == "queued"

        replay_execution_token = f"replay_{uuid.uuid4().hex}"
        with get_session_factory()() as session:
            repository = VoiceDurableWorkingRepository(session)
            task = repository.get_task(task_id)
            assert task is not None
            assert task.status == "queued"
            assert task.retry_count == 1
            assert task.execution_attempt_token is None
            assert repository.begin_task_submission(
                task_id=task_id,
                attempt_token=replay_execution_token,
                now_iso=utc_now_iso(),
            )
            session.commit()

        replay_context = controller._context_required(task_id)
        replay_key = controller._request(replay_context).idempotency_key
        assert original_execution_token != replay_execution_token
        assert original_key == replay_key

        controller.dispatch_submit(task_id)
        replayed = _wait_for_harvest(lambda: controller.harvest_submit(task_id))
        assert replayed.state == "materializing"
    finally:
        controller.close()


def test_async_lookup_recovers_accepted_submission_without_duplicate_submit() -> None:
    provider = CountingAsyncProvider()
    task_id, _ = _seed_submitting_task(provider)
    controller = _controller(provider)
    try:
        context = controller._context_required(task_id)
        submission = provider.submit(controller._request(context), controller._control(context))
        assert submission.status == "pending"
        assert provider.submit_calls == 1

        controller.dispatch_reconcile_submission(task_id)
        reconciled = _wait_for_harvest(lambda: controller.harvest_reconcile_submission(task_id))
        assert reconciled.state == "waiting_provider"
        assert provider.submit_calls == 1

        with get_session_factory()() as session:
            task = VoiceDurableWorkingRepository(session).get_task(task_id)
            assert task is not None
            assert task.status == "waiting_provider"
            assert task.provider_task_id == submission.handle.provider_task_id

        controller.dispatch_poll(task_id)
        completed = _wait_for_harvest(lambda: controller.harvest_poll(task_id))
        assert completed.state == "materializing"
        assert provider.submit_calls == 1
    finally:
        controller.close()


def test_non_idempotent_unknown_submission_is_not_blindly_replayed() -> None:
    provider = AmbiguousNonIdempotentProvider()
    task_id, _ = _seed_submitting_task(provider)
    controller = _controller(provider)
    try:
        controller.dispatch_reconcile_submission(task_id)
        reconciled = _wait_for_harvest(lambda: controller.harvest_reconcile_submission(task_id))
        assert reconciled.state == "submit_outcome_unknown"
        assert provider.submit_calls == 0

        with get_session_factory()() as session:
            task = VoiceDurableWorkingRepository(session).get_task(task_id)
            assert task is not None
            assert task.status == "submit_outcome_unknown"
            operation = VoiceDurableWorkingRepository(session).get_operation(task.operation_id)
            assert operation is not None
            assert operation.status == "running"
            assert operation.next_reconcile_at is None
    finally:
        controller.close()
