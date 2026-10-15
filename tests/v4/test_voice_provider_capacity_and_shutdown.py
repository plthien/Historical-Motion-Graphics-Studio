from __future__ import annotations

from threading import Event
from time import monotonic, sleep

import pytest
from sqlalchemy import text

from app.core.time import utc_now_iso
from app.db.session import get_session_factory
from app.modules.v4.voice_provider_adapters import FakeAsyncProvider
from app.modules.v4.voice_provider_contracts import (
    VoiceProviderPollResult,
    VoiceProviderReconcileResult,
    VoiceProviderRegistry,
)
from app.modules.v4.voice_provider_execution_pool import VoiceProviderExecutionPool
from app.voice_coordinator_runner import VoiceCoordinatorRunner
from tests.v4.test_voice_durable_provider_lifecycle import _seed_submitting_task, _wait_for_harvest


class ControlledAsyncProvider(FakeAsyncProvider):
    provider_key = "controlled_async_capacity"

    def __init__(self) -> None:
        super().__init__()
        self.poll_calls = 0
        self.poll_started = Event()
        self.reconcile_calls = 0
        self.reconcile_started = Event()

    def poll(self, handle, control) -> VoiceProviderPollResult:
        self.poll_calls += 1
        self.poll_started.set()
        return super().poll(handle, control)

    def reconcile_submission(self, request, handle, control) -> VoiceProviderReconcileResult:
        self.reconcile_calls += 1
        self.reconcile_started.set()
        return VoiceProviderReconcileResult(status="replay_safe")


def _force_only_operation_due(task_id: str) -> str:
    due = "1900-01-01T00:00:00+00:00"
    with get_session_factory()() as session:
        operation_id = str(
            session.execute(
                text("SELECT operation_id FROM voice_segment_tasks WHERE voice_segment_task_id=:task_id"),
                {"task_id": task_id},
            ).scalar_one()
        )
        # These tests share the repository fixture with the focused Voice suite.
        # Park every other active operation so claim order cannot depend on state
        # left by an earlier behavioral case.
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
            text("UPDATE voice_segment_tasks SET next_reconcile_at=:due WHERE voice_segment_task_id=:task_id"),
            {"task_id": task_id, "due": due},
        )
        session.execute(
            text(
                """
                UPDATE voice_generation_operations
                SET status='running', next_reconcile_at=:due,
                    reconcile_owner=NULL, reconcile_token=NULL, reconcile_lease_expires_at=NULL
                WHERE operation_id=:operation_id
                """
            ),
            {"operation_id": operation_id, "due": due},
        )
        session.commit()
    return operation_id


def _operation_due(operation_id: str) -> str | None:
    with get_session_factory()() as session:
        return session.execute(
            text("SELECT next_reconcile_at FROM voice_generation_operations WHERE operation_id=:operation_id"),
            {"operation_id": operation_id},
        ).scalar_one()


def _occupy_pool(pool: VoiceProviderExecutionPool[object]) -> tuple[Event, Event]:
    started = Event()
    release = Event()

    def blocking() -> object:
        started.set()
        release.wait(timeout=10)
        return object()

    reservation = pool.try_reserve()
    assert reservation is not None
    assert pool.dispatch_reserved("capacity-blocker", reservation, blocking)
    assert started.wait(timeout=5)
    return started, release


def _wait_until_completed(pool: VoiceProviderExecutionPool[object], key: str) -> None:
    deadline = monotonic() + 5
    while monotonic() < deadline:
        if pool.take_completed(key) is not None:
            return
        sleep(0.01)
    raise AssertionError(f"provider pool action {key!r} did not complete")


def test_due_poll_is_deferred_without_provider_call_when_capacity_is_full() -> None:
    provider = ControlledAsyncProvider()
    task_id, _ = _seed_submitting_task(provider)
    registry = VoiceProviderRegistry()
    registry.register(provider)
    runner = VoiceCoordinatorRunner(
        get_session_factory(), registry=registry, provider_pool_workers=1, provider_pool_max_outstanding=1
    )
    try:
        assert runner.provider.dispatch_submit(task_id).changed
        submission = _wait_for_harvest(lambda: runner.provider.harvest_submit(task_id))
        assert submission.state == "waiting_provider"
        operation_id = _force_only_operation_due(task_id)

        _, release = _occupy_pool(runner.pool)
        assert runner.run_once() is False
        assert provider.poll_calls == 0
        deferred_until = _operation_due(operation_id)
        assert deferred_until is not None and deferred_until > utc_now_iso()

        # The operation was pushed into the future, so immediate host turns do
        # not reclaim it while global provider capacity is still occupied.
        for _ in range(5):
            assert runner.run_once() is False
        assert provider.poll_calls == 0

        release.set()
        _wait_until_completed(runner.pool, "capacity-blocker")
        _force_only_operation_due(task_id)
        assert runner.run_once() is True
        assert provider.poll_started.wait(timeout=5)
        assert provider.poll_calls == 1
    finally:
        runner.close()


def test_stale_submission_reconcile_is_deferred_when_capacity_is_full() -> None:
    provider = ControlledAsyncProvider()
    task_id, _ = _seed_submitting_task(provider)
    operation_id = _force_only_operation_due(task_id)
    with get_session_factory()() as session:
        session.execute(
            text(
                "UPDATE voice_segment_tasks SET submit_started_at='2000-01-01T00:00:00+00:00' "
                "WHERE voice_segment_task_id=:task_id"
            ),
            {"task_id": task_id},
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
        _, release = _occupy_pool(runner.pool)
        assert runner.run_once() is False
        assert provider.reconcile_calls == 0
        deferred_until = _operation_due(operation_id)
        assert deferred_until is not None and deferred_until > utc_now_iso()
        for _ in range(5):
            assert runner.run_once() is False
        assert provider.reconcile_calls == 0

        release.set()
        _wait_until_completed(runner.pool, "capacity-blocker")
        _force_only_operation_due(task_id)
        assert runner.run_once() is True
        assert provider.reconcile_started.wait(timeout=5)
        assert provider.reconcile_calls == 1
    finally:
        runner.close()


def test_pool_shutdown_is_bounded_and_cancels_not_started_work() -> None:
    pool: VoiceProviderExecutionPool[str] = VoiceProviderExecutionPool(max_workers=1, max_outstanding=2)
    first_started = Event()
    release_first = Event()
    second_started = Event()

    def first() -> str:
        first_started.set()
        release_first.wait(timeout=10)
        return "first"

    def second() -> str:
        second_started.set()
        return "second"

    first_reservation = pool.try_reserve()
    second_reservation = pool.try_reserve()
    assert first_reservation is not None and second_reservation is not None
    assert pool.dispatch_reserved("first", first_reservation, first)
    assert first_started.wait(timeout=5)
    assert pool.dispatch_reserved("second", second_reservation, second)

    started_at = monotonic()
    report = pool.close(wait=True, grace_seconds=0.05)
    elapsed = monotonic() - started_at
    assert elapsed < 1.0
    assert report.timed_out is True
    assert report.running_count == 1
    assert report.cancelled_count == 1
    assert second_started.is_set() is False
    assert pool.closed is True
    with pytest.raises(RuntimeError, match="closed"):
        pool.try_reserve()

    release_first.set()
    _wait_until_completed(pool, "first")


def test_pool_shutdown_drains_cooperative_action_within_grace() -> None:
    pool: VoiceProviderExecutionPool[str] = VoiceProviderExecutionPool(max_workers=1, max_outstanding=1)
    finished = Event()
    reservation = pool.try_reserve()
    assert reservation is not None

    def cooperative() -> str:
        finished.set()
        return "done"

    assert pool.dispatch_reserved("cooperative", reservation, cooperative)
    assert finished.wait(timeout=5)
    report = pool.close(wait=True, grace_seconds=1.0)
    assert report.timed_out is False
    assert pool.take_completed("cooperative") == "done"
