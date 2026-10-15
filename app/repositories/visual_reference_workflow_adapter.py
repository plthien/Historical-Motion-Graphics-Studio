from __future__ import annotations

from app.modules.media.durable_workflow.contracts import OperationSnapshot, TaskSnapshot, UnitSnapshot
from app.repositories.visual_reference_working import VisualReferenceWorkingRepository


class VisualReferenceWorkflowRepositoryAdapter:
    """Neutral durable-workflow snapshots over Visual Reference anchor persistence."""

    def __init__(self, repository: VisualReferenceWorkingRepository) -> None:
        self.repository = repository

    def list_due_operation_ids(self, *, now_iso: str, limit: int = 50) -> list[str]:
        return self.repository.list_due_operation_ids(now_iso=now_iso, limit=limit)

    def get_operation_snapshot(self, operation_id: str) -> OperationSnapshot | None:
        operation = self.repository.get_operation(operation_id)
        if operation is None:
            return None
        return OperationSnapshot(
            operation_id=str(operation.operation_id),
            status=str(operation.status),
            next_reconcile_at=operation.next_reconcile_at,
            cancel_requested_at=operation.cancel_requested_at,
            reconcile_owner=operation.reconcile_owner,
            reconcile_token=operation.reconcile_token,
            reconcile_lease_expires_at=operation.reconcile_lease_expires_at,
        )

    def list_unit_snapshots(self, operation_id: str) -> list[UnitSnapshot]:
        return [self._unit_snapshot(value) for value in self.repository.list_operation_anchors(operation_id)]

    def get_unit_snapshot(self, operation_unit_id: str) -> UnitSnapshot | None:
        value = self.repository.get_operation_anchor(operation_unit_id)
        return self._unit_snapshot(value) if value is not None else None

    def get_task_snapshot(self, operation_task_id: str) -> TaskSnapshot | None:
        value = self.repository.get_task(operation_task_id)
        return self._task_snapshot(value) if value is not None else None

    def list_task_snapshots_for_unit(self, operation_unit_id: str) -> list[TaskSnapshot]:
        return [self._task_snapshot(value) for value in self.repository.list_tasks_for_operation_anchor(operation_unit_id)]

    def list_stale_submitting_task_ids(self, *, operation_id: str, stale_before_iso: str) -> list[str]:
        return [
            str(value.operation_task_id)
            for value in self.repository.list_stale_submitting_tasks(
                operation_id=operation_id,
                stale_before_iso=stale_before_iso,
            )
        ]

    def try_acquire_reconcile_lease(self, **kwargs) -> bool:
        return self.repository.try_acquire_reconcile_lease(**kwargs)

    def renew_reconcile_lease(self, **kwargs) -> bool:
        return self.repository.renew_reconcile_lease(**kwargs)

    def release_reconcile_lease(self, **kwargs) -> bool:
        return self.repository.release_reconcile_lease(**kwargs)

    def fence_reconcile_write(self, **kwargs) -> bool:
        return self.repository.fence_reconcile_write(**kwargs)

    @staticmethod
    def _unit_snapshot(value: object) -> UnitSnapshot:
        return UnitSnapshot(
            operation_unit_id=str(getattr(value, "operation_anchor_id")),
            operation_id=str(getattr(value, "operation_id")),
            status=str(getattr(value, "status")),
        )

    @staticmethod
    def _task_snapshot(value: object) -> TaskSnapshot:
        return TaskSnapshot(
            operation_task_id=str(getattr(value, "operation_task_id")),
            operation_unit_id=str(getattr(value, "operation_anchor_id")),
            status=str(getattr(value, "status")),
            next_reconcile_at=getattr(value, "next_reconcile_at"),
            submit_started_at=getattr(value, "submit_started_at"),
        )
