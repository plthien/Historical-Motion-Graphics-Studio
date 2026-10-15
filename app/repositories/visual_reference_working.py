from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.db.visual_reference_anchor_state import VisualReferenceAnchorState
from app.db.visual_reference_candidate import VisualReferenceCandidate
from app.db.visual_reference_operation_anchor import VisualReferenceOperationAnchor
from app.db.visual_reference_operation_task import VisualReferenceOperationTask
from app.db.visual_reference_working_operation import VisualReferenceWorkingOperation

ACTIVE_OPERATION_STATUSES = ("queued", "running", "finalizing")
TERMINAL_OPERATION_STATUSES = ("completed", "partial", "failed", "cancelled", "superseded")
ACTIVE_OPERATION_ANCHOR_STATUSES = ("queued", "running")
TERMINAL_OPERATION_ANCHOR_STATUSES = ("candidate_ready", "failed", "cancelled")
ACTIVE_TASK_STATUSES = ("queued", "submitting", "waiting_provider", "downloading", "materializing")
TERMINAL_TASK_STATUSES = ("submit_outcome_unknown", "completed", "failed", "cancelled")


@dataclass(frozen=True, slots=True)
class VisualReferenceReconcileLease:
    operation_id: str
    owner: str
    token: str
    expires_at: str


class VisualReferenceWorkingRepository:
    """Transactional persistence for Visual Reference V2 live working state."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add_operation(self, value: VisualReferenceWorkingOperation) -> VisualReferenceWorkingOperation:
        self.session.add(value)
        self.session.flush()
        return value

    def add_operation_anchor(self, value: VisualReferenceOperationAnchor) -> VisualReferenceOperationAnchor:
        self.session.add(value)
        self.session.flush()
        return value

    def add_task(self, value: VisualReferenceOperationTask) -> VisualReferenceOperationTask:
        self.session.add(value)
        self.session.flush()
        return value

    def add_anchor_state(self, value: VisualReferenceAnchorState) -> VisualReferenceAnchorState:
        self.session.add(value)
        self.session.flush()
        return value

    def add_candidate(self, value: VisualReferenceCandidate) -> VisualReferenceCandidate:
        self.session.add(value)
        self.session.flush()
        return value

    def get_operation(self, operation_id: str) -> VisualReferenceWorkingOperation | None:
        return self.session.get(VisualReferenceWorkingOperation, operation_id)

    def get_operation_anchor(self, operation_anchor_id: str) -> VisualReferenceOperationAnchor | None:
        return self.session.get(VisualReferenceOperationAnchor, operation_anchor_id)

    def get_task(self, operation_task_id: str) -> VisualReferenceOperationTask | None:
        return self.session.get(VisualReferenceOperationTask, operation_task_id)

    def get_anchor_state(self, project_id: str, anchor_type: str, anchor_id: str) -> VisualReferenceAnchorState | None:
        return self.session.get(VisualReferenceAnchorState, (project_id, anchor_type, anchor_id))

    def get_candidate(self, candidate_id: str) -> VisualReferenceCandidate | None:
        return self.session.get(VisualReferenceCandidate, candidate_id)

    def get_candidate_for_operation_task(self, operation_task_id: str) -> VisualReferenceCandidate | None:
        return self.session.scalar(
            select(VisualReferenceCandidate).where(
                VisualReferenceCandidate.source_operation_task_id == operation_task_id
            )
        )

    def list_candidates_for_anchor(
        self,
        *,
        project_id: str,
        anchor_type: str,
        anchor_id: str,
    ) -> list[VisualReferenceCandidate]:
        stmt = (
            select(VisualReferenceCandidate)
            .where(
                VisualReferenceCandidate.project_id == project_id,
                VisualReferenceCandidate.anchor_type == anchor_type,
                VisualReferenceCandidate.anchor_id == anchor_id,
            )
            .order_by(
                VisualReferenceCandidate.created_at,
                VisualReferenceCandidate.candidate_index,
                VisualReferenceCandidate.candidate_id,
            )
        )
        return list(self.session.scalars(stmt))

    def list_due_operation_ids(self, *, now_iso: str, limit: int = 50) -> list[str]:
        stmt = (
            select(VisualReferenceWorkingOperation.operation_id)
            .where(
                VisualReferenceWorkingOperation.status.in_(ACTIVE_OPERATION_STATUSES),
                VisualReferenceWorkingOperation.next_reconcile_at.is_not(None),
                VisualReferenceWorkingOperation.next_reconcile_at <= now_iso,
                or_(
                    VisualReferenceWorkingOperation.reconcile_token.is_(None),
                    VisualReferenceWorkingOperation.reconcile_lease_expires_at.is_(None),
                    VisualReferenceWorkingOperation.reconcile_lease_expires_at <= now_iso,
                ),
            )
            .order_by(
                VisualReferenceWorkingOperation.next_reconcile_at,
                VisualReferenceWorkingOperation.created_at,
                VisualReferenceWorkingOperation.operation_id,
            )
            .limit(limit)
        )
        return [str(value) for value in self.session.scalars(stmt)]

    def list_operation_anchors(self, operation_id: str) -> list[VisualReferenceOperationAnchor]:
        stmt = (
            select(VisualReferenceOperationAnchor)
            .where(VisualReferenceOperationAnchor.operation_id == operation_id)
            .order_by(VisualReferenceOperationAnchor.anchor_type, VisualReferenceOperationAnchor.anchor_id)
        )
        return list(self.session.scalars(stmt))

    def list_tasks_for_operation_anchor(self, operation_anchor_id: str) -> list[VisualReferenceOperationTask]:
        stmt = (
            select(VisualReferenceOperationTask)
            .where(VisualReferenceOperationTask.operation_anchor_id == operation_anchor_id)
            .order_by(VisualReferenceOperationTask.candidate_index, VisualReferenceOperationTask.operation_task_id)
        )
        return list(self.session.scalars(stmt))

    def list_stale_submitting_tasks(self, *, operation_id: str, stale_before_iso: str) -> list[VisualReferenceOperationTask]:
        stmt = (
            select(VisualReferenceOperationTask)
            .join(
                VisualReferenceOperationAnchor,
                VisualReferenceOperationAnchor.operation_anchor_id == VisualReferenceOperationTask.operation_anchor_id,
            )
            .where(
                VisualReferenceOperationAnchor.operation_id == operation_id,
                VisualReferenceOperationTask.status == "submitting",
                VisualReferenceOperationTask.submit_started_at.is_not(None),
                VisualReferenceOperationTask.submit_started_at <= stale_before_iso,
            )
            .order_by(
                VisualReferenceOperationAnchor.anchor_type,
                VisualReferenceOperationAnchor.anchor_id,
                VisualReferenceOperationTask.candidate_index,
            )
        )
        return list(self.session.scalars(stmt))

    def try_acquire_reconcile_lease(self, *, operation_id: str, owner: str, token: str, now_iso: str, expires_at: str) -> bool:
        stmt = (
            update(VisualReferenceWorkingOperation)
            .where(
                VisualReferenceWorkingOperation.operation_id == operation_id,
                VisualReferenceWorkingOperation.status.in_(ACTIVE_OPERATION_STATUSES),
                VisualReferenceWorkingOperation.next_reconcile_at.is_not(None),
                VisualReferenceWorkingOperation.next_reconcile_at <= now_iso,
                or_(
                    VisualReferenceWorkingOperation.reconcile_token.is_(None),
                    VisualReferenceWorkingOperation.reconcile_lease_expires_at.is_(None),
                    VisualReferenceWorkingOperation.reconcile_lease_expires_at <= now_iso,
                ),
            )
            .values(reconcile_owner=owner, reconcile_token=token, reconcile_lease_expires_at=expires_at, updated_at=now_iso)
        )
        return int(self.session.execute(stmt).rowcount or 0) == 1

    def renew_reconcile_lease(self, *, operation_id: str, owner: str, token: str, now_iso: str, expires_at: str) -> bool:
        stmt = (
            update(VisualReferenceWorkingOperation)
            .where(
                VisualReferenceWorkingOperation.operation_id == operation_id,
                VisualReferenceWorkingOperation.status.in_(ACTIVE_OPERATION_STATUSES),
                VisualReferenceWorkingOperation.reconcile_owner == owner,
                VisualReferenceWorkingOperation.reconcile_token == token,
                VisualReferenceWorkingOperation.reconcile_lease_expires_at.is_not(None),
                VisualReferenceWorkingOperation.reconcile_lease_expires_at > now_iso,
            )
            .values(reconcile_lease_expires_at=expires_at, updated_at=now_iso)
        )
        return int(self.session.execute(stmt).rowcount or 0) == 1

    def release_reconcile_lease(self, *, operation_id: str, owner: str, token: str, now_iso: str) -> bool:
        stmt = (
            update(VisualReferenceWorkingOperation)
            .where(
                VisualReferenceWorkingOperation.operation_id == operation_id,
                VisualReferenceWorkingOperation.reconcile_owner == owner,
                VisualReferenceWorkingOperation.reconcile_token == token,
            )
            .values(reconcile_owner=None, reconcile_token=None, reconcile_lease_expires_at=None, updated_at=now_iso)
        )
        return int(self.session.execute(stmt).rowcount or 0) == 1

    def fence_reconcile_write(self, *, operation_id: str, token: str, now_iso: str) -> bool:
        stmt = (
            update(VisualReferenceWorkingOperation)
            .where(
                VisualReferenceWorkingOperation.operation_id == operation_id,
                VisualReferenceWorkingOperation.status.in_(ACTIVE_OPERATION_STATUSES),
                VisualReferenceWorkingOperation.reconcile_token == token,
                VisualReferenceWorkingOperation.reconcile_lease_expires_at.is_not(None),
                VisualReferenceWorkingOperation.reconcile_lease_expires_at > now_iso,
            )
            .values(updated_at=now_iso)
        )
        return int(self.session.execute(stmt).rowcount or 0) == 1

    def compare_and_set_anchor_state(
        self,
        *,
        project_id: str,
        anchor_type: str,
        anchor_id: str,
        expected_version: int,
        values: dict[str, Any],
        updated_at: str,
    ) -> bool:
        forbidden = {"project_id", "anchor_type", "anchor_id", "version", "created_at"}
        if forbidden.intersection(values):
            raise ValueError("AnchorState CAS cannot mutate identity/version fields directly.")
        stmt = (
            update(VisualReferenceAnchorState)
            .where(
                VisualReferenceAnchorState.project_id == project_id,
                VisualReferenceAnchorState.anchor_type == anchor_type,
                VisualReferenceAnchorState.anchor_id == anchor_id,
                VisualReferenceAnchorState.version == expected_version,
            )
            .values(**values, version=VisualReferenceAnchorState.version + 1, updated_at=updated_at)
        )
        changed = int(self.session.execute(stmt).rowcount or 0) == 1
        if changed:
            self.session.expire_all()
        return changed

    def release_active_operation_anchor(self, *, operation_anchor_id: str, updated_at: str) -> int:
        stmt = (
            update(VisualReferenceAnchorState)
            .where(VisualReferenceAnchorState.active_operation_anchor_id == operation_anchor_id)
            .values(
                active_operation_anchor_id=None,
                version=VisualReferenceAnchorState.version + 1,
                updated_at=updated_at,
                last_progress_at=updated_at,
            )
        )
        return int(self.session.execute(stmt).rowcount or 0)

    def fail_active_operation_anchor_state(
        self,
        *,
        operation_anchor_id: str,
        updated_at: str,
        error_json: dict[str, Any],
    ) -> int:
        first = self.session.execute(
            update(VisualReferenceAnchorState)
            .where(
                VisualReferenceAnchorState.active_operation_anchor_id == operation_anchor_id,
                VisualReferenceAnchorState.selected_candidate_id.is_(None),
                VisualReferenceAnchorState.approved_candidate_id.is_(None),
            )
            .values(
                active_operation_anchor_id=None,
                resolution_status="failed",
                review_status="unreviewed",
                last_error_json=error_json,
                last_progress_at=updated_at,
                version=VisualReferenceAnchorState.version + 1,
                updated_at=updated_at,
            )
        )
        released = int(first.rowcount or 0)
        released += self.release_active_operation_anchor(
            operation_anchor_id=operation_anchor_id,
            updated_at=updated_at,
        )
        return released

    def transition_operation(self, *, operation_id: str, expected_statuses: Iterable[str], target_status: str, values: dict[str, Any]) -> bool:
        stmt = (
            update(VisualReferenceWorkingOperation)
            .where(
                VisualReferenceWorkingOperation.operation_id == operation_id,
                VisualReferenceWorkingOperation.status.in_(tuple(expected_statuses)),
            )
            .values(status=target_status, **values)
        )
        return int(self.session.execute(stmt).rowcount or 0) == 1

    def transition_operation_anchor(self, *, operation_anchor_id: str, expected_statuses: Iterable[str], target_status: str, values: dict[str, Any]) -> bool:
        stmt = (
            update(VisualReferenceOperationAnchor)
            .where(
                VisualReferenceOperationAnchor.operation_anchor_id == operation_anchor_id,
                VisualReferenceOperationAnchor.status.in_(tuple(expected_statuses)),
            )
            .values(status=target_status, version=VisualReferenceOperationAnchor.version + 1, **values)
        )
        changed = int(self.session.execute(stmt).rowcount or 0) == 1
        if not changed or target_status not in TERMINAL_OPERATION_ANCHOR_STATUSES:
            return changed
        updated_at = values.get("updated_at")
        if not isinstance(updated_at, str) or not updated_at:
            raise ValueError("terminal operation anchor transition requires updated_at")
        if target_status == "failed":
            self.fail_active_operation_anchor_state(
                operation_anchor_id=operation_anchor_id,
                updated_at=updated_at,
                error_json={
                    "error_code": values.get("error_code") or "VISUAL_REFERENCE_GENERATION_FAILED",
                    "error_message": values.get("error_message") or "Visual Reference generation failed.",
                    "error_details": values.get("error_details_json") or {},
                },
            )
        else:
            self.release_active_operation_anchor(operation_anchor_id=operation_anchor_id, updated_at=updated_at)
        return True

    def transition_task(self, *, operation_task_id: str, expected_statuses: Iterable[str], target_status: str, values: dict[str, Any]) -> bool:
        stmt = (
            update(VisualReferenceOperationTask)
            .where(
                VisualReferenceOperationTask.operation_task_id == operation_task_id,
                VisualReferenceOperationTask.status.in_(tuple(expected_statuses)),
            )
            .values(status=target_status, **values)
        )
        return int(self.session.execute(stmt).rowcount or 0) == 1
