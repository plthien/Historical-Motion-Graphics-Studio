from __future__ import annotations

from app.modules.media.durable_workflow.contracts import (
    OperationSnapshot,
    TaskSnapshot,
    UnitSnapshot,
)
from app.repositories.scene_asset_working import SceneAssetWorkingRepository


class SceneAssetWorkflowRepositoryAdapter:
    """Neutral execution facade over the existing Scene Assets V2 repository."""

    def __init__(self, repository: SceneAssetWorkingRepository) -> None:
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
            reconcile_owner=getattr(operation, "reconcile_owner", None),
            reconcile_token=getattr(operation, "reconcile_token", None),
            reconcile_lease_expires_at=getattr(operation, "reconcile_lease_expires_at", None),
        )

    def list_unit_snapshots(self, operation_id: str) -> list[UnitSnapshot]:
        return [self._unit_snapshot(unit) for unit in self.repository.list_operation_beats(operation_id)]

    def get_unit_snapshot(self, operation_unit_id: str) -> UnitSnapshot | None:
        unit = self.repository.get_operation_beat(operation_unit_id)
        return self._unit_snapshot(unit) if unit is not None else None

    def get_task_snapshot(self, operation_task_id: str) -> TaskSnapshot | None:
        task = self.repository.get_task(operation_task_id)
        return self._task_snapshot(task) if task is not None else None

    def list_task_snapshots_for_unit(self, operation_unit_id: str) -> list[TaskSnapshot]:
        return [
            self._task_snapshot(task)
            for task in self.repository.list_tasks_for_operation_beat(operation_unit_id)
        ]

    def list_stale_submitting_task_ids(
        self,
        *,
        operation_id: str,
        stale_before_iso: str,
    ) -> list[str]:
        return [
            str(task.operation_task_id)
            for task in self.repository.list_stale_submitting_tasks(
                operation_id=operation_id,
                stale_before_iso=stale_before_iso,
            )
        ]

    def try_acquire_reconcile_lease(
        self,
        *,
        operation_id: str,
        owner: str,
        token: str,
        now_iso: str,
        expires_at: str,
    ) -> bool:
        return self.repository.try_acquire_reconcile_lease(
            operation_id=operation_id,
            owner=owner,
            token=token,
            now_iso=now_iso,
            expires_at=expires_at,
        )

    def renew_reconcile_lease(
        self,
        *,
        operation_id: str,
        owner: str,
        token: str,
        now_iso: str,
        expires_at: str,
    ) -> bool:
        return self.repository.renew_reconcile_lease(
            operation_id=operation_id,
            owner=owner,
            token=token,
            now_iso=now_iso,
            expires_at=expires_at,
        )

    def release_reconcile_lease(
        self,
        *,
        operation_id: str,
        owner: str,
        token: str,
        now_iso: str,
    ) -> bool:
        return self.repository.release_reconcile_lease(
            operation_id=operation_id,
            owner=owner,
            token=token,
            now_iso=now_iso,
        )

    def fence_reconcile_write(
        self,
        *,
        operation_id: str,
        token: str,
        now_iso: str,
    ) -> bool:
        return self.repository.fence_reconcile_write(
            operation_id=operation_id,
            token=token,
            now_iso=now_iso,
        )

    @staticmethod
    def _unit_snapshot(unit: object) -> UnitSnapshot:
        return UnitSnapshot(
            operation_unit_id=str(getattr(unit, "operation_beat_id")),
            operation_id=str(getattr(unit, "operation_id")),
            status=str(getattr(unit, "status")),
        )

    @staticmethod
    def _task_snapshot(task: object) -> TaskSnapshot:
        return TaskSnapshot(
            operation_task_id=str(getattr(task, "operation_task_id")),
            operation_unit_id=str(getattr(task, "operation_beat_id")),
            status=str(getattr(task, "status")),
            next_reconcile_at=getattr(task, "next_reconcile_at"),
            submit_started_at=getattr(task, "submit_started_at"),
        )
