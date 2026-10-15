from __future__ import annotations

import inspect
from types import SimpleNamespace

from app.modules.v4.visual_reference_provider_driver import VisualReferenceDurableProviderExecutor
from app.repositories.visual_reference_working import VisualReferenceReconcileLease
from app.visual_reference_coordinator_runner import VisualReferenceCoordinatorRunner


class _Coordinator:
    def __init__(self, *, action: str, operation_task_id: str | None = "vr_task_1") -> None:
        self.action = action
        self.operation_task_id = operation_task_id
        self.events: list[str] = []

    def claim_due_operation(self, **_kwargs):
        self.events.append("claim")
        return VisualReferenceReconcileLease(
            operation_id="vr_operation_1",
            owner="runner",
            token="lease-token",
            expires_at="2099-01-01T00:00:00+00:00",
        )

    def plan_next_action(self, lease):
        self.events.append("plan")
        return SimpleNamespace(
            action=self.action,
            operation_id=lease.operation_id,
            reconcile_token=lease.token,
            operation_anchor_id="vr_anchor_op_1",
            operation_task_id=self.operation_task_id,
            task_status="queued",
            next_reconcile_at=None,
        )

    def release_lease(self, _lease, **_kwargs):
        self.events.append("release")
        return True

    def refresh_local_aggregate(self, _lease):
        self.events.append("finalize")
        return "completed"

    def cancel_operation(self, *, operation_id: str):
        self.events.append(f"cancel:{operation_id}")
        return {"operation_id": operation_id, "status": "cancelled"}


class _Provider:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def submit_task(self, _lease, *, operation_task_id: str):
        self.events.append(f"submit:{operation_task_id}")
        return object()

    def resume_or_advance_task(self, *, operation_task_id: str, now_iso: str | None = None):
        del now_iso
        self.events.append(f"resume:{operation_task_id}")
        return object()


def _runner(action: str) -> tuple[VisualReferenceCoordinatorRunner, _Coordinator]:
    runner = VisualReferenceCoordinatorRunner(lambda: None, owner="runner")
    coordinator = _Coordinator(action=action)
    runner.coordinator = coordinator
    runner.provider = _Provider(coordinator.events)
    runner._repair_unscheduled_active_operations = lambda: 0  # type: ignore[method-assign]
    return runner, coordinator


def test_submit_action_uses_visual_reference_shared_provider_facade() -> None:
    runner, coordinator = _runner("submit")

    assert runner.run_once() is True
    assert coordinator.events == ["claim", "plan", "submit:vr_task_1"]


def test_poll_action_releases_reconcile_lease_before_provider_io() -> None:
    runner, coordinator = _runner("poll")

    assert runner.run_once() is True
    assert coordinator.events == ["claim", "plan", "release", "resume:vr_task_1"]


def test_finalize_action_refreshes_aggregate_before_releasing_lease() -> None:
    runner, coordinator = _runner("finalize")

    assert runner.run_once() is True
    assert coordinator.events == ["claim", "plan", "finalize", "release"]


def test_visual_reference_runner_is_db_backed_not_rq_owned() -> None:
    source = inspect.getsource(VisualReferenceCoordinatorRunner).lower()
    assert "redis" not in source
    assert "rq" not in source
    assert "claim_due_operation" in source
    assert "visualreferencedurableproviderexecutor" in source
    assert isinstance(VisualReferenceDurableProviderExecutor, type)
