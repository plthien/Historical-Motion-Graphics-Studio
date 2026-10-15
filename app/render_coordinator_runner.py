from __future__ import annotations

import logging
import os
import socket
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event, Thread
from typing import Callable
from uuid import uuid4

from sqlalchemy import event, text
from sqlalchemy.orm import Session

from app.api.errors import AppError
from app.db.enums import JobStatus
from app.modules.v4.render_durable_recovery import (
    INTERRUPTED_RENDER_STATUSES,
    reset_interrupted_render_attempt,
)
from app.modules.v4.render_service import RenderService
from app.repositories import JobRepository
from app.repositories.render_durable_working import RenderDurableWorkingRepository
from app.services.jobs import JobLifecycleService


logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class RenderReconcileLease:
    operation_id: str
    owner: str
    token: str
    expires_at: str


class _RenderLeaseKeepalive:
    """Renew one Render lease while long-running FFmpeg execution is outside DB writes."""

    def __init__(self, runner: "RenderCoordinatorRunner", lease: RenderReconcileLease) -> None:
        self.runner = runner
        self.lease = lease
        self._stop = Event()
        self._lost = Event()
        self._thread = Thread(
            target=self._run,
            name=f"render-lease-{lease.operation_id}",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=max(1.0, min(5.0, self.runner.lease_seconds / 2)))
        if self._thread.is_alive():
            self._mark_lost("Render lease keepalive did not stop cleanly.")
            logger.error(
                "Render lease keepalive did not stop operation_id=%s",
                self.lease.operation_id,
            )

    def assert_not_lost(self) -> None:
        if self._lost.is_set():
            raise AppError(
                "RENDER_EXECUTION_FENCE_LOST",
                "Render durable execution ownership is no longer current.",
                409,
                retryable=True,
                details={"render_operation_id": self.lease.operation_id},
            )

    def _mark_lost(self, reason: str) -> None:
        was_lost = self._lost.is_set()
        self._lost.set()
        if was_lost or self.runner.fatal_execution_loss is None:
            return
        try:
            self.runner.fatal_execution_loss(self.lease.operation_id)
        except Exception:
            logger.exception(
                "Unable to terminate Render child processes after lease loss operation_id=%s",
                self.lease.operation_id,
            )

    def _run(self) -> None:
        interval = max(0.25, min(5.0, self.runner.lease_seconds / 3))
        while not self._stop.wait(interval):
            try:
                if not self.runner._renew_lease(self.lease):
                    self._mark_lost("Render lease renewal lost ownership.")
                    logger.error(
                        "Render lease renewal lost ownership operation_id=%s owner=%s",
                        self.lease.operation_id,
                        self.lease.owner,
                    )
                    return
            except Exception:
                self._mark_lost("Render lease renewal failed.")
                logger.exception(
                    "Render lease renewal failed operation_id=%s owner=%s",
                    self.lease.operation_id,
                    self.lease.owner,
                )
                return


class RenderCoordinatorRunner:
    """One-operation-at-a-time SQLite durable runner for Render.

    This process is intentionally separate from DurableMediaWorkflowHost because
    local FFmpeg work is long-running. Task 3 adds Windows process-tree ownership
    and expired-attempt recovery, but the Windows launcher still leaves RQ as the
    production Render owner until the hard-cutover task.
    """

    def __init__(
        self,
        session_factory,
        *,
        owner: str | None = None,
        lease_seconds: int = 15,
        executor: Callable[[str, str], None] | None = None,
        fatal_execution_loss: Callable[[str], None] | None = None,
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        self.session_factory = session_factory
        self.owner = owner or self._default_owner()
        self.lease_seconds = int(lease_seconds)
        self.executor = executor
        self.fatal_execution_loss = fatal_execution_loss

    @staticmethod
    def _default_owner() -> str:
        return f"render:{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"

    def _claim_due_operation(self) -> RenderReconcileLease | None:
        now = _utc_now()
        now_iso = now.isoformat()
        expires_at = (now + timedelta(seconds=self.lease_seconds)).isoformat()
        with self.session_factory() as session:
            repository = RenderDurableWorkingRepository(session)
            for operation_id in repository.list_due_operation_ids(now_iso=now_iso, limit=20):
                token = uuid4().hex
                if not repository.try_acquire_reconcile_lease(
                    operation_id=operation_id,
                    owner=self.owner,
                    token=token,
                    now_iso=now_iso,
                    expires_at=expires_at,
                ):
                    session.rollback()
                    continue
                session.commit()
                return RenderReconcileLease(
                    operation_id=operation_id,
                    owner=self.owner,
                    token=token,
                    expires_at=expires_at,
                )
        return None

    def _renew_lease(self, lease: RenderReconcileLease) -> bool:
        now = _utc_now()
        now_iso = now.isoformat()
        expires_at = (now + timedelta(seconds=self.lease_seconds)).isoformat()
        with self.session_factory() as session:
            repository = RenderDurableWorkingRepository(session)
            changed = repository.renew_reconcile_lease(
                operation_id=lease.operation_id,
                owner=lease.owner,
                token=lease.token,
                now_iso=now_iso,
                expires_at=expires_at,
            )
            if changed:
                session.commit()
            else:
                session.rollback()
            return changed

    def _fence_required(self, operation_id: str, token: str) -> None:
        with self.session_factory() as session:
            repository = RenderDurableWorkingRepository(session)
            if not repository.fence_reconcile_write(
                operation_id=operation_id,
                token=token,
                now_iso=_utc_now().isoformat(),
            ):
                raise AppError(
                    "RENDER_EXECUTION_FENCE_LOST",
                    "Render durable execution ownership is no longer current.",
                    409,
                    retryable=True,
                    details={"render_operation_id": operation_id},
                )

    @staticmethod
    def _fence_session_commit(
        session: Session,
        lease: RenderReconcileLease,
        keepalive: _RenderLeaseKeepalive,
    ) -> None:
        keepalive.assert_not_lost()
        now_iso = _utc_now().isoformat()
        value = session.connection().execute(
            text(
                """
                SELECT 1
                FROM render_operations
                WHERE render_operation_id = :operation_id
                  AND reconcile_owner = :owner
                  AND reconcile_token = :token
                  AND reconcile_lease_expires_at IS NOT NULL
                  AND reconcile_lease_expires_at > :now_iso
                """
            ),
            {
                "operation_id": lease.operation_id,
                "owner": lease.owner,
                "token": lease.token,
                "now_iso": now_iso,
            },
        ).scalar_one_or_none()
        if value is None:
            raise AppError(
                "RENDER_EXECUTION_FENCE_LOST",
                "Render durable execution ownership is no longer current.",
                409,
                retryable=True,
                details={"render_operation_id": lease.operation_id},
            )

    @staticmethod
    def _cleanup_previous_attempt(service: RenderService, operation) -> None:
        relative = operation.attempt_relative_output_path
        if not relative:
            return
        root = service.artifacts._artifact_root().resolve()
        output = (root / str(relative)).resolve()
        try:
            output.relative_to(root)
        except ValueError as exc:
            raise AppError(
                "RENDER_RECOVERY_PATH_INVALID",
                "Interrupted Render attempt path is outside the artifact root.",
                409,
                retryable=False,
                details={"render_operation_id": operation.render_operation_id},
            ) from exc
        service._cleanup_attempt(Path(output).parent)

    def _prepare_owned_operation(
        self,
        service: RenderService,
        operation,
        job,
    ) -> bool:
        if operation.status == "queued" and job.status == JobStatus.QUEUED:
            return True
        if operation.status not in INTERRUPTED_RENDER_STATUSES or job.status != JobStatus.RUNNING:
            raise AppError(
                "RENDER_OPERATION_NOT_EXECUTABLE",
                "Render operation is not in a durable executable or recoverable state.",
                409,
                retryable=False,
            )
        return reset_interrupted_render_attempt(
            operation,
            job,
            now_iso=_utc_now().isoformat(),
            cleanup_previous_attempt=lambda current: self._cleanup_previous_attempt(service, current),
        )

    def _execute_owned_operation(
        self,
        lease: RenderReconcileLease,
        keepalive: _RenderLeaseKeepalive,
    ) -> None:
        self._fence_required(lease.operation_id, lease.token)
        with self.session_factory() as session:
            def fence_before_commit(current_session: Session) -> None:
                self._fence_session_commit(current_session, lease, keepalive)

            event.listen(session, "before_commit", fence_before_commit)
            try:
                service = RenderService(session)
                operation = service.operations.get(lease.operation_id)
                if operation is None:
                    raise AppError(
                        "RENDER_OPERATION_NOT_FOUND",
                        "Render operation was not found.",
                        404,
                        retryable=False,
                    )
                lifecycle = JobLifecycleService(session, JobRepository(session))
                job = lifecycle.get_required(operation.job_id)
                should_execute = self._prepare_owned_operation(service, operation, job)
                if operation.status != "queued" or job.status != JobStatus.QUEUED:
                    # A persisted cancellation discovered during recovery is a
                    # terminal durable transition, not a new execution attempt.
                    session.commit()
                    if not should_execute:
                        return
                    raise AppError(
                        "RENDER_DURABLE_RECOVERY_INVALID",
                        "Recovered Render state is inconsistent.",
                        409,
                        retryable=False,
                    )
                if operation.attempt_no > 1 and job.attempt > 1:
                    session.commit()
                    keepalive.assert_not_lost()
                lifecycle.mark_running(job)
                session.commit()

                # RQ used to perform the Job claim before entering RenderService.
                # The dedicated durable host preserves that exact domain boundary.
                self._fence_required(lease.operation_id, lease.token)
                RenderService(session).run_operation(lease.operation_id)
            finally:
                event.remove(session, "before_commit", fence_before_commit)

    def _release_best_effort(self, lease: RenderReconcileLease) -> None:
        try:
            with self.session_factory() as session:
                repository = RenderDurableWorkingRepository(session)
                if repository.release_reconcile_lease(
                    operation_id=lease.operation_id,
                    owner=lease.owner,
                    token=lease.token,
                    now_iso=_utc_now().isoformat(),
                ):
                    session.commit()
                else:
                    session.rollback()
        except Exception:
            logger.exception(
                "Unable to release Render reconcile lease operation_id=%s",
                lease.operation_id,
            )

    def run_once(self) -> bool:
        lease = self._claim_due_operation()
        if lease is None:
            return False
        keepalive = _RenderLeaseKeepalive(self, lease)
        keepalive.start()
        try:
            self._fence_required(lease.operation_id, lease.token)
            if self.executor is None:
                self._execute_owned_operation(lease, keepalive)
            else:
                self.executor(lease.operation_id, lease.token)
            keepalive.assert_not_lost()
            return True
        finally:
            keepalive.close()
            self._release_best_effort(lease)
