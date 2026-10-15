from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.modules.media.durable_workflow.contracts import (
    OperationSnapshot,
    TaskSnapshot,
    UnitSnapshot,
)


@runtime_checkable
class DurableWorkflowRepository(Protocol):
    """Storage boundary required by the generic durable coordinator.

    Domain repositories remain authoritative for persistence. Adapters expose
    neutral snapshots so shared workflow code never depends on beat/anchor
    identity or domain ORM models.
    """

    def list_due_operation_ids(self, *, now_iso: str, limit: int = 50) -> list[str]: ...

    def get_operation_snapshot(self, operation_id: str) -> OperationSnapshot | None: ...

    def list_unit_snapshots(self, operation_id: str) -> list[UnitSnapshot]: ...

    def get_unit_snapshot(self, operation_unit_id: str) -> UnitSnapshot | None: ...

    def get_task_snapshot(self, operation_task_id: str) -> TaskSnapshot | None: ...

    def list_task_snapshots_for_unit(self, operation_unit_id: str) -> list[TaskSnapshot]: ...

    def list_stale_submitting_task_ids(
        self,
        *,
        operation_id: str,
        stale_before_iso: str,
    ) -> list[str]: ...

    def try_acquire_reconcile_lease(
        self,
        *,
        operation_id: str,
        owner: str,
        token: str,
        now_iso: str,
        expires_at: str,
    ) -> bool: ...

    def renew_reconcile_lease(
        self,
        *,
        operation_id: str,
        owner: str,
        token: str,
        now_iso: str,
        expires_at: str,
    ) -> bool: ...

    def release_reconcile_lease(
        self,
        *,
        operation_id: str,
        owner: str,
        token: str,
        now_iso: str,
    ) -> bool: ...

    def fence_reconcile_write(
        self,
        *,
        operation_id: str,
        token: str,
        now_iso: str,
    ) -> bool: ...
