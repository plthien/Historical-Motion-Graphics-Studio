from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass(frozen=True, slots=True)
class VoiceActiveStatus:
    current_segment_id: str | None
    provider: str | None
    action: str | None
    retry_count: int
    max_attempts: int
    next_retry_at: str | None
    cancel_requested: bool
    problem: dict[str, Any] | None


class VoiceJobProjectionService:
    """Project durable Voice state into generic Job rows and read-model details.

    Voice operation/task tables remain authoritative. Job is updated only from
    durable write transitions; GET paths call ``active_status`` and never mutate.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    @staticmethod
    def _json_for_sql(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        return json.dumps(value, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _json_from_sql(value: Any) -> Any:
        if value is None or isinstance(value, (dict, list)):
            return value
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return {"raw": value}
        return value

    def project_operation(self, *, operation_id: str, now_iso: str) -> None:
        operation = self.session.execute(
            text(
                """
                SELECT operation_id, job_id, status, segment_count,
                       error_code, error_message, error_details_json,
                       cancel_requested_at
                FROM voice_generation_operations
                WHERE operation_id = :operation_id
                """
            ),
            {"operation_id": operation_id},
        ).mappings().one_or_none()
        if operation is None:
            return

        counts = self.session.execute(
            text(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed,
                    SUM(CASE WHEN status IN ('submitting','waiting_provider','materializing') THEN 1 ELSE 0 END) AS active,
                    SUM(CASE WHEN status = 'queued' AND retry_count > 0 THEN 1 ELSE 0 END) AS retry_waiting
                FROM voice_segment_tasks
                WHERE operation_id = :operation_id
                """
            ),
            {"operation_id": operation_id},
        ).mappings().one()

        operation_status = str(operation["status"])
        active_count = int(counts["active"] or 0)
        completed_count = int(counts["completed"] or 0)
        segment_count = max(1, int(operation["segment_count"] or counts["total"] or 1))
        progress = min(90, int(completed_count * 90 / segment_count))

        if operation_status == "completed":
            job_status = "completed"
            progress = 100
        elif operation_status in {"failed", "superseded"}:
            job_status = "failed"
        elif operation_status == "cancelled":
            job_status = "cancelled"
        elif operation_status == "finalizing":
            job_status = "running"
            progress = max(progress, 95)
        elif operation_status == "running" or active_count > 0:
            job_status = "running"
        else:
            job_status = "queued"

        terminal = job_status in {"completed", "failed", "cancelled"}
        running = job_status == "running"
        retryable = bool(counts["retry_waiting"] or 0) and not terminal
        error_code = operation["error_code"] if job_status == "failed" else None
        error_message = operation["error_message"] if job_status == "failed" else None
        error_details = operation["error_details_json"] if job_status == "failed" else None

        self.session.execute(
            text(
                """
                UPDATE jobs
                SET status = :job_status,
                    progress = :progress,
                    retryable = :retryable,
                    error_code = :error_code,
                    error_message = :error_message,
                    error_details_json = :error_details_json,
                    started_at = CASE WHEN :running = 1 THEN COALESCE(started_at, :now_iso) ELSE started_at END,
                    heartbeat_at = CASE WHEN :running = 1 THEN :now_iso ELSE heartbeat_at END,
                    finished_at = CASE WHEN :terminal = 1 THEN COALESCE(finished_at, :now_iso) ELSE NULL END,
                    completed_at = CASE WHEN :terminal = 1 THEN COALESCE(completed_at, :now_iso) ELSE NULL END,
                    updated_at = :now_iso
                WHERE job_id = :job_id
                """
            ),
            {
                "job_id": str(operation["job_id"]),
                "job_status": job_status,
                "progress": progress,
                "retryable": 1 if retryable else 0,
                "error_code": error_code,
                "error_message": error_message,
                "error_details_json": self._json_for_sql(error_details),
                "running": 1 if running else 0,
                "terminal": 1 if terminal else 0,
                "now_iso": now_iso,
            },
        )

    def active_status(self, *, operation_id: str) -> VoiceActiveStatus:
        operation = self.session.execute(
            text(
                """
                SELECT status, cancel_requested_at, error_code, error_message, error_details_json
                FROM voice_generation_operations
                WHERE operation_id = :operation_id
                """
            ),
            {"operation_id": operation_id},
        ).mappings().one_or_none()
        if operation is None:
            return VoiceActiveStatus(None, None, None, 0, 0, None, False, None)

        task = self.session.execute(
            text(
                """
                SELECT segment_id, provider, status, provider_status,
                       retry_count, max_attempts, next_reconcile_at,
                       error_code, error_message, error_details_json
                FROM voice_segment_tasks
                WHERE operation_id = :operation_id
                  AND status IN (
                      'submitting','waiting_provider','materializing','submit_outcome_unknown','queued','failed'
                  )
                ORDER BY
                    CASE status
                        WHEN 'submitting' THEN 0
                        WHEN 'waiting_provider' THEN 1
                        WHEN 'materializing' THEN 2
                        WHEN 'submit_outcome_unknown' THEN 3
                        WHEN 'queued' THEN 4
                        ELSE 5
                    END,
                    segment_order,
                    voice_segment_task_id
                LIMIT 1
                """
            ),
            {"operation_id": operation_id},
        ).mappings().one_or_none()

        operation_status = str(operation["status"])
        cancel_requested = operation["cancel_requested_at"] is not None
        if cancel_requested and operation_status not in {"completed", "failed", "cancelled", "superseded"}:
            action = "cancel"
        elif operation_status == "finalizing":
            action = "finalize"
        elif task is None:
            action = None
        else:
            task_status = str(task["status"])
            action = {
                "submitting": "submit",
                "waiting_provider": "poll",
                "materializing": "materialize",
                "submit_outcome_unknown": "submission_outcome_unknown",
                "failed": "failed",
            }.get(task_status)
            if action is None and task_status == "queued":
                action = "retry_wait" if int(task["retry_count"] or 0) > 0 and task["next_reconcile_at"] else "submit"

        problem = None
        if task is not None and task["error_code"]:
            problem = {
                "error_code": str(task["error_code"]),
                "message": str(task["error_message"] or "Voice segment processing failed."),
                "details": self._json_from_sql(task["error_details_json"]),
            }
        elif operation["error_code"]:
            problem = {
                "error_code": str(operation["error_code"]),
                "message": str(operation["error_message"] or "Voice operation failed."),
                "details": self._json_from_sql(operation["error_details_json"]),
            }

        return VoiceActiveStatus(
            current_segment_id=str(task["segment_id"]) if task is not None else None,
            provider=str(task["provider"]) if task is not None and task["provider"] else None,
            action=action,
            retry_count=int(task["retry_count"] or 0) if task is not None else 0,
            max_attempts=int(task["max_attempts"] or 0) if task is not None else 0,
            next_retry_at=(
                str(task["next_reconcile_at"])
                if task is not None and str(task["status"]) == "queued" and int(task["retry_count"] or 0) > 0 and task["next_reconcile_at"]
                else None
            ),
            cancel_requested=cancel_requested,
            problem=problem,
        )
