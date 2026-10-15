from __future__ import annotations

from app.modules.media.durable_workflow.contracts import OperationSnapshot, TaskSnapshot, UnitSnapshot
from app.repositories.voice_durable_working import DurableVoiceTaskRow, VoiceDurableWorkingRepository


class VoiceWorkflowRepositoryAdapter:
    """Neutral durable snapshots over the existing Voice segment tables.

    A VoiceSegmentTask is deliberately projected as both one Unit and its singleton
    Task. No Voice operation-unit table exists.
    """

    def __init__(self, repository: VoiceDurableWorkingRepository) -> None:
        self.repository = repository

    def list_due_operation_ids(self, *, now_iso: str, limit: int = 50) -> list[str]:
        return self.repository.list_due_operation_ids(now_iso=now_iso, limit=limit)

    def get_operation_snapshot(self, operation_id: str) -> OperationSnapshot | None:
        operation = self.repository.get_operation(operation_id)
        if operation is None:
            return None
        return OperationSnapshot(
            operation_id=operation.operation_id,
            status=operation.status,
            next_reconcile_at=operation.next_reconcile_at,
            cancel_requested_at=operation.cancel_requested_at,
            reconcile_owner=operation.reconcile_owner,
            reconcile_token=operation.reconcile_token,
            reconcile_lease_expires_at=operation.reconcile_lease_expires_at,
        )

    def list_unit_snapshots(self, operation_id: str) -> list[UnitSnapshot]:
        return [self._unit_snapshot(task) for task in self.repository.list_tasks_for_operation(operation_id)]

    def get_unit_snapshot(self, operation_unit_id: str) -> UnitSnapshot | None:
        task = self.repository.get_task(operation_unit_id)
        return self._unit_snapshot(task) if task is not None else None

    def get_task_snapshot(self, operation_task_id: str) -> TaskSnapshot | None:
        task = self.repository.get_task(operation_task_id)
        return self._task_snapshot(task) if task is not None else None

    def list_task_snapshots_for_unit(self, operation_unit_id: str) -> list[TaskSnapshot]:
        task = self.repository.get_task(operation_unit_id)
        return [self._task_snapshot(task)] if task is not None else []

    def list_stale_submitting_task_ids(self, *, operation_id: str, stale_before_iso: str) -> list[str]:
        return [
            task.voice_segment_task_id
            for task in self.repository.list_stale_submitting_tasks(
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
    def _unit_snapshot(task: DurableVoiceTaskRow) -> UnitSnapshot:
        status = {
            "queued": "queued",
            "submitting": "running",
            "waiting_provider": "running",
            "materializing": "running",
            "submit_outcome_unknown": "running",
            "completed": "candidate_ready",
            "failed": "failed",
            "cancelled": "cancelled",
        }.get(task.status, "running")
        return UnitSnapshot(
            operation_unit_id=task.voice_segment_task_id,
            operation_id=task.operation_id,
            status=status,
        )

    @staticmethod
    def _task_snapshot(task: DurableVoiceTaskRow) -> TaskSnapshot:
        return TaskSnapshot(
            operation_task_id=task.voice_segment_task_id,
            operation_unit_id=task.voice_segment_task_id,
            status=task.status,
            next_reconcile_at=task.next_reconcile_at,
            submit_started_at=task.submit_started_at,
        )
