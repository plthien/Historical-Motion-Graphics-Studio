from __future__ import annotations

from typing import Mapping

from app.api.errors import AppError
from app.modules.v4.voice_job_projection import VoiceJobProjectionService
from app.repositories.voice_durable_working import VoiceDurableWorkingRepository


class VoiceDurableWorkflowErrors:
    @staticmethod
    def lease_lost(operation_id: str) -> Exception:
        return AppError(
            "VOICE_RECONCILE_LEASE_LOST",
            "The durable Voice reconcile lease is no longer current.",
            409,
            retryable=True,
            details={"operation_id": operation_id},
        )

    @staticmethod
    def operation_not_found(operation_id: str) -> Exception:
        return AppError("VOICE_OPERATION_NOT_FOUND", "Voice operation was not found.", 404, retryable=False)

    @staticmethod
    def task_not_found(operation_task_id: str) -> Exception:
        return AppError(
            "VOICE_SEGMENT_TASK_NOT_FOUND",
            "Voice segment task was not found.",
            404,
            retryable=False,
            details={"voice_segment_task_id": operation_task_id},
        )

    @staticmethod
    def task_operation_mismatch(operation_id: str, operation_task_id: str) -> Exception:
        return AppError(
            "VOICE_SEGMENT_TASK_OPERATION_MISMATCH",
            "Voice segment task does not belong to the reconciled operation.",
            409,
            retryable=False,
            details={"operation_id": operation_id, "voice_segment_task_id": operation_task_id},
        )


class VoiceDurableWorkflowDriver:
    """Voice-specific durable mutations used by the neutral coordinator."""

    def __init__(self, repository: VoiceDurableWorkingRepository) -> None:
        self.repository = repository
        self.job_projection = VoiceJobProjectionService(repository.session)

    def begin_task_submission(
        self,
        *,
        operation_task_id: str,
        submit_attempt_token: str,
        now_iso: str,
    ) -> None:
        task = self.repository.get_task(operation_task_id)
        if task is None:
            raise VoiceDurableWorkflowErrors.task_not_found(operation_task_id)
        if not self.repository.begin_task_submission(
            task_id=operation_task_id,
            attempt_token=submit_attempt_token,
            now_iso=now_iso,
        ):
            raise AppError(
                "VOICE_SEGMENT_TASK_NOT_SUBMITTABLE",
                "Voice segment task is no longer queued for submission.",
                409,
                retryable=True,
                details={"voice_segment_task_id": operation_task_id},
            )
        self.job_projection.project_operation(operation_id=task.operation_id, now_iso=now_iso)

    def refresh_unit(self, *, operation_unit_id: str, now_iso: str) -> None:
        if self.repository.get_task(operation_unit_id) is None:
            raise VoiceDurableWorkflowErrors.task_not_found(operation_unit_id)

    def refresh_operation(self, *, operation_id: str, now_iso: str) -> str:
        operation = self.repository.get_operation(operation_id)
        if operation is None:
            raise VoiceDurableWorkflowErrors.operation_not_found(operation_id)
        if operation.cancel_requested_at is not None:
            self.job_projection.project_operation(operation_id=operation_id, now_iso=now_iso)
            return operation.status

        tasks = self.repository.list_tasks_for_operation(operation_id)
        if not tasks:
            # Valid for generate-missing/regeneration operations whose full
            # segment set was satisfied by immutable compatible reuse.
            status = "finalizing"
            next_reconcile_at = now_iso
        else:
            statuses = {task.status for task in tasks}
            if statuses == {"completed"}:
                status = "finalizing"
                next_reconcile_at = now_iso
            elif "failed" in statuses and not any(
                value in {"queued", "submitting", "waiting_provider", "materializing"} for value in statuses
            ):
                status = "failed"
                next_reconcile_at = None
            else:
                status = "running"
                due_values = sorted(task.next_reconcile_at for task in tasks if task.next_reconcile_at is not None)
                next_reconcile_at = due_values[0] if due_values else now_iso
                if "submit_outcome_unknown" in statuses and not any(
                    value in {"queued", "submitting", "waiting_provider", "materializing"} for value in statuses
                ):
                    next_reconcile_at = None

        self.repository.set_operation_status(
            operation_id=operation_id,
            status=status,
            next_reconcile_at=next_reconcile_at,
            now_iso=now_iso,
        )
        self.job_projection.project_operation(operation_id=operation_id, now_iso=now_iso)
        return status

    def recover_stale_submitting(
        self,
        *,
        operation_id: str,
        reconcile_token: str,
        stale_before_iso: str,
        now_iso: str,
    ) -> int:
        if not self.repository.fence_reconcile_write(
            operation_id=operation_id,
            token=reconcile_token,
            now_iso=now_iso,
        ):
            raise VoiceDurableWorkflowErrors.lease_lost(operation_id)
        recovered = 0
        for task in self.repository.list_stale_submitting_tasks(
            operation_id=operation_id,
            stale_before_iso=stale_before_iso,
        ):
            if self.repository.mark_submit_outcome_unknown(
                task_id=task.voice_segment_task_id,
                expected_submit_token=task.submit_attempt_token,
                now_iso=now_iso,
            ):
                recovered += 1
        self.refresh_operation(operation_id=operation_id, now_iso=now_iso)
        return recovered

    def fail_operation(
        self,
        *,
        operation_id: str,
        reconcile_owner: str,
        reconcile_token: str,
        error_code: str,
        error_message: str,
        error_details: dict[str, object] | None,
        now_iso: str,
    ) -> None:
        if not self.repository.fail_operation_from_reconcile(
            operation_id=operation_id,
            owner=reconcile_owner,
            token=reconcile_token,
            error_code=error_code,
            error_message=error_message,
            error_details=dict(error_details or {}),
            now_iso=now_iso,
        ):
            raise VoiceDurableWorkflowErrors.lease_lost(operation_id)
        self.job_projection.project_operation(operation_id=operation_id, now_iso=now_iso)

    def cancel_operation(self, *, operation_id: str, now_iso: str) -> Mapping[str, object]:
        operation = self.repository.get_operation(operation_id)
        if operation is None:
            raise VoiceDurableWorkflowErrors.operation_not_found(operation_id)
        if operation.status in {"completed", "failed", "cancelled", "superseded"}:
            self.job_projection.project_operation(operation_id=operation_id, now_iso=now_iso)
            return {"operation_id": operation_id, "status": operation.status, "cancelled_task_count": 0}

        self.repository.request_cancel(operation_id=operation_id, now_iso=now_iso)
        cancelled_task_count = self.repository.cancel_tasks(operation_id=operation_id, now_iso=now_iso)
        self.repository.finish_cancel(operation_id=operation_id, now_iso=now_iso)
        self.job_projection.project_operation(operation_id=operation_id, now_iso=now_iso)
        return {
            "operation_id": operation_id,
            "status": "cancelled",
            "cancelled_task_count": cancelled_task_count,
        }
