from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import AppError
from app.core.time import utc_now_iso
from app.db.scene_asset_beat_state import SceneAssetBeatState
from app.db.scene_asset_working_operation import SceneAssetWorkingOperation
from app.repositories import ProjectRepository
from app.repositories.scene_asset_working import ACTIVE_OPERATION_STATUSES, SceneAssetWorkingRepository


_TERMINAL_BEAT_STATUSES = {"candidate_ready", "failed", "cancelled"}
_TERMINAL_OPERATION_STATUSES = {"completed", "partial", "failed", "cancelled", "superseded"}
_STALE_SUBMIT_SECONDS = 30
_RECONCILE_OVERDUE_GRACE_SECONDS = 20


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _older_than(value: str | None, *, now: datetime, seconds: int) -> bool:
    parsed = _parse_iso(value)
    return parsed is not None and parsed <= now - timedelta(seconds=seconds)


class SceneAssetExecutionProjection:
    """Read-only projection of durable Scene Assets V2 execution state."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.projects = ProjectRepository(session)
        self.repo = SceneAssetWorkingRepository(session)

    def get_operation_status(self, project_id: str, operation_id: str) -> dict[str, Any]:
        operation = self.session.get(SceneAssetWorkingOperation, operation_id)
        if operation is None or operation.project_id != project_id:
            raise AppError(
                "SCENE_ASSET_OPERATION_NOT_FOUND",
                "Scene Asset V2 operation was not found.",
                404,
                retryable=False,
            )
        now = _parse_iso(utc_now_iso()) or datetime.now(UTC)
        beats = self.repo.list_operation_beats(operation_id)
        all_tasks = self.repo.list_tasks_for_operation(operation_id)
        tasks_by_beat: dict[str, list[Any]] = {beat.operation_beat_id: [] for beat in beats}
        for task in all_tasks:
            tasks_by_beat.setdefault(task.operation_beat_id, []).append(task)
        beat_ids = [str(beat.beat_id) for beat in beats]
        states_by_beat = {
            str(state.beat_id): state
            for state in self.session.scalars(
                select(SceneAssetBeatState).where(
                    SceneAssetBeatState.project_id == project_id,
                    SceneAssetBeatState.beat_id.in_(beat_ids),
                )
            )
        } if beat_ids else {}
        operation_health, operation_stalled_reason = self._operation_health(
            operation,
            all_tasks,
            now=now,
        )
        beat_payloads: list[dict[str, Any]] = []
        for beat in beats:
            tasks = tasks_by_beat.get(beat.operation_beat_id, [])
            beat_health, beat_stalled_reason, beat_last_progress_at = self._beat_health(
                operation=operation,
                beat=beat,
                tasks=tasks,
                state=states_by_beat.get(str(beat.beat_id)),
                operation_health=operation_health,
                operation_stalled_reason=operation_stalled_reason,
                now=now,
            )
            beat_payloads.append({
                "operation_beat_id": beat.operation_beat_id,
                "beat_id": beat.beat_id,
                "status": beat.status,
                "health": beat_health,
                "stalled": beat_health == "stalled",
                "stalled_reason": beat_stalled_reason,
                "last_progress_at": beat_last_progress_at,
                "candidate_count": beat.candidate_count,
                "error_code": beat.error_code,
                "error_message": beat.error_message,
                "tasks": [{
                    "operation_task_id": task.operation_task_id,
                    "candidate_index": task.candidate_index,
                    "status": task.status,
                    "media_task_id": task.media_task_id,
                    "next_reconcile_at": task.next_reconcile_at,
                    "error_code": task.error_code,
                    "error_message": task.error_message,
                } for task in tasks],
            })
        return {
            "project_id": project_id,
            "operation_id": operation.operation_id,
            "operation_type": operation.operation_type,
            "status": operation.status,
            "health": operation_health,
            "stalled": operation_health == "stalled",
            "stalled_reason": operation_stalled_reason,
            "last_progress_at": operation.last_progress_at,
            "total_beat_count": operation.total_beat_count,
            "completed_count": operation.completed_count,
            "failed_count": operation.failed_count,
            "cancelled_count": operation.cancelled_count,
            "next_reconcile_at": operation.next_reconcile_at,
            "cancel_requested_at": operation.cancel_requested_at,
            "completed_at": operation.completed_at,
            "error_code": operation.error_code,
            "error_message": operation.error_message,
            "beats": beat_payloads,
        }

    def get_execution_status(self, project_id: str) -> dict[str, Any]:
        if self.projects.get(project_id) is None:
            raise AppError("PROJECT_NOT_FOUND", f"Project '{project_id}' was not found.", 404, retryable=False)
        active = list(
            self.session.scalars(
                select(SceneAssetWorkingOperation)
                .where(
                    SceneAssetWorkingOperation.project_id == project_id,
                    SceneAssetWorkingOperation.status.in_(ACTIVE_OPERATION_STATUSES),
                )
                .order_by(
                    SceneAssetWorkingOperation.created_at,
                    SceneAssetWorkingOperation.operation_id,
                )
            )
        )
        operations = [self.get_operation_status(project_id, operation.operation_id) for operation in active]
        return {
            "project_id": project_id,
            "active": bool(active),
            "active_operation_count": len(active),
            "stalled_operation_count": sum(1 for operation in operations if operation["stalled"]),
            "operations": operations,
        }

    def _operation_health(self, operation: Any, tasks: list[Any], *, now: datetime) -> tuple[str, str | None]:
        if operation.status in _TERMINAL_OPERATION_STATUSES:
            return "terminal", None
        stale_submit = next(
            (task for task in tasks if task.status == "submitting" and _older_than(task.submit_started_at, now=now, seconds=_STALE_SUBMIT_SECONDS)),
            None,
        )
        if stale_submit is not None:
            return "stalled", "stale_submission"
        lease_expires = _parse_iso(operation.reconcile_lease_expires_at)
        if operation.reconcile_token and lease_expires is not None and lease_expires <= now:
            return "stalled", "expired_reconcile_lease"
        due = _parse_iso(operation.next_reconcile_at)
        has_live_lease = bool(operation.reconcile_token and lease_expires is not None and lease_expires > now)
        if due is not None and due <= now - timedelta(seconds=_RECONCILE_OVERDUE_GRACE_SECONDS) and not has_live_lease:
            return "stalled", "reconcile_overdue"
        if due is None and not has_live_lease and _older_than(operation.last_progress_at, now=now, seconds=_STALE_SUBMIT_SECONDS):
            return "stalled", "missing_reconcile_schedule"
        return "healthy", None

    def _beat_health(
        self,
        *,
        operation: Any,
        beat: Any,
        tasks: list[Any],
        state: SceneAssetBeatState | None,
        operation_health: str,
        operation_stalled_reason: str | None,
        now: datetime,
    ) -> tuple[str, str | None, str | None]:
        last_progress_at = state.last_progress_at if state is not None else beat.updated_at
        if beat.status in _TERMINAL_BEAT_STATUSES:
            return "terminal", None, last_progress_at
        if any(
            task.status == "submitting" and _older_than(task.submit_started_at, now=now, seconds=_STALE_SUBMIT_SECONDS)
            for task in tasks
        ):
            return "stalled", "stale_submission", last_progress_at
        if operation_health == "stalled":
            return "stalled", operation_stalled_reason, last_progress_at
        return "healthy", None, last_progress_at
