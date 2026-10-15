from __future__ import annotations

from threading import Event, Lock

from sqlalchemy import text

from app.core.time import utc_now_iso
from app.db.session import get_session_factory
from app.modules.media.durable_workflow.host import DurableMediaWorkflowHost, RegisteredDurableWorkflow
from app.modules.v4.voice_provider_adapters import FakeImmediateProvider
from app.modules.v4.voice_provider_contracts import (
    VoiceExecutionControl,
    VoiceProviderCapabilities,
    VoiceProviderRegistry,
    VoiceProviderSubmission,
    VoiceSynthesisRequest,
)
from app.modules.v4.voice_provider_execution_pool import VoiceProviderExecutionPool
from app.voice_coordinator_runner import VoiceCoordinatorRunner
from tests.v4.test_voice_durable_provider_lifecycle import _seed_submitting_task


class ControlledBlockingProvider(FakeImmediateProvider):
    provider_key = "controlled_blocking"

    def __init__(self) -> None:
        self.first_started = Event()
        self.second_started = Event()
        self.release_first = Event()
        self.release_second = Event()
        self.first_finished = Event()
        self.second_finished = Event()
        self.controls: list[VoiceExecutionControl] = []
        self._lock = Lock()
        self._calls = 0

    def capabilities(self) -> VoiceProviderCapabilities:
        return VoiceProviderCapabilities(
            execution_model="blocking",
            supports_idempotency=True,
            supports_cancel=True,
        )

    def submit(self, request: VoiceSynthesisRequest, control: VoiceExecutionControl) -> VoiceProviderSubmission:
        with self._lock:
            self._calls += 1
            call = self._calls
            self.controls.append(control)
        started = self.first_started if call == 1 else self.second_started
        release = self.release_first if call == 1 else self.release_second
        finished = self.first_finished if call == 1 else self.second_finished
        started.set()
        if not release.wait(timeout=10):
            raise AssertionError("controlled provider was not released")
        result = super().submit(request, control)
        finished.set()
        return result


class FakeMonotonicClock:
    def __init__(self) -> None:
        self.value = 0.0
        self._lock = Lock()

    def __call__(self) -> float:
        with self._lock:
            return self.value

    def advance(self, seconds: float) -> None:
        with self._lock:
            self.value += seconds


class CountingRunner:
    def __init__(self) -> None:
        self.turns = 0

    def run_once(self) -> bool:
        self.turns += 1
        return True


def _reset_to_queued(task_id: str, *, due: str) -> str:
    with get_session_factory()() as session:
        operation_id = session.execute(
            text("SELECT operation_id FROM voice_segment_tasks WHERE voice_segment_task_id = :task_id"),
            {"task_id": task_id},
        ).scalar_one()
        session.execute(
            text(
                """
                UPDATE voice_segment_tasks
                SET status = 'queued',
                    submit_attempt_token = NULL,
                    submit_started_at = NULL,
                    execution_attempt_token = NULL,
                    execution_started_at = NULL,
                    next_reconcile_at = :due
                WHERE voice_segment_task_id = :task_id
                """
            ),
            {"task_id": task_id, "due": due},
        )
        session.execute(
            text(
                """
                UPDATE voice_generation_operations
                SET status = 'running', next_reconcile_at = :due,
                    reconcile_owner = NULL, reconcile_token = NULL,
                    reconcile_lease_expires_at = NULL
                WHERE operation_id = :operation_id
                """
            ),
            {"operation_id": operation_id, "due": due},
        )
        session.commit()
    return str(operation_id)


def _isolate_due_operations(first_operation: str, second_operation: str) -> None:
    with get_session_factory()() as session:
        session.execute(
            text(
                """
                UPDATE voice_generation_operations
                SET next_reconcile_at='2099-01-01T00:00:00+00:00',
                    reconcile_owner=NULL, reconcile_token=NULL, reconcile_lease_expires_at=NULL
                WHERE operation_id NOT IN (:first_operation, :second_operation)
                  AND status IN ('queued','running','finalizing')
                """
            ),
            {"first_operation": first_operation, "second_operation": second_operation},
        )
        session.commit()


def _task_status(task_id: str) -> tuple[str, str | None]:
    with get_session_factory()() as session:
        row = session.execute(
            text(
                "SELECT status, execution_started_at FROM voice_segment_tasks "
                "WHERE voice_segment_task_id=:task_id"
            ),
            {"task_id": task_id},
        ).mappings().one()
        return str(row["status"]), row["execution_started_at"]


def test_pool_reservation_strictly_bounds_outstanding_actions() -> None:
    pool: VoiceProviderExecutionPool[str] = VoiceProviderExecutionPool(max_workers=1, max_outstanding=1)
    release = Event()
    started = Event()

    def blocking() -> str:
        started.set()
        assert release.wait(timeout=10)
        return "done"

    first = pool.try_reserve()
    assert first is not None
    assert pool.outstanding_count == 1
    assert pool.try_reserve() is None
    assert pool.dispatch_reserved("first", first, blocking)
    assert started.wait(timeout=5)
    assert pool.outstanding_count == 1
    assert pool.try_reserve() is None
    release.set()
    pool.close(wait=True)
    assert pool.take_completed("first") == "done"


def test_runner_does_not_mark_second_task_submitting_until_capacity_and_timeout_starts_on_execution() -> None:
    provider = ControlledBlockingProvider()
    first_task, _ = _seed_submitting_task(provider)
    second_task, _ = _seed_submitting_task(provider)
    first_operation = _reset_to_queued(first_task, due="2000-01-01T00:00:00+00:00")
    second_operation = _reset_to_queued(second_task, due="2000-01-02T00:00:00+00:00")
    _isolate_due_operations(first_operation, second_operation)

    registry = VoiceProviderRegistry()
    registry.register(provider)
    runner = VoiceCoordinatorRunner(
        get_session_factory(),
        registry=registry,
        provider_pool_workers=1,
        provider_pool_max_outstanding=1,
    )
    clock = FakeMonotonicClock()
    runner.provider.monotonic_clock = clock
    try:
        assert runner.run_once() is True
        assert provider.first_started.wait(timeout=5)
        first_status, first_started_at = _task_status(first_task)
        assert first_status == "submitting"
        assert first_started_at is not None
        assert runner.pool.outstanding_count == 1

        # Operation two is earlier than the refreshed first operation, so the
        # runner sees it while provider capacity is occupied. It must stay queued.
        assert runner.run_once() is False
        second_status, second_started_at = _task_status(second_task)
        assert second_status == "queued"
        assert second_started_at is None
        assert not provider.second_started.is_set()
        assert runner.pool.outstanding_count == 1

        # Time spent waiting for capacity must not consume the second action's
        # provider timeout because its execution control does not exist yet.
        clock.advance(10_000)
        assert len(provider.controls) == 1

        provider.release_first.set()
        assert provider.first_finished.wait(timeout=5)
        harvested = runner.provider.harvest_submit(first_task)
        assert harvested.state == "materializing"
        assert runner.pool.outstanding_count == 0

        # Remove the first operation from scheduling so the deferred second
        # operation receives the next turn deterministically.
        with get_session_factory()() as session:
            session.execute(
                text("UPDATE voice_generation_operations SET status='failed', next_reconcile_at=NULL WHERE operation_id=:id"),
                {"id": first_operation},
            )
            session.execute(
                text("UPDATE voice_generation_operations SET next_reconcile_at=:now WHERE operation_id=:id"),
                {"id": second_operation, "now": utc_now_iso()},
            )
            session.commit()

        assert runner.run_once() is True
        assert provider.second_started.wait(timeout=5)
        assert len(provider.controls) == 2
        assert provider.controls[1].deadline_reached() is False
        second_status, second_started_at = _task_status(second_task)
        assert second_status == "submitting"
        assert second_started_at is not None

        # Blocking provider work runs in the bounded execution pool, so the
        # shared host can still give Scene Assets and Visual References turns.
        scene = CountingRunner()
        visual = CountingRunner()
        host = DurableMediaWorkflowHost(
            (
                RegisteredDurableWorkflow("scene-assets-v2", scene),
                RegisteredDurableWorkflow("visual-references-v2", visual),
                RegisteredDurableWorkflow("voice", runner),
            )
        )
        host.run_once()
        assert scene.turns == 1
        assert visual.turns == 1
        assert provider.second_started.is_set()
    finally:
        provider.release_first.set()
        provider.release_second.set()
        runner.close()
