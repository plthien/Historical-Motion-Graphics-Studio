from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from sqlalchemy.orm import Session

from app.api.errors import AppError
from app.repositories.visual_reference_working import (
    ACTIVE_OPERATION_ANCHOR_STATUSES,
    ACTIVE_OPERATION_STATUSES,
    ACTIVE_TASK_STATUSES,
    TERMINAL_OPERATION_ANCHOR_STATUSES,
    TERMINAL_OPERATION_STATUSES,
    TERMINAL_TASK_STATUSES,
    VisualReferenceWorkingRepository,
)

_OPERATION_TRANSITIONS: dict[str, set[str]] = {
    "queued": {"running", "finalizing", "failed", "cancelled", "superseded"},
    "running": {"finalizing", "failed", "cancelled", "superseded"},
    "finalizing": {"completed", "partial", "failed", "cancelled", "superseded"},
    "completed": set(),
    "partial": set(),
    "failed": set(),
    "cancelled": set(),
    "superseded": set(),
}
_ANCHOR_TRANSITIONS: dict[str, set[str]] = {
    "queued": {"running", "failed", "cancelled"},
    "running": {"candidate_ready", "failed", "cancelled"},
    "candidate_ready": set(),
    "failed": set(),
    "cancelled": set(),
}
_TASK_TRANSITIONS: dict[str, set[str]] = {
    "queued": {"submitting", "failed", "cancelled"},
    "submitting": {"queued", "waiting_provider", "submit_outcome_unknown", "failed", "cancelled"},
    "submit_outcome_unknown": set(),
    "waiting_provider": {"downloading", "failed", "cancelled"},
    "downloading": {"materializing", "failed", "cancelled"},
    "materializing": {"completed", "failed", "cancelled"},
    "completed": set(),
    "failed": set(),
    "cancelled": set(),
}


@dataclass(frozen=True, slots=True)
class VisualReferenceMaterializationFence:
    operation_id: str
    operation_anchor_id: str
    operation_task_id: str
    project_id: str
    anchor_type: str
    anchor_id: str
    prompt_revision_id: str
    anchor_state_version: int


class VisualReferenceWorkingStateService:
    """Visual Reference V2 transactional state machine with no external I/O."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = VisualReferenceWorkingRepository(session)

    @staticmethod
    def _error(code: str, message: str, *, retryable: bool = False, status_code: int = 409, **details: Any) -> AppError:
        return AppError(code, message, status_code, retryable=retryable, details=details)

    @classmethod
    def _not_found(cls, entity: str, identity: str) -> AppError:
        return cls._error(
            "VISUAL_REFERENCE_V2_STATE_NOT_FOUND",
            f"Visual Reference V2 {entity} was not found.",
            status_code=404,
            id=identity,
        )

    @classmethod
    def _integrity(cls, message: str, **details: Any) -> AppError:
        return cls._error("VISUAL_REFERENCE_V2_STATE_INTEGRITY_INVALID", message, **details)

    @classmethod
    def _invalid_transition(cls, entity: str, identity: str, source: str, target: str) -> AppError:
        return cls._error(
            "VISUAL_REFERENCE_V2_STATE_TRANSITION_INVALID",
            f"Invalid Visual Reference V2 {entity} state transition.",
            id=identity,
            source=source,
            target=target,
        )

    def compare_and_set_anchor_state(
        self,
        *,
        project_id: str,
        anchor_type: str,
        anchor_id: str,
        expected_version: int,
        values: dict[str, Any],
        now_iso: str,
    ) -> int:
        if not self.repo.compare_and_set_anchor_state(
            project_id=project_id,
            anchor_type=anchor_type,
            anchor_id=anchor_id,
            expected_version=expected_version,
            values=values,
            updated_at=now_iso,
        ):
            raise self._error(
                "VISUAL_REFERENCE_V2_ANCHOR_STATE_CONFLICT",
                "Visual Reference anchor state changed before this mutation committed.",
                retryable=True,
                project_id=project_id,
                anchor_type=anchor_type,
                anchor_id=anchor_id,
                expected_version=expected_version,
            )
        self.session.flush()
        state = self.repo.get_anchor_state(project_id, anchor_type, anchor_id)
        if state is None:
            raise self._not_found("anchor state", anchor_id)
        return int(state.version)

    def claim_anchor_for_operation(self, *, operation_anchor_id: str, expected_version: int, now_iso: str) -> int:
        unit = self.repo.get_operation_anchor(operation_anchor_id)
        if unit is None:
            raise self._not_found("operation anchor", operation_anchor_id)
        operation = self.repo.get_operation(unit.operation_id)
        state = self.repo.get_anchor_state(unit.project_id, unit.anchor_type, unit.anchor_id)
        if (
            operation is None
            or operation.status not in ACTIVE_OPERATION_STATUSES
            or operation.cancel_requested_at is not None
            or unit.status not in ACTIVE_OPERATION_ANCHOR_STATUSES
            or state is None
        ):
            raise self._integrity(
                "Only an active Visual Reference operation anchor may own AnchorState.",
                operation_anchor_id=operation_anchor_id,
            )
        if state.current_prompt_revision_id != unit.prompt_revision_id:
            raise self._integrity(
                "Visual Reference operation anchor prompt revision is no longer current.",
                operation_anchor_id=operation_anchor_id,
                anchor_id=unit.anchor_id,
                operation_prompt_revision_id=unit.prompt_revision_id,
                current_prompt_revision_id=state.current_prompt_revision_id,
            )
        if state.active_operation_anchor_id == operation_anchor_id:
            return int(state.version)
        if state.active_operation_anchor_id is not None:
            raise self._error(
                "VISUAL_REFERENCE_V2_ANCHOR_BUSY",
                "Visual Reference anchor already has an active operation.",
                retryable=True,
                project_id=unit.project_id,
                anchor_type=unit.anchor_type,
                anchor_id=unit.anchor_id,
                active_operation_anchor_id=state.active_operation_anchor_id,
            )
        return self.compare_and_set_anchor_state(
            project_id=unit.project_id,
            anchor_type=unit.anchor_type,
            anchor_id=unit.anchor_id,
            expected_version=expected_version,
            values={"active_operation_anchor_id": operation_anchor_id, "last_progress_at": now_iso},
            now_iso=now_iso,
        )

    def _transition_operation(self, operation_id: str, target: str, *, now_iso: str, **values: Any) -> None:
        operation = self.repo.get_operation(operation_id)
        if operation is None:
            raise self._not_found("operation", operation_id)
        source = str(operation.status)
        if source == target:
            if values and not self.repo.transition_operation(
                operation_id=operation_id,
                expected_statuses=(source,),
                target_status=source,
                values={"updated_at": now_iso, **values},
            ):
                raise self._error(
                    "VISUAL_REFERENCE_V2_OPERATION_CONFLICT",
                    "Visual Reference operation changed concurrently.",
                    retryable=True,
                    operation_id=operation_id,
                )
            return
        if target not in _OPERATION_TRANSITIONS.get(source, set()):
            raise self._invalid_transition("operation", operation_id, source, target)
        if not self.repo.transition_operation(
            operation_id=operation_id,
            expected_statuses=(source,),
            target_status=target,
            values={"updated_at": now_iso, **values},
        ):
            raise self._error(
                "VISUAL_REFERENCE_V2_OPERATION_CONFLICT",
                "Visual Reference operation changed before the transition committed.",
                retryable=True,
                operation_id=operation_id,
                source=source,
                target=target,
            )

    def _transition_anchor(self, operation_anchor_id: str, target: str, *, now_iso: str, **values: Any) -> None:
        unit = self.repo.get_operation_anchor(operation_anchor_id)
        if unit is None:
            raise self._not_found("operation anchor", operation_anchor_id)
        source = str(unit.status)
        if source == target:
            return
        if target not in _ANCHOR_TRANSITIONS.get(source, set()):
            raise self._invalid_transition("operation anchor", operation_anchor_id, source, target)
        if not self.repo.transition_operation_anchor(
            operation_anchor_id=operation_anchor_id,
            expected_statuses=(source,),
            target_status=target,
            values={"updated_at": now_iso, **values},
        ):
            raise self._error(
                "VISUAL_REFERENCE_V2_OPERATION_ANCHOR_CONFLICT",
                "Visual Reference operation anchor changed before the transition committed.",
                retryable=True,
                operation_anchor_id=operation_anchor_id,
                source=source,
                target=target,
            )

    def _transition_task(self, operation_task_id: str, target: str, *, now_iso: str, **values: Any) -> None:
        task = self.repo.get_task(operation_task_id)
        if task is None:
            raise self._not_found("operation task", operation_task_id)
        source = str(task.status)
        if source == target:
            return
        if target not in _TASK_TRANSITIONS.get(source, set()):
            raise self._invalid_transition("operation task", operation_task_id, source, target)
        payload = {"updated_at": now_iso, **values}
        if target in TERMINAL_TASK_STATUSES:
            payload.setdefault("next_reconcile_at", None)
            payload.setdefault("completed_at", now_iso)
        if not self.repo.transition_task(
            operation_task_id=operation_task_id,
            expected_statuses=(source,),
            target_status=target,
            values=payload,
        ):
            raise self._error(
                "VISUAL_REFERENCE_V2_OPERATION_TASK_CONFLICT",
                "Visual Reference operation task changed before the transition committed.",
                retryable=True,
                operation_task_id=operation_task_id,
                source=source,
                target=target,
            )

    def _assert_task_owner(self, operation_task_id: str) -> tuple[Any, Any, Any, Any]:
        task = self.repo.get_task(operation_task_id)
        if task is None:
            raise self._not_found("operation task", operation_task_id)
        unit = self.repo.get_operation_anchor(task.operation_anchor_id)
        if unit is None:
            raise self._not_found("operation anchor", task.operation_anchor_id)
        operation = self.repo.get_operation(unit.operation_id)
        state = self.repo.get_anchor_state(unit.project_id, unit.anchor_type, unit.anchor_id)
        valid = (
            operation is not None
            and operation.status in ACTIVE_OPERATION_STATUSES
            and operation.cancel_requested_at is None
            and unit.status in ACTIVE_OPERATION_ANCHOR_STATUSES
            and state is not None
            and state.current_prompt_revision_id == unit.prompt_revision_id
            and state.active_operation_anchor_id == unit.operation_anchor_id
        )
        if not valid:
            raise self._error(
                "VISUAL_REFERENCE_V2_ANCHOR_OWNERSHIP_LOST",
                "Visual Reference task is not owned by the active anchor operation.",
                retryable=True,
                operation_task_id=operation_task_id,
                operation_anchor_id=unit.operation_anchor_id,
            )
        return task, unit, operation, state

    def begin_task_submission(self, *, operation_task_id: str, submit_attempt_token: str, now_iso: str) -> None:
        if not submit_attempt_token:
            raise ValueError("submit_attempt_token is required")
        self._assert_task_owner(operation_task_id)
        self._transition_task(
            operation_task_id,
            "submitting",
            now_iso=now_iso,
            submit_attempt_token=submit_attempt_token,
            submit_started_at=now_iso,
            next_reconcile_at=None,
            error_code=None,
            error_message=None,
            error_details_json=None,
        )

    def defer_task_for_provider_capacity(
        self,
        *,
        operation_task_id: str,
        media_task_id: str,
        now_iso: str,
        next_reconcile_at: str,
    ) -> None:
        task, _, _, _ = self._assert_task_owner(operation_task_id)
        if task.status != "submitting" or task.media_task_id != media_task_id:
            raise self._integrity(
                "Only the exact submitting Visual Reference task may defer for provider capacity.",
                operation_task_id=operation_task_id,
                media_task_id=media_task_id,
                stored_media_task_id=task.media_task_id,
                status=task.status,
            )
        self._transition_task(
            operation_task_id,
            "queued",
            now_iso=now_iso,
            media_task_id=media_task_id,
            submit_attempt_token=None,
            submit_started_at=None,
            next_reconcile_at=next_reconcile_at,
            error_code=None,
            error_message=None,
            error_details_json=None,
        )

    def mark_task_waiting_provider(
        self,
        *,
        operation_task_id: str,
        media_task_id: str,
        now_iso: str,
        next_reconcile_at: str,
    ) -> None:
        if not media_task_id:
            raise ValueError("media_task_id is required after provider acceptance")
        self._assert_task_owner(operation_task_id)
        self._transition_task(
            operation_task_id,
            "waiting_provider",
            now_iso=now_iso,
            media_task_id=media_task_id,
            next_reconcile_at=next_reconcile_at,
        )

    def mark_task_downloading(self, *, operation_task_id: str, now_iso: str) -> None:
        self._assert_task_owner(operation_task_id)
        self._transition_task(operation_task_id, "downloading", now_iso=now_iso, next_reconcile_at=None)

    def mark_task_materializing(self, *, operation_task_id: str, now_iso: str) -> None:
        self._assert_task_owner(operation_task_id)
        self._transition_task(operation_task_id, "materializing", now_iso=now_iso, next_reconcile_at=None)

    def mark_submit_outcome_unknown(
        self,
        *,
        operation_task_id: str,
        now_iso: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self._assert_task_owner(operation_task_id)
        self._transition_task(
            operation_task_id,
            "submit_outcome_unknown",
            now_iso=now_iso,
            error_code="VISUAL_REFERENCE_PROVIDER_SUBMIT_OUTCOME_UNKNOWN",
            error_message="Provider submission outcome is unknown; automatic resubmission is disabled.",
            error_details_json=details or {},
        )

    def mark_task_failed(
        self,
        *,
        operation_task_id: str,
        now_iso: str,
        error_code: str,
        error_message: str,
        error_details: dict[str, Any] | None = None,
    ) -> None:
        self._assert_task_owner(operation_task_id)
        self._transition_task(
            operation_task_id,
            "failed",
            now_iso=now_iso,
            error_code=error_code,
            error_message=error_message,
            error_details_json=error_details or {},
        )

    def mark_task_completed(self, *, operation_task_id: str, now_iso: str) -> None:
        task, unit, _, _ = self._assert_task_owner(operation_task_id)
        candidate = self.repo.get_candidate_for_operation_task(operation_task_id)
        if (
            candidate is None
            or candidate.project_id != unit.project_id
            or candidate.anchor_type != unit.anchor_type
            or candidate.anchor_id != unit.anchor_id
            or candidate.source_prompt_revision_id != unit.prompt_revision_id
            or candidate.candidate_index != task.candidate_index
        ):
            raise self._integrity(
                "A generated Visual Reference task cannot complete before its exact candidate is materialized.",
                operation_task_id=operation_task_id,
                operation_anchor_id=unit.operation_anchor_id,
            )
        self._transition_task(operation_task_id, "completed", now_iso=now_iso)

    def assert_task_can_materialize(self, *, operation_task_id: str) -> VisualReferenceMaterializationFence:
        task = self.repo.get_task(operation_task_id)
        if task is None:
            raise self._not_found("operation task", operation_task_id)
        unit = self.repo.get_operation_anchor(task.operation_anchor_id)
        if unit is None:
            raise self._not_found("operation anchor", task.operation_anchor_id)
        operation = self.repo.get_operation(unit.operation_id)
        state = self.repo.get_anchor_state(unit.project_id, unit.anchor_type, unit.anchor_id)
        already_materialized = self.repo.get_candidate_for_operation_task(operation_task_id)
        valid = (
            task.status == "materializing"
            and operation is not None
            and operation.status in ACTIVE_OPERATION_STATUSES
            and operation.cancel_requested_at is None
            and unit.status in ACTIVE_OPERATION_ANCHOR_STATUSES
            and state is not None
            and state.current_prompt_revision_id == unit.prompt_revision_id
            and state.active_operation_anchor_id == unit.operation_anchor_id
            and already_materialized is None
        )
        if not valid:
            raise self._error(
                "VISUAL_REFERENCE_V2_LATE_RESULT_FENCED",
                "Visual Reference provider result is no longer owned by an active materialization task.",
                operation_id=unit.operation_id,
                operation_anchor_id=unit.operation_anchor_id,
                operation_task_id=operation_task_id,
                anchor_type=unit.anchor_type,
                anchor_id=unit.anchor_id,
            )
        return VisualReferenceMaterializationFence(
            operation_id=unit.operation_id,
            operation_anchor_id=unit.operation_anchor_id,
            operation_task_id=operation_task_id,
            project_id=unit.project_id,
            anchor_type=unit.anchor_type,
            anchor_id=unit.anchor_id,
            prompt_revision_id=unit.prompt_revision_id,
            anchor_state_version=int(state.version),
        )

    def refresh_operation_anchor(self, *, operation_anchor_id: str, now_iso: str) -> str:
        self.session.flush()
        self.session.expire_all()
        unit = self.repo.get_operation_anchor(operation_anchor_id)
        if unit is None:
            raise self._not_found("operation anchor", operation_anchor_id)
        if unit.status in TERMINAL_OPERATION_ANCHOR_STATUSES:
            return str(unit.status)
        tasks = self.repo.list_tasks_for_operation_anchor(operation_anchor_id)
        if len(tasks) != int(unit.candidate_count):
            raise self._integrity(
                "Visual Reference operation anchor task count does not match frozen candidate_count.",
                operation_anchor_id=operation_anchor_id,
                candidate_count=int(unit.candidate_count),
                task_count=len(tasks),
            )
        statuses = [str(task.status) for task in tasks]
        if any(status in ACTIVE_TASK_STATUSES for status in statuses):
            target = "running"
        elif any(status == "completed" for status in statuses):
            target = "candidate_ready"
        elif statuses and all(status == "cancelled" for status in statuses):
            target = "cancelled"
        else:
            target = "failed"
        if target != unit.status:
            values: dict[str, Any] = {}
            if target in TERMINAL_OPERATION_ANCHOR_STATUSES:
                values["completed_at"] = now_iso
            if target == "failed" and any(status == "submit_outcome_unknown" for status in statuses):
                values.update(
                    error_code="VISUAL_REFERENCE_PROVIDER_SUBMIT_OUTCOME_UNKNOWN",
                    error_message="At least one provider submission outcome is unknown.",
                    error_details_json={"operation_anchor_id": operation_anchor_id},
                )
            self._transition_anchor(operation_anchor_id, target, now_iso=now_iso, **values)
        return target

    def refresh_operation(self, *, operation_id: str, now_iso: str) -> str:
        self.session.flush()
        self.session.expire_all()
        operation = self.repo.get_operation(operation_id)
        if operation is None:
            raise self._not_found("operation", operation_id)
        if operation.status in TERMINAL_OPERATION_STATUSES:
            return str(operation.status)
        units = self.repo.list_operation_anchors(operation_id)
        if len(units) != int(operation.total_anchor_count):
            raise self._integrity(
                "Visual Reference operation anchor count does not match total_anchor_count.",
                operation_id=operation_id,
                total_anchor_count=int(operation.total_anchor_count),
                operation_anchor_count=len(units),
            )
        completed = sum(1 for unit in units if unit.status == "candidate_ready")
        failed = sum(1 for unit in units if unit.status == "failed")
        cancelled = sum(1 for unit in units if unit.status == "cancelled")
        active = len(units) - completed - failed - cancelled
        values: dict[str, Any] = {
            "completed_count": completed,
            "failed_count": failed,
            "cancelled_count": cancelled,
            "last_progress_at": now_iso,
        }
        current = str(operation.status)
        if operation.cancel_requested_at is not None:
            target = "cancelled"
            values.update(
                cancelled_at=operation.cancelled_at or now_iso,
                completed_at=operation.completed_at or now_iso,
                next_reconcile_at=None,
                reconcile_owner=None,
                reconcile_token=None,
                reconcile_lease_expires_at=None,
            )
        elif active > 0:
            if current == "finalizing":
                raise self._integrity(
                    "Finalizing Visual Reference operation still has active anchors.",
                    operation_id=operation_id,
                    active_count=active,
                )
            target = "running" if current == "queued" else current
        elif current != "finalizing":
            target = "finalizing"
            values["next_reconcile_at"] = now_iso
        else:
            if completed == int(operation.total_anchor_count):
                target = "completed"
            elif completed > 0:
                target = "partial"
            elif cancelled == int(operation.total_anchor_count):
                target = "cancelled"
            else:
                target = "failed"
            values.update(completed_at=now_iso, next_reconcile_at=None)
        self._transition_operation(operation_id, target, now_iso=now_iso, **values)
        return target

    def recover_stale_submitting(
        self,
        *,
        operation_id: str,
        reconcile_token: str,
        stale_before_iso: str,
        now_iso: str,
    ) -> int:
        if not self.repo.fence_reconcile_write(
            operation_id=operation_id,
            token=reconcile_token,
            now_iso=now_iso,
        ):
            raise self._error(
                "VISUAL_REFERENCE_V2_RECONCILE_LEASE_LOST",
                "Visual Reference reconciliation lease is no longer current.",
                retryable=True,
                operation_id=operation_id,
            )
        tasks = self.repo.list_stale_submitting_tasks(
            operation_id=operation_id,
            stale_before_iso=stale_before_iso,
        )
        touched: set[str] = set()
        for task in tasks:
            self.mark_submit_outcome_unknown(
                operation_task_id=str(task.operation_task_id),
                now_iso=now_iso,
                details={
                    "operation_id": operation_id,
                    "submit_started_at": task.submit_started_at,
                    "submit_attempt_token": task.submit_attempt_token,
                    "recovery": "stale_submitting",
                },
            )
            touched.add(str(task.operation_anchor_id))
        for operation_anchor_id in sorted(touched):
            self.refresh_operation_anchor(operation_anchor_id=operation_anchor_id, now_iso=now_iso)
        if touched:
            self.refresh_operation(operation_id=operation_id, now_iso=now_iso)
        return len(tasks)

    def cancel_operation(self, *, operation_id: str, now_iso: str) -> Mapping[str, object]:
        operation = self.repo.get_operation(operation_id)
        if operation is None:
            raise self._not_found("operation", operation_id)
        if operation.status == "cancelled":
            return {"operation_id": operation_id, "status": "cancelled", "already_cancelled": True}
        if operation.status in TERMINAL_OPERATION_STATUSES:
            raise self._error(
                "VISUAL_REFERENCE_V2_OPERATION_TERMINAL",
                "A terminal Visual Reference operation cannot be cancelled.",
                operation_id=operation_id,
                status=operation.status,
            )
        units = self.repo.list_operation_anchors(operation_id)
        if len(units) != int(operation.total_anchor_count):
            raise self._integrity(
                "Visual Reference operation anchor count does not match total_anchor_count.",
                operation_id=operation_id,
                total_anchor_count=int(operation.total_anchor_count),
                operation_anchor_count=len(units),
            )
        cancelled_tasks = 0
        cancelled_anchors = 0
        for unit in units:
            tasks = self.repo.list_tasks_for_operation_anchor(unit.operation_anchor_id)
            if len(tasks) != int(unit.candidate_count):
                raise self._integrity(
                    "Visual Reference operation anchor task count does not match candidate_count.",
                    operation_anchor_id=unit.operation_anchor_id,
                    candidate_count=int(unit.candidate_count),
                    task_count=len(tasks),
                )
            for task in tasks:
                if task.status in ACTIVE_TASK_STATUSES:
                    self._transition_task(
                        str(task.operation_task_id),
                        "cancelled",
                        now_iso=now_iso,
                        error_code="VISUAL_REFERENCE_OPERATION_CANCELLED",
                        error_message="Visual Reference operation was cancelled locally.",
                        error_details_json={"operation_id": operation_id},
                    )
                    cancelled_tasks += 1
            if unit.status in ACTIVE_OPERATION_ANCHOR_STATUSES:
                self._transition_anchor(
                    str(unit.operation_anchor_id),
                    "cancelled",
                    now_iso=now_iso,
                    completed_at=now_iso,
                    error_code="VISUAL_REFERENCE_OPERATION_CANCELLED",
                    error_message="Visual Reference operation was cancelled locally.",
                    error_details_json={"operation_id": operation_id},
                )
                cancelled_anchors += 1
        self.session.flush()
        self.session.expire_all()
        refreshed = self.repo.list_operation_anchors(operation_id)
        completed = sum(1 for unit in refreshed if unit.status == "candidate_ready")
        failed = sum(1 for unit in refreshed if unit.status == "failed")
        cancelled = sum(1 for unit in refreshed if unit.status == "cancelled")
        current_operation = self.repo.get_operation(operation_id)
        cancel_requested_at = now_iso if current_operation is None else (current_operation.cancel_requested_at or now_iso)
        self._transition_operation(
            operation_id,
            "cancelled",
            now_iso=now_iso,
            cancel_requested_at=cancel_requested_at,
            cancelled_at=now_iso,
            completed_at=now_iso,
            completed_count=completed,
            failed_count=failed,
            cancelled_count=cancelled,
            next_reconcile_at=None,
            reconcile_owner=None,
            reconcile_token=None,
            reconcile_lease_expires_at=None,
        )
        self.session.flush()
        return {
            "operation_id": operation_id,
            "status": "cancelled",
            "already_cancelled": False,
            "cancelled_task_count": cancelled_tasks,
            "cancelled_anchor_count": cancelled_anchors,
            "completed_count": completed,
            "failed_count": failed,
            "cancelled_count": cancelled,
        }
