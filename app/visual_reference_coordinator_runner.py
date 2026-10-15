from __future__ import annotations

import logging
import os
import socket
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.db import visual_reference_working_models as _visual_reference_working_models  # noqa: F401
from app.db.visual_reference_operation_anchor import VisualReferenceOperationAnchor
from app.db.visual_reference_operation_task import VisualReferenceOperationTask
from app.db.visual_reference_working_operation import VisualReferenceWorkingOperation
from app.modules.media.durable_workflow.timing import (
    parse_iso,
    submit_stale_at,
    utc_now,
    utc_now_iso,
)
from app.modules.v4.visual_reference_coordinator import VisualReferenceCoordinator
from app.modules.v4.visual_reference_provider_driver import VisualReferenceDurableProviderExecutor
from app.repositories.visual_reference_working import VisualReferenceReconcileLease


logger = logging.getLogger(__name__)
_ACTIVE_OPERATION_STATUSES = ("queued", "running", "finalizing")
_ACTIVE_TASK_STATUSES = {"queued", "submitting", "waiting_provider", "downloading", "materializing"}
_IMMEDIATE_TASK_STATUSES = {"queued", "downloading", "materializing"}


class VisualReferenceCoordinatorRunner:
    """Visual References domain runner registered with the durable media workflow host."""

    def __init__(
        self,
        session_factory,
        *,
        owner: str | None = None,
        lease_seconds: int = 15,
        stale_submit_seconds: int = 30,
        error_backoff_seconds: int = 5,
    ) -> None:
        self.session_factory = session_factory
        self.owner = owner or self._default_owner()
        self.lease_seconds = lease_seconds
        self.stale_submit_seconds = stale_submit_seconds
        self.error_backoff_seconds = error_backoff_seconds
        self.coordinator = VisualReferenceCoordinator(session_factory)
        self.provider = VisualReferenceDurableProviderExecutor(session_factory)

    @staticmethod
    def _default_owner() -> str:
        return f"visual-references:{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"

    def run_once(self) -> bool:
        self._repair_unscheduled_active_operations()
        lease = self.coordinator.claim_due_operation(
            owner=self.owner,
            lease_seconds=self.lease_seconds,
        )
        if lease is None:
            return False
        try:
            action = self.coordinator.plan_next_action(lease)
            if action.action == "submit" and action.operation_task_id:
                self.provider.submit_task(
                    lease,
                    operation_task_id=action.operation_task_id,
                )
                return True
            if action.action in {"poll", "download", "materialize"} and action.operation_task_id:
                self.coordinator.release_lease(lease)
                self.provider.resume_or_advance_task(
                    operation_task_id=action.operation_task_id,
                )
                return True
            if action.action == "await_submit_commit" and action.operation_task_id:
                return self._handle_awaiting_submission(lease, action.operation_task_id)
            if action.action == "finalize":
                self.coordinator.refresh_local_aggregate(lease)
                self.coordinator.release_lease(lease, require_current=False)
                return True
            if action.action == "cancel":
                self.coordinator.cancel_operation(operation_id=lease.operation_id)
                return True
            self.coordinator.release_lease(lease, require_current=False)
            return False
        except Exception:
            self._release_best_effort(lease)
            self._defer_operation_after_failure(lease.operation_id)
            raise

    def _repair_unscheduled_active_operations(self) -> int:
        now = utc_now_iso()
        with self.session_factory() as session:  # type: Session
            changed = session.execute(
                update(VisualReferenceWorkingOperation)
                .where(
                    VisualReferenceWorkingOperation.status.in_(_ACTIVE_OPERATION_STATUSES),
                    VisualReferenceWorkingOperation.next_reconcile_at.is_(None),
                    or_(
                        VisualReferenceWorkingOperation.reconcile_token.is_(None),
                        VisualReferenceWorkingOperation.reconcile_lease_expires_at.is_(None),
                        VisualReferenceWorkingOperation.reconcile_lease_expires_at <= now,
                    ),
                )
                .values(next_reconcile_at=now, updated_at=now)
            )
            repaired = int(changed.rowcount or 0)
            session.commit()
        if repaired:
            logger.warning(
                "Repaired %s unscheduled active Visual Reference V2 operation(s)",
                repaired,
            )
        return repaired

    def _handle_awaiting_submission(
        self,
        lease: VisualReferenceReconcileLease,
        operation_task_id: str,
    ) -> bool:
        """Defer a fresh submit intent or recover its exact durable transport.

        Shared timing helpers define the stale boundary. Visual References keeps
        its domain recovery policy because MediaBatch, MediaTask, and the durable
        task link are staged atomically before provider submission.
        """
        now = utc_now()
        with self.session_factory() as session:  # type: Session
            task = session.get(VisualReferenceOperationTask, operation_task_id)
            if task is None or task.status != "submitting" or not task.submit_started_at:
                session.rollback()
                self.coordinator.release_lease(lease, require_current=False)
                return False
            stale_at = submit_stale_at(
                task.submit_started_at,
                stale_after_seconds=self.stale_submit_seconds,
            )
            if now >= stale_at:
                session.rollback()
                self.coordinator.release_lease(lease)
                self.provider.resume_or_advance_task(
                    operation_task_id=operation_task_id,
                    now_iso=now.isoformat(),
                )
                return True

            due_values = [stale_at]
            operation_tasks = list(
                session.scalars(
                    select(VisualReferenceOperationTask)
                    .join(
                        VisualReferenceOperationAnchor,
                        VisualReferenceOperationAnchor.operation_anchor_id
                        == VisualReferenceOperationTask.operation_anchor_id,
                    )
                    .where(VisualReferenceOperationAnchor.operation_id == lease.operation_id)
                )
            )
            for other in operation_tasks:
                if (
                    other.operation_task_id == operation_task_id
                    or other.status not in _ACTIVE_TASK_STATUSES
                ):
                    continue
                if other.status in _IMMEDIATE_TASK_STATUSES:
                    due_values.append(now)
                elif other.status == "submitting" and other.submit_started_at:
                    due_values.append(
                        submit_stale_at(
                            other.submit_started_at,
                            stale_after_seconds=self.stale_submit_seconds,
                        )
                    )
                elif other.next_reconcile_at:
                    due_values.append(parse_iso(other.next_reconcile_at))

            next_due = min(due_values).isoformat()
            changed = session.execute(
                update(VisualReferenceWorkingOperation)
                .where(
                    VisualReferenceWorkingOperation.operation_id == lease.operation_id,
                    VisualReferenceWorkingOperation.reconcile_owner == lease.owner,
                    VisualReferenceWorkingOperation.reconcile_token == lease.token,
                    VisualReferenceWorkingOperation.reconcile_lease_expires_at.is_not(None),
                    VisualReferenceWorkingOperation.reconcile_lease_expires_at > now.isoformat(),
                )
                .values(next_reconcile_at=next_due, updated_at=now.isoformat())
            )
            if int(changed.rowcount or 0) != 1:
                session.rollback()
                self.coordinator.release_lease(lease, require_current=False)
                return False
            session.commit()
        self.coordinator.release_lease(lease)
        return False

    def _release_best_effort(self, lease: VisualReferenceReconcileLease) -> None:
        try:
            self.coordinator.release_lease(lease, require_current=False)
        except Exception:
            logger.exception(
                "Unable to release Visual Reference V2 reconcile lease after failure"
            )

    def _defer_operation_after_failure(self, operation_id: str) -> None:
        """Move a poisoned operation behind other due work after an iteration failure."""
        now = utc_now()
        next_due = (now + timedelta(seconds=self.error_backoff_seconds)).isoformat()
        now_iso = now.isoformat()
        try:
            with self.session_factory() as session:  # type: Session
                changed = session.execute(
                    update(VisualReferenceWorkingOperation)
                    .where(
                        VisualReferenceWorkingOperation.operation_id == operation_id,
                        VisualReferenceWorkingOperation.status.in_(_ACTIVE_OPERATION_STATUSES),
                        or_(
                            VisualReferenceWorkingOperation.reconcile_token.is_(None),
                            VisualReferenceWorkingOperation.reconcile_lease_expires_at.is_(None),
                            VisualReferenceWorkingOperation.reconcile_lease_expires_at <= now_iso,
                        ),
                    )
                    .values(next_reconcile_at=next_due, updated_at=now_iso)
                )
                if int(changed.rowcount or 0) == 1:
                    session.commit()
                else:
                    session.rollback()
        except Exception:
            logger.exception("Unable to defer Visual Reference V2 operation after iteration failure")
