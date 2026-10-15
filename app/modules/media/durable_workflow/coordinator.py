from __future__ import annotations

from typing import Callable, Mapping, Protocol

from sqlalchemy.orm import Session

from app.core.time import utc_now_iso
from app.modules.media.durable_workflow.contracts import (
    OperationSnapshot,
    PreparedSubmission,
    ReconcileAction,
    ReconcileLease,
)
from app.modules.media.durable_workflow.states import (
    ACTIVE_OPERATION_STATUSES,
    TERMINAL_TASK_STATUSES,
    TERMINAL_UNIT_STATUSES,
)
from app.modules.media.durable_workflow.timing import parse_iso, plus_seconds
from app.repositories.durable_workflow_protocol import DurableWorkflowRepository


class DurableWorkflowDomainDriver(Protocol):
    """Domain mutations required by the generic coordinator."""

    def begin_task_submission(
        self,
        *,
        operation_task_id: str,
        submit_attempt_token: str,
        now_iso: str,
    ) -> None: ...

    def refresh_unit(self, *, operation_unit_id: str, now_iso: str) -> None: ...

    def refresh_operation(self, *, operation_id: str, now_iso: str) -> str: ...

    def recover_stale_submitting(
        self,
        *,
        operation_id: str,
        reconcile_token: str,
        stale_before_iso: str,
        now_iso: str,
    ) -> int: ...

    def cancel_operation(self, *, operation_id: str, now_iso: str) -> Mapping[str, object]: ...


class DurableWorkflowErrorFactory(Protocol):
    def lease_lost(self, operation_id: str) -> Exception: ...

    def operation_not_found(self, operation_id: str) -> Exception: ...

    def task_not_found(self, operation_task_id: str) -> Exception: ...

    def task_operation_mismatch(self, operation_id: str, operation_task_id: str) -> Exception: ...


class DurableMediaCoordinator:
    """Reusable DB-backed durable media coordinator with no provider I/O.

    The coordinator owns generic operation/unit/task scheduling mechanics. Domain
    state mutations are delegated through ``DurableWorkflowDomainDriver`` so the
    shared layer never imports beat/anchor working state.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        repository_factory: Callable[[Session], DurableWorkflowRepository],
        driver_factory: Callable[[Session], DurableWorkflowDomainDriver],
        reconcile_token_factory: Callable[[], str],
        submit_token_factory: Callable[[], str],
        errors: DurableWorkflowErrorFactory,
    ) -> None:
        self.session_factory = session_factory
        self.repository_factory = repository_factory
        self.driver_factory = driver_factory
        self.reconcile_token_factory = reconcile_token_factory
        self.submit_token_factory = submit_token_factory
        self.errors = errors

    def claim_due_operation(
        self,
        *,
        owner: str,
        now_iso: str | None = None,
        lease_seconds: int = 15,
        scan_limit: int = 50,
    ) -> ReconcileLease | None:
        if not owner:
            raise ValueError("owner is required")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if scan_limit <= 0:
            raise ValueError("scan_limit must be positive")
        now = now_iso or utc_now_iso()
        expires_at = plus_seconds(now, lease_seconds)
        with self.session_factory() as session:
            repo = self.repository_factory(session)
            for operation_id in repo.list_due_operation_ids(now_iso=now, limit=scan_limit):
                token = self.reconcile_token_factory()
                if repo.try_acquire_reconcile_lease(
                    operation_id=operation_id,
                    owner=owner,
                    token=token,
                    now_iso=now,
                    expires_at=expires_at,
                ):
                    session.commit()
                    return ReconcileLease(operation_id, owner, token, expires_at)
            session.commit()
        return None

    def renew_lease(
        self,
        lease: ReconcileLease,
        *,
        now_iso: str | None = None,
        lease_seconds: int = 15,
    ) -> ReconcileLease:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        now = now_iso or utc_now_iso()
        expires_at = plus_seconds(now, lease_seconds)
        with self.session_factory() as session:
            repo = self.repository_factory(session)
            if not repo.renew_reconcile_lease(
                operation_id=lease.operation_id,
                owner=lease.owner,
                token=lease.token,
                now_iso=now,
                expires_at=expires_at,
            ):
                session.rollback()
                raise self.errors.lease_lost(lease.operation_id)
            session.commit()
        return ReconcileLease(lease.operation_id, lease.owner, lease.token, expires_at)

    def release_lease(
        self,
        lease: ReconcileLease,
        *,
        now_iso: str | None = None,
        require_current: bool = True,
    ) -> bool:
        now = now_iso or utc_now_iso()
        with self.session_factory() as session:
            repo = self.repository_factory(session)
            released = repo.release_reconcile_lease(
                operation_id=lease.operation_id,
                owner=lease.owner,
                token=lease.token,
                now_iso=now,
            )
            if require_current and not released:
                session.rollback()
                raise self.errors.lease_lost(lease.operation_id)
            session.commit()
            return released

    def plan_next_action(
        self,
        lease: ReconcileLease,
        *,
        now_iso: str | None = None,
    ) -> ReconcileAction:
        now = now_iso or utc_now_iso()
        now_dt = parse_iso(now)
        with self.session_factory() as session:
            repo = self.repository_factory(session)
            operation = repo.get_operation_snapshot(lease.operation_id)
            self._assert_read_lease(operation, lease, now_dt)
            assert operation is not None
            if operation.cancel_requested_at is not None:
                return ReconcileAction("cancel", operation.operation_id, lease.token)

            units = repo.list_unit_snapshots(operation.operation_id)
            earliest_future_due: str | None = None
            awaiting_submit: ReconcileAction | None = None
            for unit in units:
                for task in repo.list_task_snapshots_for_unit(unit.operation_unit_id):
                    status = task.status
                    due_at = task.next_reconcile_at
                    if status == "submitting":
                        if awaiting_submit is None:
                            awaiting_submit = ReconcileAction(
                                "await_submit_commit",
                                operation.operation_id,
                                lease.token,
                                unit.operation_unit_id,
                                task.operation_task_id,
                                status,
                            )
                        continue
                    if status in TERMINAL_TASK_STATUSES:
                        continue
                    if due_at is not None and parse_iso(due_at) > now_dt:
                        if earliest_future_due is None or parse_iso(due_at) < parse_iso(earliest_future_due):
                            earliest_future_due = due_at
                        continue
                    action = {
                        "queued": "submit",
                        "waiting_provider": "poll",
                        "downloading": "download",
                        "materializing": "materialize",
                    }.get(status)
                    if action is not None:
                        return ReconcileAction(
                            action,
                            operation.operation_id,
                            lease.token,
                            unit.operation_unit_id,
                            task.operation_task_id,
                            status,
                            due_at,
                        )

            if awaiting_submit is not None:
                return awaiting_submit
            if operation.status == "finalizing" or (
                units and all(unit.status in TERMINAL_UNIT_STATUSES for unit in units)
            ):
                return ReconcileAction("finalize", operation.operation_id, lease.token)
            return ReconcileAction(
                "none",
                operation.operation_id,
                lease.token,
                next_reconcile_at=earliest_future_due,
            )

    def prepare_submission(
        self,
        lease: ReconcileLease,
        *,
        operation_task_id: str,
        now_iso: str | None = None,
    ) -> PreparedSubmission:
        now = now_iso or utc_now_iso()
        submit_attempt_token = self.submit_token_factory()
        with self.session_factory() as session:
            repo = self.repository_factory(session)
            if not repo.fence_reconcile_write(
                operation_id=lease.operation_id,
                token=lease.token,
                now_iso=now,
            ):
                session.rollback()
                raise self.errors.lease_lost(lease.operation_id)
            task = repo.get_task_snapshot(operation_task_id)
            if task is None:
                session.rollback()
                raise self.errors.task_not_found(operation_task_id)
            unit = repo.get_unit_snapshot(task.operation_unit_id)
            if unit is None or unit.operation_id != lease.operation_id:
                session.rollback()
                raise self.errors.task_operation_mismatch(lease.operation_id, operation_task_id)

            driver = self.driver_factory(session)
            driver.begin_task_submission(
                operation_task_id=operation_task_id,
                submit_attempt_token=submit_attempt_token,
                now_iso=now,
            )
            driver.refresh_unit(operation_unit_id=unit.operation_unit_id, now_iso=now)
            driver.refresh_operation(operation_id=lease.operation_id, now_iso=now)
            if not repo.release_reconcile_lease(
                operation_id=lease.operation_id,
                owner=lease.owner,
                token=lease.token,
                now_iso=now,
            ):
                session.rollback()
                raise self.errors.lease_lost(lease.operation_id)
            session.commit()
            return PreparedSubmission(
                lease.operation_id,
                unit.operation_unit_id,
                operation_task_id,
                submit_attempt_token,
            )

    def refresh_local_aggregate(
        self,
        lease: ReconcileLease,
        *,
        now_iso: str | None = None,
    ) -> str:
        now = now_iso or utc_now_iso()
        with self.session_factory() as session:
            repo = self.repository_factory(session)
            if not repo.fence_reconcile_write(
                operation_id=lease.operation_id,
                token=lease.token,
                now_iso=now,
            ):
                session.rollback()
                raise self.errors.lease_lost(lease.operation_id)
            driver = self.driver_factory(session)
            for unit in repo.list_unit_snapshots(lease.operation_id):
                driver.refresh_unit(operation_unit_id=unit.operation_unit_id, now_iso=now)
            status = driver.refresh_operation(operation_id=lease.operation_id, now_iso=now)
            session.commit()
            return status

    def recover_stale_submissions(
        self,
        lease: ReconcileLease,
        *,
        stale_before_iso: str,
        now_iso: str | None = None,
    ) -> int:
        now = now_iso or utc_now_iso()
        with self.session_factory() as session:
            recovered = self.driver_factory(session).recover_stale_submitting(
                operation_id=lease.operation_id,
                reconcile_token=lease.token,
                stale_before_iso=stale_before_iso,
                now_iso=now,
            )
            session.commit()
            return recovered

    def cancel_operation(self, *, operation_id: str, now_iso: str | None = None) -> dict[str, object]:
        now = now_iso or utc_now_iso()
        with self.session_factory() as session:
            result = self.driver_factory(session).cancel_operation(
                operation_id=operation_id,
                now_iso=now,
            )
            session.commit()
            return dict(result)

    def _assert_read_lease(
        self,
        operation: OperationSnapshot | None,
        lease: ReconcileLease,
        now_dt: object,
    ) -> None:
        if operation is None:
            raise self.errors.operation_not_found(lease.operation_id)
        if (
            operation.status not in ACTIVE_OPERATION_STATUSES
            or operation.reconcile_owner != lease.owner
            or operation.reconcile_token != lease.token
            or not isinstance(operation.reconcile_lease_expires_at, str)
            or parse_iso(operation.reconcile_lease_expires_at) <= now_dt
        ):
            raise self.errors.lease_lost(lease.operation_id)
