from __future__ import annotations

import logging
import os
import socket
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.errors import AppError
from app.core.diagnostic_redaction import sanitize_public_error_details
from app.modules.media.durable_workflow.contracts import ReconcileLease
from app.modules.media.durable_workflow.timing import submit_stale_at, utc_now, utc_now_iso
from app.modules.v4.voice_coordinator import VoiceCoordinator
from app.modules.v4.voice_durable_finalizer import VoiceDurableFinalizer
from app.modules.v4.voice_durable_materializer import VoiceDurableMaterializer
from app.modules.v4.voice_provider_contracts import VoiceProviderRegistry
from app.modules.v4.voice_provider_action_controller import VoiceProviderActionController
from app.modules.v4.voice_provider_execution_pool import VoiceProviderExecutionPool
from app.modules.v4.voice_provider_registry import build_voice_provider_registry
from app.modules.v4.voice_task3 import VoiceTask3AlignmentService
from app.repositories.voice_durable_working import VoiceDurableWorkingRepository


logger = logging.getLogger(__name__)


class VoiceCoordinatorRunner:
    """Bounded Voice domain runner for the shared Durable Media Workflow Host."""

    def __init__(
        self,
        session_factory,
        *,
        owner: str | None = None,
        lease_seconds: int = 15,
        stale_submit_seconds: int = 30,
        error_backoff_seconds: int = 5,
        provider_pool_workers: int = 1,
        provider_pool_max_outstanding: int | None = None,
        registry: VoiceProviderRegistry | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.owner = owner or self._default_owner()
        self.lease_seconds = lease_seconds
        self.stale_submit_seconds = stale_submit_seconds
        self.error_backoff_seconds = error_backoff_seconds
        self.coordinator = VoiceCoordinator(session_factory)
        self.registry = registry or build_voice_provider_registry()
        self.pool = VoiceProviderExecutionPool(
            max_workers=provider_pool_workers,
            max_outstanding=provider_pool_max_outstanding or provider_pool_workers,
        )
        self.provider = VoiceProviderActionController(
            session_factory=session_factory,
            registry=self.registry,
            pool=self.pool,
        )
        self.materializer = VoiceDurableMaterializer(session_factory=session_factory)
        self.finalizer = VoiceDurableFinalizer(session_factory=session_factory)
        self.task3 = VoiceTask3AlignmentService(session_factory=session_factory)

    @staticmethod
    def _default_owner() -> str:
        return f"voice:{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"

    def run_once(self) -> bool:
        self.provider.reap_background_actions()
        lease = self.coordinator.claim_due_operation(owner=self.owner, lease_seconds=self.lease_seconds)
        if lease is None:
            return False
        try:
            action = self.coordinator.plan_next_action(lease)
            if action.action == "submit" and action.operation_task_id:
                reservation = self.pool.try_reserve()
                if reservation is None:
                    if self._harvest_owned_submission(lease.operation_id):
                        self.coordinator.release_lease(lease, require_current=False)
                        return True
                    self._defer_owned_operation(lease, seconds=1)
                    return False
                prepared = None
                try:
                    prepared = self.coordinator.prepare_submission(
                        lease, operation_task_id=action.operation_task_id
                    )
                    dispatched = self.provider.dispatch_submit(
                        prepared.operation_task_id, reservation=reservation
                    )
                    if not dispatched.changed:
                        self._rollback_unstarted_submission(
                            prepared.operation_task_id, prepared.submit_attempt_token
                        )
                    return dispatched.changed
                except Exception:
                    if prepared is None:
                        self.pool.release_reservation(reservation)
                    else:
                        self._rollback_unstarted_submission(
                            prepared.operation_task_id, prepared.submit_attempt_token
                        )
                    raise
            if action.action == "await_submit_commit" and action.operation_task_id:
                return self._handle_awaiting_submission(lease, action.operation_task_id)
            if action.action == "poll" and action.operation_task_id:
                harvested = self.provider.harvest_poll(action.operation_task_id)
                if harvested.state != "not_owned":
                    if harvested.state == "running":
                        self._defer_owned_operation(lease, seconds=1)
                    else:
                        self.coordinator.release_lease(lease, require_current=False)
                    return True
                reservation = self.pool.try_reserve()
                if reservation is None:
                    self._defer_owned_operation(lease, seconds=1)
                    return False
                try:
                    dispatched = self.provider.dispatch_poll(
                        action.operation_task_id, reservation=reservation
                    )
                except Exception:
                    self.pool.release_reservation(reservation)
                    raise
                if dispatched.changed:
                    self.coordinator.release_lease(lease, require_current=False)
                    return True
                self._defer_owned_operation(lease, seconds=1)
                return False
            if action.action == "materialize" and action.operation_task_id:
                logger.info("Voice materialization started operation_id=%s task_id=%s", lease.operation_id, action.operation_task_id)
                self.coordinator.release_lease(lease)
                changed = self.materializer.materialize_task(action.operation_task_id)
                logger.info("Voice materialization finished operation_id=%s task_id=%s changed=%s", lease.operation_id, action.operation_task_id, changed)
                return True
            if action.action == "finalize":
                logger.info("Voice finalization started operation_id=%s", lease.operation_id)
                current_lease = lease

                def renew_finalization_lease() -> None:
                    nonlocal current_lease
                    current_lease = self.coordinator.renew_lease(
                        current_lease,
                        lease_seconds=self.lease_seconds,
                    )

                try:
                    if self.task3.reconcile_operation(lease.operation_id):
                        self.coordinator.release_lease(current_lease, require_current=False)
                        logger.info("Voice Task 3 alignment/finalization turn finished operation_id=%s", lease.operation_id)
                        return True
                    self.finalizer.finalize_operation(
                        lease.operation_id,
                        reconcile_lease=lease,
                        renew_reconcile_lease=renew_finalization_lease,
                    )
                except AppError as exc:
                    if exc.error_code == "VOICE_OPERATION_CANCELLED":
                        self.coordinator.release_lease(current_lease, require_current=False)
                        return True
                    if not exc.retryable:
                        safe_details = sanitize_public_error_details(exc.details)
                        self.coordinator.fail_operation(
                            current_lease,
                            error_code=exc.error_code,
                            error_message=exc.message,
                            error_details=safe_details if isinstance(safe_details, dict) else {"details": safe_details},
                        )
                        return True
                    raise
                self.coordinator.release_lease(current_lease, require_current=False)
                logger.info("Voice finalization finished operation_id=%s", lease.operation_id)
                return True
            if action.action == "cancel":
                task_ids = self._active_task_ids(lease.operation_id)
                self.coordinator.cancel_operation(operation_id=lease.operation_id)
                logger.info(
                    "Voice operation cancelled operation_id=%s active_task_count=%s",
                    lease.operation_id,
                    len(task_ids),
                )
                for task_id in task_ids:
                    try:
                        self.provider.interrupt_and_retire_submit(task_id, reason="cancelled")
                    except Exception:
                        logger.exception(
                            "Voice provider execution interrupt failed",
                            extra={"voice_segment_task_id": task_id, "voice_operation_id": lease.operation_id},
                        )
                for task_id in task_ids:
                    try:
                        self.provider.best_effort_cancel(task_id)
                    except Exception:
                        logger.exception("Best-effort Voice provider cancellation failed", extra={"voice_segment_task_id": task_id})
                return True
            self.coordinator.release_lease(lease, require_current=False)
            return False
        except Exception:
            self._release_best_effort(lease)
            self._defer_operation_after_failure(lease.operation_id)
            raise

    def close(self) -> None:
        self.provider.close()

    def _handle_awaiting_submission(self, lease: ReconcileLease, task_id: str) -> bool:
        harvested = self.provider.harvest_submit(task_id)
        if harvested.state != "not_owned":
            if harvested.state == "running":
                self._defer_owned_operation(lease, seconds=1)
            else:
                self.coordinator.release_lease(lease, require_current=False)
            return True
        reconciled = self.provider.harvest_reconcile_submission(task_id)
        if reconciled.state != "not_owned":
            if reconciled.state == "running":
                self._defer_owned_operation(lease, seconds=1)
            else:
                self.coordinator.release_lease(lease, require_current=False)
            return True
        with self.session_factory() as session:
            task = VoiceDurableWorkingRepository(session).get_task(task_id)
            if task is None or task.status != "submitting" or not task.submit_started_at:
                session.rollback()
                self.coordinator.release_lease(lease, require_current=False)
                return False
            stale_at = submit_stale_at(task.submit_started_at, stale_after_seconds=self.stale_submit_seconds)
        now = utc_now()
        if now < stale_at:
            self._defer_owned_operation_until(lease, stale_at.isoformat())
            return False
        reservation = self.pool.try_reserve()
        if reservation is None:
            self._defer_owned_operation(lease, seconds=1)
            return False
        try:
            dispatched = self.provider.dispatch_reconcile_submission(task_id, reservation=reservation)
        except Exception:
            self.pool.release_reservation(reservation)
            raise
        if dispatched.changed:
            self.coordinator.release_lease(lease, require_current=False)
            return True
        self._defer_owned_operation(lease, seconds=1)
        return False

    def _harvest_owned_submission(self, operation_id: str) -> bool:
        with self.session_factory() as session:
            rows = session.execute(
                text(
                    """
                    SELECT voice_segment_task_id FROM voice_segment_tasks
                    WHERE operation_id = :operation_id AND status = 'submitting'
                    ORDER BY segment_order, voice_segment_task_id
                    """
                ),
                {"operation_id": operation_id},
            ).all()
        changed = False
        for row in rows:
            progress = self.provider.harvest_submit(str(row[0]))
            if progress.state not in {"running", "not_owned"}:
                changed = True
        return changed

    def _rollback_unstarted_submission(self, task_id: str, execution_attempt_token: str) -> None:
        now = utc_now_iso()
        with self.session_factory() as session:
            repository = VoiceDurableWorkingRepository(session)
            task = repository.get_task(task_id)
            if task is None:
                session.rollback()
                return
            if repository.reset_unstarted_submission(
                task_id=task_id,
                execution_attempt_token=execution_attempt_token,
                now_iso=now,
            ):
                from app.modules.v4.voice_durable_workflow import VoiceDurableWorkflowDriver
                VoiceDurableWorkflowDriver(repository).refresh_operation(
                    operation_id=task.operation_id, now_iso=now
                )
                session.commit()
            else:
                session.rollback()

    def _active_task_ids(self, operation_id: str) -> list[str]:
        with self.session_factory() as session:
            rows = session.execute(
                text("""
                    SELECT voice_segment_task_id FROM voice_segment_tasks
                    WHERE operation_id = :operation_id
                      AND status IN ('submitting', 'waiting_provider', 'materializing')
                    ORDER BY segment_order, voice_segment_task_id
                """),
                {"operation_id": operation_id},
            ).all()
        return [str(row[0]) for row in rows]

    def _defer_owned_operation(self, lease: ReconcileLease, *, seconds: int) -> None:
        self._defer_owned_operation_until(lease, (utc_now() + timedelta(seconds=seconds)).isoformat())

    def _defer_owned_operation_until(self, lease: ReconcileLease, next_due: str) -> None:
        now = utc_now_iso()
        with self.session_factory() as session:
            result = session.execute(
                text("""
                    UPDATE voice_generation_operations
                    SET next_reconcile_at = :next_due, updated_at = :now_iso, state_revision = state_revision + 1
                    WHERE operation_id = :operation_id AND reconcile_owner = :owner AND reconcile_token = :token
                      AND reconcile_lease_expires_at IS NOT NULL AND reconcile_lease_expires_at > :now_iso
                """),
                {"operation_id": lease.operation_id, "owner": lease.owner, "token": lease.token, "next_due": next_due, "now_iso": now},
            )
            if int(result.rowcount or 0) != 1:
                session.rollback()
                self.coordinator.release_lease(lease, require_current=False)
                return
            session.commit()
        self.coordinator.release_lease(lease, require_current=False)

    def _release_best_effort(self, lease: ReconcileLease) -> None:
        try:
            self.coordinator.release_lease(lease, require_current=False)
        except Exception:
            logger.exception("Unable to release Voice reconcile lease after failure")

    def _defer_operation_after_failure(self, operation_id: str) -> None:
        now = utc_now()
        now_iso = now.isoformat()
        next_due = (now + timedelta(seconds=self.error_backoff_seconds)).isoformat()
        try:
            with self.session_factory() as session:
                result = session.execute(
                    text("""
                        UPDATE voice_generation_operations
                        SET next_reconcile_at = :next_due, updated_at = :now_iso, state_revision = state_revision + 1
                        WHERE operation_id = :operation_id AND status IN ('queued', 'running', 'finalizing')
                          AND (reconcile_token IS NULL OR reconcile_lease_expires_at IS NULL OR reconcile_lease_expires_at <= :now_iso)
                    """),
                    {"operation_id": operation_id, "next_due": next_due, "now_iso": now_iso},
                )
                if int(result.rowcount or 0) == 1:
                    session.commit()
                else:
                    session.rollback()
        except Exception:
            logger.exception("Unable to defer Voice operation after iteration failure")
