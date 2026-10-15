from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReconcileLease:
    operation_id: str
    owner: str
    token: str
    expires_at: str


@dataclass(frozen=True, slots=True)
class ReconcileAction:
    action: str
    operation_id: str
    reconcile_token: str
    operation_unit_id: str | None = None
    operation_task_id: str | None = None
    task_status: str | None = None
    next_reconcile_at: str | None = None


@dataclass(frozen=True, slots=True)
class PreparedSubmission:
    operation_id: str
    operation_unit_id: str
    operation_task_id: str
    submit_attempt_token: str


@dataclass(frozen=True, slots=True)
class OperationSnapshot:
    operation_id: str
    status: str
    next_reconcile_at: str | None
    cancel_requested_at: str | None
    reconcile_owner: str | None = None
    reconcile_token: str | None = None
    reconcile_lease_expires_at: str | None = None


@dataclass(frozen=True, slots=True)
class UnitSnapshot:
    operation_unit_id: str
    operation_id: str
    status: str


@dataclass(frozen=True, slots=True)
class TaskSnapshot:
    operation_task_id: str
    operation_unit_id: str
    status: str
    next_reconcile_at: str | None
    submit_started_at: str | None
