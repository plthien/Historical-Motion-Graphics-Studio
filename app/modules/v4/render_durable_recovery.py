from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from app.api.errors import AppError
from app.db.enums import JobStatus


INTERRUPTED_RENDER_STATUSES = {
    "running",
    "rendering",
    "probing",
    "validating",
    "finalizing",
}


def reset_interrupted_render_attempt(
    operation: Any,
    job: Any,
    *,
    now_iso: str,
    cleanup_previous_attempt: Callable[[Any], None],
) -> bool:
    """Reset one expired durable Render attempt under an already-held lease.

    Returns True when the operation should execute a new attempt immediately.
    A persisted cancellation request is terminalized instead and returns False.
    The caller owns the fenced transaction/commit.
    """

    if operation.status not in INTERRUPTED_RENDER_STATUSES or job.status != JobStatus.RUNNING:
        raise AppError(
            "RENDER_DURABLE_RECOVERY_INVALID",
            "Only an interrupted running Render attempt may be recovered.",
            409,
            retryable=False,
            details={"render_operation_id": operation.render_operation_id},
        )

    cleanup_previous_attempt(operation)
    operation.process_pid = None
    operation.process_started_at = None
    operation.worker_instance_id = None
    operation.attempt_token = None
    operation.attempt_relative_output_path = None
    operation.recovery_enqueued = False
    request_json = deepcopy(operation.request_json) if isinstance(operation.request_json, dict) else {}
    request_json.pop("active_process", None)
    operation.request_json = request_json

    if operation.cancellation_requested:
        operation.status = "cancelled"
        operation.progress_percent = max(0, min(100, int(operation.progress_percent or 0)))
        operation.error_code = None
        operation.error_message = None
        operation.error_details_json = None
        operation.updated_at = now_iso
        operation.completed_at = now_iso

        job.status = JobStatus.CANCELLED
        job.retryable = False
        job.updated_at = now_iso
        job.heartbeat_at = now_iso
        job.finished_at = now_iso
        job.completed_at = now_iso
        job.error_code = "JOB_CANCELLED"
        job.error_message = "Render operation cancelled during durable recovery."
        job.error_details_json = {"reason": job.error_message}
        return False

    operation.status = "queued"
    operation.attempt_no += 1
    operation.cancellation_requested = False
    operation.progress_time_units = 0
    operation.progress_frame = 0
    operation.progress_percent = 0
    operation.error_code = None
    operation.error_message = None
    operation.error_details_json = None
    operation.completed_at = None
    operation.updated_at = now_iso
    operation.next_reconcile_at = now_iso
    operation.state_revision = int(operation.state_revision or 0) + 1

    job.status = JobStatus.QUEUED
    job.progress = 0
    job.attempt += 1
    job.retryable = False
    job.error_code = None
    job.error_message = None
    job.error_details_json = None
    job.started_at = None
    job.finished_at = None
    job.completed_at = None
    job.heartbeat_at = None
    job.updated_at = now_iso
    return True
