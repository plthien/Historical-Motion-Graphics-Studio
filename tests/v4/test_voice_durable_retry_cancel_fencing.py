from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import text

from app.api.errors import AppError
from app.core.time import utc_now_iso
from app.db.session import get_session_factory
from app.modules.v4.voice_durable_workflow import VoiceDurableWorkflowDriver
from app.modules.v4.voice_provider_action_controller import VoiceProviderActionController
from app.modules.v4.voice_provider_adapters import FakeImmediateProvider
from app.modules.v4.voice_provider_contracts import (
    VoiceExecutionControl,
    VoiceProviderProfile,
    VoiceProviderRegistry,
    VoiceProviderSubmission,
    VoiceSynthesisRequest,
)
from app.modules.v4.voice_provider_execution_pool import VoiceProviderExecutionPool
from app.repositories.voice_durable_working import VoiceDurableWorkingRepository
from tests.v4.test_voice_durable_provider_lifecycle import _seed_submitting_task, _wait_for_harvest


class TransientThenSuccessProvider(FakeImmediateProvider):
    provider_key = "fake_transient_then_success"

    def __init__(self) -> None:
        self.submit_calls = 0

    def submit(self, request: VoiceSynthesisRequest, control: VoiceExecutionControl) -> VoiceProviderSubmission:
        self.submit_calls += 1
        if self.submit_calls == 1:
            raise AppError("VOICE_PROVIDER_TRANSIENT", "Transient provider failure.", 503, retryable=True)
        return super().submit(request, control)


class MutableRuntimeTransientProvider(TransientThenSuccessProvider):
    provider_key = "fake_mutable_runtime_transient"

    def __init__(self) -> None:
        super().__init__()
        self.runtime_version = "v1"

    def execution_identity(self, profile: VoiceProviderProfile) -> dict[str, object]:
        _ = profile
        return {"schema_version": 1, "provider_key": self.provider_key, "runtime_version": self.runtime_version}


def _controller(provider) -> VoiceProviderActionController:
    registry = VoiceProviderRegistry()
    registry.register(provider)
    return VoiceProviderActionController(
        session_factory=get_session_factory(),
        registry=registry,
        pool=VoiceProviderExecutionPool(max_workers=2),
        poll_interval_seconds=1,
    )


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_transient_failure_retries_automatically_and_backoff_uses_retry_count() -> None:
    provider = TransientThenSuccessProvider()
    task_id, _ = _seed_submitting_task(provider)
    with get_session_factory()() as session:
        session.execute(
            text("UPDATE voice_segment_tasks SET attempt_no = 9 WHERE voice_segment_task_id = :task_id"),
            {"task_id": task_id},
        )
        session.commit()

    controller = _controller(provider)
    try:
        controller.dispatch_submit(task_id)
        failed = _wait_for_harvest(lambda: controller.harvest_submit(task_id))
        assert failed.state == "queued"
        assert provider.submit_calls == 1

        with get_session_factory()() as session:
            row = session.execute(
                text(
                    "SELECT status, retry_count, attempt_no, next_reconcile_at, last_retry_at, "
                    "execution_attempt_token FROM voice_segment_tasks WHERE voice_segment_task_id = :task_id"
                ),
                {"task_id": task_id},
            ).mappings().one()
            assert row["status"] == "queued"
            assert row["retry_count"] == 1
            assert row["attempt_no"] == 9
            assert row["execution_attempt_token"] is None
            delay = (_parse(row["next_reconcile_at"]) - _parse(row["last_retry_at"])).total_seconds()
            assert 1.5 <= delay <= 2.5
            repository = VoiceDurableWorkingRepository(session)
            assert repository.begin_task_submission(
                task_id=task_id,
                attempt_token=f"retry_{uuid.uuid4().hex}",
                now_iso=utc_now_iso(),
            )
            session.commit()

        controller.dispatch_submit(task_id)
        recovered = _wait_for_harvest(lambda: controller.harvest_submit(task_id))
        assert recovered.state == "materializing"
        assert provider.submit_calls == 2
    finally:
        controller.close()


def test_retry_after_provider_runtime_change_fails_without_second_submit() -> None:
    provider = MutableRuntimeTransientProvider()
    task_id, _ = _seed_submitting_task(provider)
    controller = _controller(provider)
    try:
        controller.dispatch_submit(task_id)
        failed = _wait_for_harvest(lambda: controller.harvest_submit(task_id))
        assert failed.state == "queued"
        assert provider.submit_calls == 1

        provider.runtime_version = "v2"
        with get_session_factory()() as session:
            repository = VoiceDurableWorkingRepository(session)
            assert repository.begin_task_submission(
                task_id=task_id,
                attempt_token=f"runtime_changed_{uuid.uuid4().hex}",
                now_iso=utc_now_iso(),
            )
            session.commit()

        controller.dispatch_submit(task_id)
        runtime_failed = _wait_for_harvest(lambda: controller.harvest_submit(task_id))

        assert runtime_failed.state == "failed"
        assert provider.submit_calls == 1
        with get_session_factory()() as session:
            task = VoiceDurableWorkingRepository(session).get_task(task_id)
            assert task is not None
            assert task.status == "failed"
            error_code = session.execute(
                text("SELECT error_code FROM voice_segment_tasks WHERE voice_segment_task_id = :task_id"),
                {"task_id": task_id},
            ).scalar_one()
            assert error_code == "VOICE_PROVIDER_RUNTIME_CHANGED"
    finally:
        controller.close()


def test_retryable_provider_failure_respects_max_attempts() -> None:
    provider = TransientThenSuccessProvider()
    task_id, _ = _seed_submitting_task(provider)
    with get_session_factory()() as session:
        session.execute(
            text("UPDATE voice_segment_tasks SET max_attempts = 1 WHERE voice_segment_task_id = :task_id"),
            {"task_id": task_id},
        )
        session.commit()

    controller = _controller(provider)
    try:
        controller.dispatch_submit(task_id)
        failed = _wait_for_harvest(lambda: controller.harvest_submit(task_id))
        assert failed.state == "failed"
        with get_session_factory()() as session:
            task = VoiceDurableWorkingRepository(session).get_task(task_id)
            assert task is not None
            assert task.status == "failed"
            operation = VoiceDurableWorkingRepository(session).get_operation(task.operation_id)
            assert operation is not None
            assert operation.status == "failed"
    finally:
        controller.close()


def test_late_result_from_superseded_attempt_is_fenced_and_cleaned() -> None:
    provider = FakeImmediateProvider()
    task_id, old_token = _seed_submitting_task(provider)
    controller = _controller(provider)
    try:
        old_context = controller._context_required(task_id)
        submission = provider.submit(controller._request(old_context), controller._control(old_context))
        assert submission.result is not None
        late_path = submission.result.audio_file_path
        assert late_path.exists()

        with get_session_factory()() as session:
            session.execute(
                text(
                    "UPDATE voice_segment_tasks SET status = 'queued', submit_attempt_token = NULL, "
                    "submit_started_at = NULL, execution_attempt_token = NULL, execution_started_at = NULL "
                    "WHERE voice_segment_task_id = :task_id AND execution_attempt_token = :old_token"
                ),
                {"task_id": task_id, "old_token": old_token},
            )
            repository = VoiceDurableWorkingRepository(session)
            new_token = f"new_{uuid.uuid4().hex}"
            assert repository.begin_task_submission(task_id=task_id, attempt_token=new_token, now_iso=utc_now_iso())
            session.commit()

        accepted = controller._accept_execution_result(old_context, submission.result)
        assert accepted.state == "stale"
        assert not late_path.exists()
        with get_session_factory()() as session:
            task = VoiceDurableWorkingRepository(session).get_task(task_id)
            assert task is not None
            assert task.status == "submitting"
            assert task.execution_attempt_token != old_token
    finally:
        controller.close()


def test_cancellation_survives_controller_restart_and_late_result_cannot_publish() -> None:
    provider = FakeImmediateProvider()
    task_id, old_token = _seed_submitting_task(provider)
    controller = _controller(provider)
    old_context = controller._context_required(task_id)
    submission = provider.submit(controller._request(old_context), controller._control(old_context))
    assert submission.result is not None
    late_path = submission.result.audio_file_path
    operation_id = old_context.operation_id
    controller.close()

    with get_session_factory()() as session:
        repository = VoiceDurableWorkingRepository(session)
        result = VoiceDurableWorkflowDriver(repository).cancel_operation(operation_id=operation_id, now_iso=utc_now_iso())
        assert result["status"] == "cancelled"
        session.commit()

    restarted = _controller(provider)
    try:
        assert restarted._control(old_context).cancellation_requested()
        accepted = restarted._accept_execution_result(old_context, submission.result)
        assert accepted.state == "stale"
        assert not late_path.exists()
        with get_session_factory()() as session:
            task = VoiceDurableWorkingRepository(session).get_task(task_id)
            assert task is not None
            assert task.status == "cancelled"
            assert task.execution_attempt_token == old_token
            operation = VoiceDurableWorkingRepository(session).get_operation(operation_id)
            assert operation is not None
            assert operation.status == "cancelled"
            assert operation.cancel_requested_at is not None
    finally:
        restarted.close()
