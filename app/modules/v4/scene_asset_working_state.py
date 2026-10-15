from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import AppError
from app.db.scene_asset_beat_state import SceneAssetBeatState
from app.repositories.scene_asset_working import (
    ACTIVE_OPERATION_BEAT_STATUSES,
    ACTIVE_OPERATION_STATUSES,
    ACTIVE_TASK_STATUSES,
    TERMINAL_OPERATION_BEAT_STATUSES,
    TERMINAL_OPERATION_STATUSES,
    TERMINAL_TASK_STATUSES,
    SceneAssetWorkingRepository,
)


_OPERATION_TRANSITIONS: dict[str, set[str]] = {
    "queued": {"running", "finalizing", "failed", "cancelled", "superseded"},
    "running": {"queued", "finalizing", "failed", "cancelled", "superseded"},
    "finalizing": {"completed", "partial", "failed", "cancelled", "superseded"},
    "completed": set(),
    "partial": set(),
    "failed": set(),
    "cancelled": set(),
    "superseded": set(),
}
_OPERATION_BEAT_TRANSITIONS: dict[str, set[str]] = {
    "queued": {"running", "failed", "cancelled"},
    "running": {"queued", "candidate_ready", "failed", "cancelled"},
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
_RUNNING_TASK_STATUSES = {"submitting", "waiting_provider", "downloading", "materializing"}


@dataclass(frozen=True, slots=True)
class MaterializationFence:
    operation_id: str
    operation_beat_id: str
    operation_task_id: str
    project_id: str
    scene_id: str
    beat_id: str
    beat_state_version: int


class SceneAssetWorkingStateService:
    """Transactional Scene Assets V2 state machine with no external I/O."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = SceneAssetWorkingRepository(session)

    def reset_for_full_prompt_bundle_epoch(
        self,
        *,
        project_id: str,
        prompt_items: list[dict[str, Any]],
        now_iso: str,
    ) -> int:
        """Reset working state when a full Prompt Bundle changes Beat authority."""
        return self.invalidate_for_lineage_replacement(
            project_id=project_id,
            scene_by_beat={
                str(item["beat_id"]): str(item["scene_id"])
                for item in prompt_items
                if isinstance(item, dict) and item.get("beat_id") and item.get("scene_id")
            },
            now_iso=now_iso,
        )

    def invalidate_for_lineage_replacement(
        self,
        *,
        project_id: str,
        scene_by_beat: Mapping[str, str],
        now_iso: str,
    ) -> int:
        """Invalidate current Scene Assets state in the caller's publication transaction.

        This is shared by Scene/Visual replacement and full Prompt Bundle
        replacement. Re-applying it in one logical publication is idempotent:
        versions advance only for BeatStates whose current state or Scene owner
        actually changes.
        """
        operations = self.repo.list_operations_for_project(project_id)
        active_operation_ids = sorted(
            str(operation.operation_id)
            for operation in operations
            if operation.status in ACTIVE_OPERATION_STATUSES
        )
        states = list(
            self.session.scalars(
                select(SceneAssetBeatState).where(
                    SceneAssetBeatState.project_id == project_id,
                )
            )
        )
        owned_beat_ids = sorted(
            str(state.beat_id)
            for state in states
            if state.active_operation_beat_id is not None
        )
        if active_operation_ids or owned_beat_ids:
            raise AppError(
                "SCENE_ASSET_GENERATION_ACTIVE",
                "A new full Prompt Bundle cannot replace Scene Assets while generation is active.",
                409,
                retryable=True,
                details={
                    "project_id": project_id,
                    "active_operation_ids": active_operation_ids,
                    "owned_beat_ids": owned_beat_ids,
                },
            )

        invalidated = 0
        for state in states:
            current_scene_id = scene_by_beat.get(str(state.beat_id))
            changed = bool(
                (current_scene_id is not None and state.scene_id != current_scene_id)
                or state.resolution_status != "not_generated"
                or state.review_status != "unreviewed"
                or state.current_draft_candidate_id is not None
                or state.selected_candidate_id is not None
                or state.active_operation_beat_id is not None
                or state.continuity_source_scene_id is not None
                or state.continuity_source_beat_id is not None
                or state.continuity_source_candidate_id is not None
                or state.last_error_json is not None
                or state.last_progress_at is not None
            )
            if not changed:
                continue
            if current_scene_id is not None:
                state.scene_id = current_scene_id
            state.version = int(state.version) + 1
            state.resolution_status = "not_generated"
            state.review_status = "unreviewed"
            state.current_draft_candidate_id = None
            state.selected_candidate_id = None
            state.active_operation_beat_id = None
            state.continuity_source_scene_id = None
            state.continuity_source_beat_id = None
            state.continuity_source_candidate_id = None
            state.last_error_json = None
            state.last_progress_at = None
            state.updated_at = now_iso
            invalidated += 1
        if invalidated:
            self.session.flush()
        return invalidated

    @staticmethod
    def _invalid_transition(entity: str, identity: str, source: str, target: str) -> AppError:
        return AppError(
            "SCENE_ASSET_V2_STATE_TRANSITION_INVALID",
            f"Invalid Scene Assets V2 {entity} state transition.",
            409,
            retryable=False,
            details={"id": identity, "from": source, "to": target},
        )

    @staticmethod
    def _not_found(entity: str, identity: str) -> AppError:
        return AppError(
            "SCENE_ASSET_V2_STATE_NOT_FOUND",
            f"Scene Assets V2 {entity} was not found.",
            404,
            retryable=False,
            details={"id": identity},
        )

    @staticmethod
    def _integrity(message: str, **details: Any) -> AppError:
        return AppError(
            "SCENE_ASSET_V2_STATE_INTEGRITY_INVALID",
            message,
            409,
            retryable=False,
            details=details,
        )

    def compare_and_set_beat_state(
        self,
        *,
        project_id: str,
        beat_id: str,
        expected_version: int,
        values: dict[str, Any],
        now_iso: str,
    ) -> int:
        if not self.repo.compare_and_set_beat_state(
            project_id=project_id,
            beat_id=beat_id,
            expected_version=expected_version,
            values=values,
            updated_at=now_iso,
        ):
            raise AppError(
                "SCENE_ASSET_V2_BEAT_STATE_CONFLICT",
                "Scene Asset beat working state changed before this mutation committed.",
                409,
                retryable=True,
                details={"project_id": project_id, "beat_id": beat_id, "expected_version": expected_version},
            )
        self.session.flush()
        state = self.repo.get_beat_state(project_id, beat_id)
        if state is None:
            raise self._not_found("beat state", beat_id)
        return int(state.version)

    def claim_beat_for_operation(
        self,
        *,
        operation_beat_id: str,
        expected_version: int,
        now_iso: str,
    ) -> int:
        operation_beat = self.repo.get_operation_beat(operation_beat_id)
        if operation_beat is None:
            raise self._not_found("operation beat", operation_beat_id)
        operation = self.repo.get_operation(operation_beat.operation_id)
        if (
            operation is None
            or operation.status not in ACTIVE_OPERATION_STATUSES
            or operation.cancel_requested_at is not None
            or operation_beat.status not in ACTIVE_OPERATION_BEAT_STATUSES
        ):
            raise self._integrity(
                "Only an active Scene Asset operation beat may own BeatState.",
                operation_beat_id=operation_beat_id,
            )
        state = self.repo.get_beat_state(operation_beat.project_id, operation_beat.beat_id)
        if state is None:
            raise self._not_found("beat state", operation_beat.beat_id)
        if state.scene_id != operation_beat.scene_id:
            raise self._integrity(
                "Scene Asset operation beat does not match BeatState scene identity.",
                operation_beat_id=operation_beat_id,
                beat_id=operation_beat.beat_id,
            )
        if state.active_operation_beat_id == operation_beat_id:
            return int(state.version)
        if state.active_operation_beat_id is not None:
            raise AppError(
                "SCENE_ASSET_V2_BEAT_BUSY",
                "Scene Asset beat already has an active operation.",
                409,
                retryable=True,
                details={
                    "project_id": operation_beat.project_id,
                    "beat_id": operation_beat.beat_id,
                    "active_operation_beat_id": state.active_operation_beat_id,
                },
            )
        return self.compare_and_set_beat_state(
            project_id=operation_beat.project_id,
            beat_id=operation_beat.beat_id,
            expected_version=expected_version,
            values={"active_operation_beat_id": operation_beat_id, "last_progress_at": now_iso},
            now_iso=now_iso,
        )

    def _transition_operation(self, operation_id: str, target_status: str, *, now_iso: str, **values: Any) -> None:
        operation = self.repo.get_operation(operation_id)
        if operation is None:
            raise self._not_found("operation", operation_id)
        source = str(operation.status)
        if source != target_status and target_status not in _OPERATION_TRANSITIONS.get(source, set()):
            raise self._invalid_transition("operation", operation_id, source, target_status)
        if not self.repo.transition_operation(
            operation_id=operation_id,
            expected_statuses=(source,),
            target_status=target_status,
            values={"updated_at": now_iso, **values},
        ):
            raise AppError(
                "SCENE_ASSET_V2_OPERATION_CONFLICT",
                "Scene Asset operation changed before the transition committed.",
                409,
                retryable=True,
                details={"operation_id": operation_id, "from": source, "to": target_status},
            )

    def _transition_operation_beat(
        self,
        operation_beat_id: str,
        target_status: str,
        *,
        now_iso: str,
        **values: Any,
    ) -> None:
        operation_beat = self.repo.get_operation_beat(operation_beat_id)
        if operation_beat is None:
            raise self._not_found("operation beat", operation_beat_id)
        source = str(operation_beat.status)
        if source == target_status:
            return
        if target_status not in _OPERATION_BEAT_TRANSITIONS.get(source, set()):
            raise self._invalid_transition("operation beat", operation_beat_id, source, target_status)
        if not self.repo.transition_operation_beat(
            operation_beat_id=operation_beat_id,
            expected_statuses=(source,),
            target_status=target_status,
            values={"updated_at": now_iso, **values},
        ):
            raise AppError(
                "SCENE_ASSET_V2_OPERATION_BEAT_CONFLICT",
                "Scene Asset operation beat changed before the transition committed.",
                409,
                retryable=True,
                details={"operation_beat_id": operation_beat_id, "from": source, "to": target_status},
            )

    def _transition_task(
        self,
        operation_task_id: str,
        target_status: str,
        *,
        now_iso: str,
        **values: Any,
    ) -> None:
        task = self.repo.get_task(operation_task_id)
        if task is None:
            raise self._not_found("operation task", operation_task_id)
        source = str(task.status)
        if source == target_status:
            return
        if target_status not in _TASK_TRANSITIONS.get(source, set()):
            raise self._invalid_transition("operation task", operation_task_id, source, target_status)
        payload = {"updated_at": now_iso, **values}
        if target_status in TERMINAL_TASK_STATUSES:
            payload.setdefault("next_reconcile_at", None)
            payload.setdefault("completed_at", now_iso)
        if not self.repo.transition_task(
            operation_task_id=operation_task_id,
            expected_statuses=(source,),
            target_status=target_status,
            values=payload,
        ):
            raise AppError(
                "SCENE_ASSET_V2_OPERATION_TASK_CONFLICT",
                "Scene Asset operation task changed before the transition committed.",
                409,
                retryable=True,
                details={"operation_task_id": operation_task_id, "from": source, "to": target_status},
            )

    def _assert_task_active_owner(self, operation_task_id: str) -> tuple[Any, Any, Any]:
        task = self.repo.get_task(operation_task_id)
        if task is None:
            raise self._not_found("operation task", operation_task_id)
        operation_beat = self.repo.get_operation_beat(task.operation_beat_id)
        if operation_beat is None:
            raise self._not_found("operation beat", task.operation_beat_id)
        operation = self.repo.get_operation(operation_beat.operation_id)
        state = self.repo.get_beat_state(operation_beat.project_id, operation_beat.beat_id)
        valid = (
            operation is not None
            and operation.status in ACTIVE_OPERATION_STATUSES
            and operation.cancel_requested_at is None
            and operation_beat.status in ACTIVE_OPERATION_BEAT_STATUSES
            and state is not None
            and state.scene_id == operation_beat.scene_id
            and state.active_operation_beat_id == operation_beat.operation_beat_id
        )
        if not valid:
            raise AppError(
                "SCENE_ASSET_V2_BEAT_OWNERSHIP_LOST",
                "Scene Asset task is not owned by the active beat operation.",
                409,
                retryable=True,
                details={"operation_task_id": operation_task_id, "operation_beat_id": task.operation_beat_id},
            )
        return task, operation_beat, state

    def begin_task_submission(
        self,
        *,
        operation_task_id: str,
        submit_attempt_token: str,
        now_iso: str,
    ) -> None:
        if not submit_attempt_token:
            raise ValueError("submit_attempt_token is required")
        self._assert_task_active_owner(operation_task_id)
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
        task, _, _ = self._assert_task_active_owner(operation_task_id)
        if task.status != "submitting" or task.media_task_id != media_task_id:
            raise self._integrity(
                "Only the exact submitting Scene Asset task may defer for provider capacity.",
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
        self._assert_task_active_owner(operation_task_id)
        self._transition_task(
            operation_task_id,
            "waiting_provider",
            now_iso=now_iso,
            media_task_id=media_task_id,
            next_reconcile_at=next_reconcile_at,
        )

    def mark_task_downloading(self, *, operation_task_id: str, now_iso: str) -> None:
        self._assert_task_active_owner(operation_task_id)
        self._transition_task(operation_task_id, "downloading", now_iso=now_iso, next_reconcile_at=None)

    def mark_task_materializing(self, *, operation_task_id: str, now_iso: str) -> None:
        self._assert_task_active_owner(operation_task_id)
        self._transition_task(operation_task_id, "materializing", now_iso=now_iso, next_reconcile_at=None)

    def mark_submit_outcome_unknown(
        self,
        *,
        operation_task_id: str,
        now_iso: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self._assert_task_active_owner(operation_task_id)
        self._transition_task(
            operation_task_id,
            "submit_outcome_unknown",
            now_iso=now_iso,
            error_code="SCENE_ASSET_PROVIDER_SUBMIT_OUTCOME_UNKNOWN",
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
        self._assert_task_active_owner(operation_task_id)
        self._transition_task(
            operation_task_id,
            "failed",
            now_iso=now_iso,
            error_code=error_code,
            error_message=error_message,
            error_details_json=error_details or {},
        )

    def mark_task_completed(self, *, operation_task_id: str, now_iso: str) -> None:
        task, operation_beat, state = self._assert_task_active_owner(operation_task_id)
        candidate = self.repo.get_candidate_for_operation_task(operation_task_id)
        if candidate is None or candidate.operation_beat_id != task.operation_beat_id:
            raise self._integrity(
                "A generated Scene Asset task cannot complete before its candidate is materialized with exact ownership.",
                operation_task_id=operation_task_id,
            )
        if state.current_draft_candidate_id is None and state.selected_candidate_id is None:
            self.compare_and_set_beat_state(
                project_id=operation_beat.project_id,
                beat_id=operation_beat.beat_id,
                expected_version=int(state.version),
                values={
                    "resolution_status": "candidate_ready",
                    "review_status": "current_draft",
                    "current_draft_candidate_id": candidate.candidate_id,
                    "last_progress_at": now_iso,
                    "last_error_json": None,
                },
                now_iso=now_iso,
            )
        self._transition_task(operation_task_id, "completed", now_iso=now_iso)

    def assert_task_can_materialize(self, *, operation_task_id: str) -> MaterializationFence:
        task = self.repo.get_task(operation_task_id)
        if task is None:
            raise self._not_found("operation task", operation_task_id)
        operation_beat = self.repo.get_operation_beat(task.operation_beat_id)
        if operation_beat is None:
            raise self._not_found("operation beat", task.operation_beat_id)
        operation = self.repo.get_operation(operation_beat.operation_id)
        state = self.repo.get_beat_state(operation_beat.project_id, operation_beat.beat_id)
        already_materialized = self.repo.get_candidate_for_operation_task(operation_task_id)
        valid = (
            task.status == "materializing"
            and operation is not None
            and operation.status in ACTIVE_OPERATION_STATUSES
            and operation.cancel_requested_at is None
            and operation_beat.status in ACTIVE_OPERATION_BEAT_STATUSES
            and state is not None
            and state.scene_id == operation_beat.scene_id
            and state.active_operation_beat_id == operation_beat.operation_beat_id
            and already_materialized is None
        )
        if not valid:
            raise AppError(
                "SCENE_ASSET_V2_LATE_RESULT_FENCED",
                "Scene Asset provider result is no longer owned by an active materialization task.",
                409,
                retryable=False,
                details={
                    "operation_id": operation_beat.operation_id,
                    "operation_beat_id": operation_beat.operation_beat_id,
                    "operation_task_id": operation_task_id,
                    "beat_id": operation_beat.beat_id,
                },
            )
        return MaterializationFence(
            operation_id=operation_beat.operation_id,
            operation_beat_id=operation_beat.operation_beat_id,
            operation_task_id=operation_task_id,
            project_id=operation_beat.project_id,
            scene_id=operation_beat.scene_id,
            beat_id=operation_beat.beat_id,
            beat_state_version=int(state.version),
        )

    def refresh_operation_beat(self, *, operation_beat_id: str, now_iso: str) -> str:
        self.session.flush()
        self.session.expire_all()
        operation_beat = self.repo.get_operation_beat(operation_beat_id)
        if operation_beat is None:
            raise self._not_found("operation beat", operation_beat_id)
        if operation_beat.status in TERMINAL_OPERATION_BEAT_STATUSES:
            return str(operation_beat.status)
        tasks = self.repo.list_tasks_for_operation_beat(operation_beat_id)
        if len(tasks) != int(operation_beat.candidate_count):
            raise self._integrity(
                "Scene Asset operation beat task count does not match frozen candidate_count.",
                operation_beat_id=operation_beat_id,
                candidate_count=int(operation_beat.candidate_count),
                task_count=len(tasks),
            )
        statuses = [str(task.status) for task in tasks]
        if any(status in _RUNNING_TASK_STATUSES for status in statuses):
            target = "running"
        elif any(status == "queued" for status in statuses):
            target = "queued"
        elif any(status == "completed" for status in statuses):
            target = "candidate_ready"
        elif statuses and all(status == "cancelled" for status in statuses):
            target = "cancelled"
        else:
            target = "failed"
        if target != operation_beat.status:
            values: dict[str, Any] = {}
            if target in TERMINAL_OPERATION_BEAT_STATUSES:
                values["completed_at"] = now_iso
            if target == "failed" and any(status == "submit_outcome_unknown" for status in statuses):
                values.update(
                    error_code="SCENE_ASSET_PROVIDER_SUBMIT_OUTCOME_UNKNOWN",
                    error_message="At least one provider submission outcome is unknown.",
                    error_details_json={"operation_beat_id": operation_beat_id},
                )
            self._transition_operation_beat(operation_beat_id, target, now_iso=now_iso, **values)
        return target

    def refresh_operation(self, *, operation_id: str, now_iso: str) -> str:
        self.session.flush()
        self.session.expire_all()
        operation = self.repo.get_operation(operation_id)
        if operation is None:
            raise self._not_found("operation", operation_id)
        beats = self.repo.list_operation_beats(operation_id)
        if len(beats) != int(operation.total_beat_count):
            raise self._integrity(
                "Scene Asset operation beat count does not match total_beat_count.",
                operation_id=operation_id,
                total_beat_count=int(operation.total_beat_count),
                operation_beat_count=len(beats),
            )
        completed_count = sum(1 for beat in beats if beat.status == "candidate_ready")
        failed_count = sum(1 for beat in beats if beat.status == "failed")
        cancelled_count = sum(1 for beat in beats if beat.status == "cancelled")
        queued_count = sum(1 for beat in beats if beat.status == "queued")
        running_count = sum(1 for beat in beats if beat.status == "running")
        active_count = queued_count + running_count
        values: dict[str, Any] = {
            "completed_count": completed_count,
            "failed_count": failed_count,
            "cancelled_count": cancelled_count,
            "last_progress_at": now_iso,
        }
        current = str(operation.status)
        if current in TERMINAL_OPERATION_STATUSES:
            return current
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
        elif active_count > 0:
            if current == "finalizing":
                raise self._integrity(
                    "Finalizing Scene Asset operation still has active operation beats.",
                    operation_id=operation_id,
                    active_count=active_count,
                )
            target = "running" if running_count > 0 else "queued"
        elif current != "finalizing":
            target = "finalizing"
            values["next_reconcile_at"] = now_iso
        else:
            if completed_count == int(operation.total_beat_count):
                target = "completed"
            elif completed_count > 0:
                target = "partial"
            elif cancelled_count == int(operation.total_beat_count):
                target = "cancelled"
            else:
                target = "failed"
            values.update(completed_at=now_iso, next_reconcile_at=None)
        self._transition_operation(operation_id, target, now_iso=now_iso, **values)
        return target

    def cancel_operation(self, *, operation_id: str, now_iso: str) -> dict[str, Any]:
        operation = self.repo.get_operation(operation_id)
        if operation is None:
            raise self._not_found("operation", operation_id)
        if operation.status == "cancelled":
            return {"operation_id": operation_id, "status": "cancelled", "already_cancelled": True}
        if operation.status in TERMINAL_OPERATION_STATUSES:
            raise AppError(
                "SCENE_ASSET_V2_OPERATION_TERMINAL",
                "A terminal Scene Asset operation cannot be cancelled.",
                409,
                retryable=False,
                details={"operation_id": operation_id, "status": operation.status},
            )
        beats = self.repo.list_operation_beats(operation_id)
        if len(beats) != int(operation.total_beat_count):
            raise self._integrity(
                "Scene Asset operation beat count does not match total_beat_count.",
                operation_id=operation_id,
                total_beat_count=int(operation.total_beat_count),
                operation_beat_count=len(beats),
            )
        cancelled_task_count = 0
        cancelled_beat_count = 0
        for beat in beats:
            tasks = self.repo.list_tasks_for_operation_beat(beat.operation_beat_id)
            if len(tasks) != int(beat.candidate_count):
                raise self._integrity(
                    "Scene Asset operation beat task count does not match frozen candidate_count.",
                    operation_beat_id=beat.operation_beat_id,
                    candidate_count=int(beat.candidate_count),
                    task_count=len(tasks),
                )
            for task in tasks:
                if task.status in ACTIVE_TASK_STATUSES:
                    self._transition_task(
                        task.operation_task_id,
                        "cancelled",
                        now_iso=now_iso,
                        error_code="SCENE_ASSET_OPERATION_CANCELLED",
                        error_message="Scene Asset operation was cancelled locally.",
                        error_details_json={"operation_id": operation_id},
                    )
                    cancelled_task_count += 1
            if beat.status in ACTIVE_OPERATION_BEAT_STATUSES:
                self._transition_operation_beat(
                    beat.operation_beat_id,
                    "cancelled",
                    now_iso=now_iso,
                    completed_at=now_iso,
                    error_code="SCENE_ASSET_OPERATION_CANCELLED",
                    error_message="Scene Asset operation was cancelled locally.",
                    error_details_json={"operation_id": operation_id},
                )
                cancelled_beat_count += 1
            self.repo.release_active_operation_beat(operation_beat_id=beat.operation_beat_id, updated_at=now_iso)
        self.session.flush()
        self.session.expire_all()
        refreshed_beats = self.repo.list_operation_beats(operation_id)
        completed_count = sum(1 for beat in refreshed_beats if beat.status == "candidate_ready")
        failed_count = sum(1 for beat in refreshed_beats if beat.status == "failed")
        cancelled_count = sum(1 for beat in refreshed_beats if beat.status == "cancelled")
        current_operation = self.repo.get_operation(operation_id)
        cancel_requested_at = now_iso if current_operation is None else (current_operation.cancel_requested_at or now_iso)
        self._transition_operation(
            operation_id,
            "cancelled",
            now_iso=now_iso,
            cancel_requested_at=cancel_requested_at,
            cancelled_at=now_iso,
            completed_at=now_iso,
            completed_count=completed_count,
            failed_count=failed_count,
            cancelled_count=cancelled_count,
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
            "cancelled_task_count": cancelled_task_count,
            "cancelled_beat_count": cancelled_beat_count,
            "completed_count": completed_count,
            "failed_count": failed_count,
            "cancelled_count": cancelled_count,
        }

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
            raise AppError(
                "SCENE_ASSET_V2_RECONCILE_LEASE_LOST",
                "Scene Asset reconciliation lease is no longer current.",
                409,
                retryable=True,
                details={"operation_id": operation_id},
            )
        tasks = self.repo.list_stale_submitting_tasks(
            operation_id=operation_id,
            stale_before_iso=stale_before_iso,
        )
        touched_beats: set[str] = set()
        for task in tasks:
            self.mark_submit_outcome_unknown(
                operation_task_id=task.operation_task_id,
                now_iso=now_iso,
                details={
                    "operation_id": operation_id,
                    "submit_started_at": task.submit_started_at,
                    "submit_attempt_token": task.submit_attempt_token,
                    "recovery": "stale_submitting",
                },
            )
            touched_beats.add(task.operation_beat_id)
        for operation_beat_id in sorted(touched_beats):
            self.refresh_operation_beat(operation_beat_id=operation_beat_id, now_iso=now_iso)
        if touched_beats:
            self.refresh_operation(operation_id=operation_id, now_iso=now_iso)
        return len(tasks)
