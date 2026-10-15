from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

from sqlalchemy.orm import Session

from app.api.errors import AppError
from app.core.ids import generate_id
from app.modules.media.durable_workflow.contracts import ReconcileLease
from app.modules.media.durable_workflow.coordinator import DurableMediaCoordinator
from app.modules.v4.visual_reference_working_state import VisualReferenceWorkingStateService
from app.repositories.visual_reference_workflow_adapter import VisualReferenceWorkflowRepositoryAdapter
from app.repositories.visual_reference_working import VisualReferenceReconcileLease, VisualReferenceWorkingRepository


@dataclass(frozen=True, slots=True)
class VisualReferenceReconcileAction:
    action: str
    operation_id: str
    reconcile_token: str
    operation_anchor_id: str | None = None
    operation_task_id: str | None = None
    task_status: str | None = None
    next_reconcile_at: str | None = None


@dataclass(frozen=True, slots=True)
class PreparedVisualReferenceSubmission:
    operation_id: str
    operation_anchor_id: str
    operation_task_id: str
    submit_attempt_token: str


class _VisualReferenceWorkflowDriver:
    def __init__(self, session: Session) -> None:
        self.state = VisualReferenceWorkingStateService(session)

    def begin_task_submission(self, *, operation_task_id: str, submit_attempt_token: str, now_iso: str) -> None:
        self.state.begin_task_submission(
            operation_task_id=operation_task_id,
            submit_attempt_token=submit_attempt_token,
            now_iso=now_iso,
        )

    def refresh_unit(self, *, operation_unit_id: str, now_iso: str) -> None:
        self.state.refresh_operation_anchor(operation_anchor_id=operation_unit_id, now_iso=now_iso)

    def refresh_operation(self, *, operation_id: str, now_iso: str) -> str:
        return self.state.refresh_operation(operation_id=operation_id, now_iso=now_iso)

    def recover_stale_submitting(
        self,
        *,
        operation_id: str,
        reconcile_token: str,
        stale_before_iso: str,
        now_iso: str,
    ) -> int:
        return self.state.recover_stale_submitting(
            operation_id=operation_id,
            reconcile_token=reconcile_token,
            stale_before_iso=stale_before_iso,
            now_iso=now_iso,
        )

    def cancel_operation(self, *, operation_id: str, now_iso: str) -> Mapping[str, object]:
        return self.state.cancel_operation(operation_id=operation_id, now_iso=now_iso)


class _VisualReferenceWorkflowErrors:
    @staticmethod
    def lease_lost(operation_id: str) -> Exception:
        return AppError(
            "VISUAL_REFERENCE_V2_RECONCILE_LEASE_LOST",
            "Visual Reference reconciliation lease is no longer current.",
            409,
            retryable=True,
            details={"operation_id": operation_id},
        )

    @staticmethod
    def operation_not_found(operation_id: str) -> Exception:
        return AppError(
            "VISUAL_REFERENCE_V2_STATE_NOT_FOUND",
            "Visual Reference working operation was not found.",
            404,
            retryable=False,
            details={"operation_id": operation_id},
        )

    @staticmethod
    def task_not_found(operation_task_id: str) -> Exception:
        return AppError(
            "VISUAL_REFERENCE_V2_STATE_NOT_FOUND",
            "Visual Reference operation task was not found.",
            404,
            retryable=False,
            details={"operation_task_id": operation_task_id},
        )

    @staticmethod
    def task_operation_mismatch(operation_id: str, operation_task_id: str) -> Exception:
        return AppError(
            "VISUAL_REFERENCE_V2_STATE_INTEGRITY_INVALID",
            "Visual Reference operation task does not belong to the leased operation.",
            409,
            retryable=False,
            details={"operation_id": operation_id, "operation_task_id": operation_task_id},
        )


class VisualReferenceCoordinator:
    """Visual Reference V2 facade over the shared durable-media coordinator."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._shared = DurableMediaCoordinator(
            session_factory,
            repository_factory=lambda session: VisualReferenceWorkflowRepositoryAdapter(
                VisualReferenceWorkingRepository(session)
            ),
            driver_factory=_VisualReferenceWorkflowDriver,
            reconcile_token_factory=lambda: generate_id("visual_reference_reconcile"),
            submit_token_factory=lambda: generate_id("visual_reference_submit"),
            errors=_VisualReferenceWorkflowErrors(),
        )

    @staticmethod
    def _shared_lease(value: VisualReferenceReconcileLease) -> ReconcileLease:
        return ReconcileLease(value.operation_id, value.owner, value.token, value.expires_at)

    @staticmethod
    def _domain_lease(value: ReconcileLease) -> VisualReferenceReconcileLease:
        return VisualReferenceReconcileLease(value.operation_id, value.owner, value.token, value.expires_at)

    def claim_due_operation(self, *, owner: str, now_iso: str | None = None, lease_seconds: int = 15, scan_limit: int = 50) -> VisualReferenceReconcileLease | None:
        value = self._shared.claim_due_operation(owner=owner, now_iso=now_iso, lease_seconds=lease_seconds, scan_limit=scan_limit)
        return self._domain_lease(value) if value is not None else None

    def renew_lease(self, lease: VisualReferenceReconcileLease, *, now_iso: str | None = None, lease_seconds: int = 15) -> VisualReferenceReconcileLease:
        return self._domain_lease(self._shared.renew_lease(self._shared_lease(lease), now_iso=now_iso, lease_seconds=lease_seconds))

    def release_lease(self, lease: VisualReferenceReconcileLease, *, now_iso: str | None = None, require_current: bool = True) -> bool:
        return self._shared.release_lease(self._shared_lease(lease), now_iso=now_iso, require_current=require_current)

    def plan_next_action(self, lease: VisualReferenceReconcileLease, *, now_iso: str | None = None) -> VisualReferenceReconcileAction:
        action = self._shared.plan_next_action(self._shared_lease(lease), now_iso=now_iso)
        return VisualReferenceReconcileAction(
            action=action.action,
            operation_id=action.operation_id,
            reconcile_token=action.reconcile_token,
            operation_anchor_id=action.operation_unit_id,
            operation_task_id=action.operation_task_id,
            task_status=action.task_status,
            next_reconcile_at=action.next_reconcile_at,
        )

    def prepare_submission(self, lease: VisualReferenceReconcileLease, *, operation_task_id: str, now_iso: str | None = None) -> PreparedVisualReferenceSubmission:
        value = self._shared.prepare_submission(self._shared_lease(lease), operation_task_id=operation_task_id, now_iso=now_iso)
        return PreparedVisualReferenceSubmission(
            operation_id=value.operation_id,
            operation_anchor_id=value.operation_unit_id,
            operation_task_id=value.operation_task_id,
            submit_attempt_token=value.submit_attempt_token,
        )

    def refresh_local_aggregate(self, lease: VisualReferenceReconcileLease, *, now_iso: str | None = None) -> str:
        return self._shared.refresh_local_aggregate(self._shared_lease(lease), now_iso=now_iso)

    def recover_stale_submissions(self, lease: VisualReferenceReconcileLease, *, stale_before_iso: str, now_iso: str | None = None) -> int:
        return self._shared.recover_stale_submissions(self._shared_lease(lease), stale_before_iso=stale_before_iso, now_iso=now_iso)

    def cancel_operation(self, *, operation_id: str, now_iso: str | None = None) -> dict[str, object]:
        return self._shared.cancel_operation(operation_id=operation_id, now_iso=now_iso)
