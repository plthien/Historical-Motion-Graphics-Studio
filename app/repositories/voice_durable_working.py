from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


ACTIVE_OPERATION_STATUSES = ("queued", "running", "finalizing")


@dataclass(frozen=True, slots=True)
class DurableVoiceOperationRow:
    operation_id: str
    status: str
    next_reconcile_at: str | None
    cancel_requested_at: str | None
    reconcile_owner: str | None
    reconcile_token: str | None
    reconcile_lease_expires_at: str | None


@dataclass(frozen=True, slots=True)
class DurableVoiceTaskRow:
    voice_segment_task_id: str
    operation_id: str
    status: str
    next_reconcile_at: str | None
    submit_started_at: str | None
    submit_attempt_token: str | None
    execution_attempt_token: str | None
    provider_task_id: str | None
    provider_status: str | None
    provider_handle_json: str | None
    attempt_no: int
    retry_count: int
    max_attempts: int


class VoiceDurableWorkingRepository:
    """Durable execution storage over the existing Voice physical tables."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def list_due_operation_ids(self, *, now_iso: str, limit: int = 50) -> list[str]:
        rows = self.session.execute(
            text(
                """
                SELECT operation_id
                FROM voice_generation_operations
                WHERE status IN ('queued', 'running', 'finalizing')
                  AND next_reconcile_at IS NOT NULL
                  AND next_reconcile_at <= :now_iso
                ORDER BY COALESCE(next_reconcile_at, created_at), created_at, operation_id
                LIMIT :limit
                """
            ),
            {"now_iso": now_iso, "limit": int(limit)},
        ).all()
        return [str(row[0]) for row in rows]

    def get_operation(self, operation_id: str) -> DurableVoiceOperationRow | None:
        row = self.session.execute(
            text(
                """
                SELECT operation_id, status, next_reconcile_at, cancel_requested_at,
                       reconcile_owner, reconcile_token, reconcile_lease_expires_at
                FROM voice_generation_operations
                WHERE operation_id = :operation_id
                """
            ),
            {"operation_id": operation_id},
        ).mappings().one_or_none()
        return self._operation(row) if row is not None else None

    def list_tasks_for_operation(self, operation_id: str) -> list[DurableVoiceTaskRow]:
        rows = self.session.execute(
            text(
                """
                SELECT voice_segment_task_id, operation_id, status, next_reconcile_at,
                       submit_started_at, submit_attempt_token, execution_attempt_token,
                       provider_task_id, provider_status, provider_handle_json,
                       attempt_no, retry_count, max_attempts
                FROM voice_segment_tasks
                WHERE operation_id = :operation_id
                ORDER BY segment_order, attempt_no, voice_segment_task_id
                """
            ),
            {"operation_id": operation_id},
        ).mappings().all()
        return [self._task(row) for row in rows]

    def get_task(self, task_id: str) -> DurableVoiceTaskRow | None:
        row = self.session.execute(
            text(
                """
                SELECT voice_segment_task_id, operation_id, status, next_reconcile_at,
                       submit_started_at, submit_attempt_token, execution_attempt_token,
                       provider_task_id, provider_status, provider_handle_json,
                       attempt_no, retry_count, max_attempts
                FROM voice_segment_tasks
                WHERE voice_segment_task_id = :task_id
                """
            ),
            {"task_id": task_id},
        ).mappings().one_or_none()
        return self._task(row) if row is not None else None

    def list_stale_submitting_tasks(self, *, operation_id: str, stale_before_iso: str) -> list[DurableVoiceTaskRow]:
        rows = self.session.execute(
            text(
                """
                SELECT voice_segment_task_id, operation_id, status, next_reconcile_at,
                       submit_started_at, submit_attempt_token, execution_attempt_token,
                       provider_task_id, provider_status, provider_handle_json,
                       attempt_no, retry_count, max_attempts
                FROM voice_segment_tasks
                WHERE operation_id = :operation_id
                  AND status = 'submitting'
                  AND submit_started_at IS NOT NULL
                  AND submit_started_at <= :stale_before_iso
                ORDER BY submit_started_at, voice_segment_task_id
                """
            ),
            {"operation_id": operation_id, "stale_before_iso": stale_before_iso},
        ).mappings().all()
        return [self._task(row) for row in rows]

    def try_acquire_reconcile_lease(
        self,
        *,
        operation_id: str,
        owner: str,
        token: str,
        now_iso: str,
        expires_at: str,
    ) -> bool:
        result = self.session.execute(
            text(
                """
                UPDATE voice_generation_operations
                SET reconcile_owner = :owner,
                    reconcile_token = :token,
                    reconcile_lease_expires_at = :expires_at,
                    updated_at = :now_iso,
                    state_revision = state_revision + 1
                WHERE operation_id = :operation_id
                  AND status IN ('queued', 'running', 'finalizing')
                  AND (
                        reconcile_token IS NULL
                        OR reconcile_lease_expires_at IS NULL
                        OR reconcile_lease_expires_at <= :now_iso
                      )
                """
            ),
            {
                "operation_id": operation_id,
                "owner": owner,
                "token": token,
                "now_iso": now_iso,
                "expires_at": expires_at,
            },
        )
        return int(result.rowcount or 0) == 1

    def renew_reconcile_lease(
        self,
        *,
        operation_id: str,
        owner: str,
        token: str,
        now_iso: str,
        expires_at: str,
    ) -> bool:
        result = self.session.execute(
            text(
                """
                UPDATE voice_generation_operations
                SET reconcile_lease_expires_at = :expires_at,
                    updated_at = :now_iso,
                    state_revision = state_revision + 1
                WHERE operation_id = :operation_id
                  AND status IN ('queued', 'running', 'finalizing')
                  AND reconcile_owner = :owner
                  AND reconcile_token = :token
                  AND reconcile_lease_expires_at > :now_iso
                """
            ),
            {
                "operation_id": operation_id,
                "owner": owner,
                "token": token,
                "now_iso": now_iso,
                "expires_at": expires_at,
            },
        )
        return int(result.rowcount or 0) == 1

    def release_reconcile_lease(
        self,
        *,
        operation_id: str,
        owner: str,
        token: str,
        now_iso: str,
    ) -> bool:
        result = self.session.execute(
            text(
                """
                UPDATE voice_generation_operations
                SET reconcile_owner = NULL,
                    reconcile_token = NULL,
                    reconcile_lease_expires_at = NULL,
                    updated_at = :now_iso,
                    state_revision = state_revision + 1
                WHERE operation_id = :operation_id
                  AND reconcile_owner = :owner
                  AND reconcile_token = :token
                """
            ),
            {"operation_id": operation_id, "owner": owner, "token": token, "now_iso": now_iso},
        )
        return int(result.rowcount or 0) == 1

    def fence_reconcile_write(self, *, operation_id: str, token: str, now_iso: str) -> bool:
        value = self.session.execute(
            text(
                """
                SELECT 1
                FROM voice_generation_operations
                WHERE operation_id = :operation_id
                  AND status IN ('queued', 'running', 'finalizing')
                  AND reconcile_token = :token
                  AND reconcile_lease_expires_at IS NOT NULL
                  AND reconcile_lease_expires_at > :now_iso
                """
            ),
            {"operation_id": operation_id, "token": token, "now_iso": now_iso},
        ).scalar_one_or_none()
        return value is not None

    def schedule_operation(self, *, operation_id: str, now_iso: str) -> None:
        self.session.execute(
            text(
                """
                UPDATE voice_generation_operations
                SET next_reconcile_at = :now_iso,
                    updated_at = :now_iso,
                    state_revision = state_revision + 1
                WHERE operation_id = :operation_id
                """
            ),
            {"operation_id": operation_id, "now_iso": now_iso},
        )

    def fail_operation_from_reconcile(
        self,
        *,
        operation_id: str,
        owner: str,
        token: str,
        error_code: str,
        error_message: str,
        error_details: dict[str, Any] | None,
        now_iso: str,
    ) -> bool:
        result = self.session.execute(
            text(
                """
                UPDATE voice_generation_operations
                SET status = 'failed',
                    error_code = :error_code,
                    error_message = :error_message,
                    error_details_json = :error_details_json,
                    completed_at = :now_iso,
                    next_reconcile_at = NULL,
                    reconcile_owner = NULL,
                    reconcile_token = NULL,
                    reconcile_lease_expires_at = NULL,
                    updated_at = :now_iso,
                    state_revision = state_revision + 1
                WHERE operation_id = :operation_id
                  AND status IN ('queued', 'running', 'finalizing')
                  AND reconcile_owner = :owner
                  AND reconcile_token = :token
                  AND reconcile_lease_expires_at IS NOT NULL
                  AND reconcile_lease_expires_at > :now_iso
                """
            ),
            {
                "operation_id": operation_id,
                "owner": owner,
                "token": token,
                "error_code": error_code,
                "error_message": error_message,
                "error_details_json": json.dumps(error_details or {}, sort_keys=True, separators=(",", ":")),
                "now_iso": now_iso,
            },
        )
        return int(result.rowcount or 0) == 1

    def begin_task_submission(self, *, task_id: str, attempt_token: str, now_iso: str) -> bool:
        result = self.session.execute(
            text(
                """
                UPDATE voice_segment_tasks
                SET status = 'submitting',
                    submit_attempt_token = :attempt_token,
                    submit_started_at = :now_iso,
                    execution_attempt_token = :attempt_token,
                    execution_started_at = NULL,
                    provider_status = 'submitting',
                    next_reconcile_at = NULL,
                    updated_at = :now_iso,
                    state_revision = state_revision + 1
                WHERE voice_segment_task_id = :task_id
                  AND status = 'queued'
                """
            ),
            {"task_id": task_id, "attempt_token": attempt_token, "now_iso": now_iso},
        )
        return int(result.rowcount or 0) == 1

    def mark_execution_started(self, *, task_id: str, execution_attempt_token: str, now_iso: str) -> bool:
        result = self.session.execute(
            text(
                """
                UPDATE voice_segment_tasks
                SET execution_started_at = COALESCE(execution_started_at, :now_iso),
                    updated_at = :now_iso,
                    state_revision = state_revision + 1
                WHERE voice_segment_task_id = :task_id
                  AND execution_attempt_token = :execution_attempt_token
                  AND status IN ('submitting', 'waiting_provider')
                  AND EXISTS (
                      SELECT 1 FROM voice_generation_operations o
                      WHERE o.operation_id = voice_segment_tasks.operation_id
                        AND o.cancel_requested_at IS NULL
                        AND o.status IN ('queued', 'running', 'finalizing')
                  )
                """
            ),
            {
                "task_id": task_id,
                "execution_attempt_token": execution_attempt_token,
                "now_iso": now_iso,
            },
        )
        return int(result.rowcount or 0) == 1

    def reset_unstarted_submission(self, *, task_id: str, execution_attempt_token: str, now_iso: str) -> bool:
        result = self.session.execute(
            text(
                """
                UPDATE voice_segment_tasks
                SET status = 'queued',
                    submit_attempt_token = NULL,
                    submit_started_at = NULL,
                    execution_attempt_token = NULL,
                    execution_started_at = NULL,
                    provider_status = NULL,
                    next_reconcile_at = :now_iso,
                    updated_at = :now_iso,
                    state_revision = state_revision + 1
                WHERE voice_segment_task_id = :task_id
                  AND status = 'submitting'
                  AND execution_attempt_token = :execution_attempt_token
                  AND provider_task_id IS NULL
                  AND provider_submitted_at IS NULL
                """
            ),
            {
                "task_id": task_id,
                "execution_attempt_token": execution_attempt_token,
                "now_iso": now_iso,
            },
        )
        return int(result.rowcount or 0) == 1

    def mark_submit_outcome_unknown(self, *, task_id: str, expected_submit_token: str | None, now_iso: str) -> bool:
        conditions = "submit_attempt_token IS NULL" if expected_submit_token is None else "submit_attempt_token = :submit_token"
        params: dict[str, Any] = {"task_id": task_id, "now_iso": now_iso}
        if expected_submit_token is not None:
            params["submit_token"] = expected_submit_token
        result = self.session.execute(
            text(
                f"""
                UPDATE voice_segment_tasks
                SET status = 'submit_outcome_unknown',
                    provider_status = 'submit_outcome_unknown',
                    next_reconcile_at = NULL,
                    error_code = 'VOICE_PROVIDER_SUBMISSION_OUTCOME_UNKNOWN',
                    error_message = 'Provider submission outcome could not be proven after interruption.',
                    updated_at = :now_iso,
                    state_revision = state_revision + 1
                WHERE voice_segment_task_id = :task_id
                  AND status = 'submitting'
                  AND {conditions}
                """
            ),
            params,
        )
        return int(result.rowcount or 0) == 1

    def request_cancel(self, *, operation_id: str, now_iso: str) -> bool:
        result = self.session.execute(
            text(
                """
                UPDATE voice_generation_operations
                SET cancel_requested_at = COALESCE(cancel_requested_at, :now_iso),
                    next_reconcile_at = :now_iso,
                    updated_at = :now_iso,
                    state_revision = state_revision + 1
                WHERE operation_id = :operation_id
                  AND status IN ('queued', 'running', 'finalizing')
                """
            ),
            {"operation_id": operation_id, "now_iso": now_iso},
        )
        return int(result.rowcount or 0) == 1

    def cancel_tasks(self, *, operation_id: str, now_iso: str) -> int:
        result = self.session.execute(
            text(
                """
                UPDATE voice_segment_tasks
                SET status = 'cancelled',
                    provider_status = 'cancelled',
                    next_reconcile_at = NULL,
                    completed_at = COALESCE(completed_at, :now_iso),
                    updated_at = :now_iso,
                    state_revision = state_revision + 1
                WHERE operation_id = :operation_id
                  AND status NOT IN ('completed', 'failed', 'cancelled')
                """
            ),
            {"operation_id": operation_id, "now_iso": now_iso},
        )
        return int(result.rowcount or 0)

    def finish_cancel(self, *, operation_id: str, now_iso: str) -> None:
        self.session.execute(
            text(
                """
                UPDATE voice_generation_operations
                SET status = 'cancelled',
                    cancelled_at = COALESCE(cancelled_at, :now_iso),
                    completed_at = COALESCE(completed_at, :now_iso),
                    next_reconcile_at = NULL,
                    reconcile_owner = NULL,
                    reconcile_token = NULL,
                    reconcile_lease_expires_at = NULL,
                    updated_at = :now_iso,
                    state_revision = state_revision + 1
                WHERE operation_id = :operation_id
                """
            ),
            {"operation_id": operation_id, "now_iso": now_iso},
        )

    def set_operation_status(self, *, operation_id: str, status: str, next_reconcile_at: str | None, now_iso: str) -> None:
        self.session.execute(
            text(
                """
                UPDATE voice_generation_operations
                SET status = :status,
                    next_reconcile_at = :next_reconcile_at,
                    updated_at = :now_iso,
                    state_revision = state_revision + 1
                WHERE operation_id = :operation_id
                """
            ),
            {
                "operation_id": operation_id,
                "status": status,
                "next_reconcile_at": next_reconcile_at,
                "now_iso": now_iso,
            },
        )

    @staticmethod
    def _operation(row: Any) -> DurableVoiceOperationRow:
        return DurableVoiceOperationRow(
            operation_id=str(row["operation_id"]),
            status=str(row["status"]),
            next_reconcile_at=row["next_reconcile_at"],
            cancel_requested_at=row["cancel_requested_at"],
            reconcile_owner=row["reconcile_owner"],
            reconcile_token=row["reconcile_token"],
            reconcile_lease_expires_at=row["reconcile_lease_expires_at"],
        )

    @staticmethod
    def _task(row: Any) -> DurableVoiceTaskRow:
        return DurableVoiceTaskRow(
            voice_segment_task_id=str(row["voice_segment_task_id"]),
            operation_id=str(row["operation_id"]),
            status=str(row["status"]),
            next_reconcile_at=row["next_reconcile_at"],
            submit_started_at=row["submit_started_at"],
            submit_attempt_token=row["submit_attempt_token"],
            execution_attempt_token=row["execution_attempt_token"],
            provider_task_id=row["provider_task_id"],
            provider_status=row["provider_status"],
            provider_handle_json=row["provider_handle_json"],
            attempt_no=int(row["attempt_no"]),
            retry_count=int(row["retry_count"]),
            max_attempts=int(row["max_attempts"]),
        )
