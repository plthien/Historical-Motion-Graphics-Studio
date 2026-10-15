from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.db.scene_asset_beat_state import SceneAssetBeatState
from app.db.scene_asset_candidate import SceneAssetCandidate
from app.db.scene_asset_operation_beat import SceneAssetOperationBeat
from app.db.scene_asset_operation_task import SceneAssetOperationTask
from app.db.scene_asset_working_operation import SceneAssetWorkingOperation


ACTIVE_OPERATION_STATUSES = ("queued", "running", "finalizing")
TERMINAL_OPERATION_STATUSES = ("completed", "partial", "failed", "cancelled", "superseded")
ACTIVE_OPERATION_BEAT_STATUSES = ("queued", "running")
TERMINAL_OPERATION_BEAT_STATUSES = ("candidate_ready", "failed", "cancelled")
ACTIVE_TASK_STATUSES = (
    "queued",
    "submitting",
    "waiting_provider",
    "downloading",
    "materializing",
)
TERMINAL_TASK_STATUSES = ("submit_outcome_unknown", "completed", "failed", "cancelled")


@dataclass(frozen=True, slots=True)
class SceneAssetReconcileLease:
    operation_id: str
    owner: str
    token: str
    expires_at: str


class SceneAssetWorkingRepository:
    """Transactional repository for Scene Assets V2 live state.

    The repository deliberately contains no provider/network/RQ behavior. Callers
    own transaction boundaries so SQLite write transactions stay short.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    # ---- creation ---------------------------------------------------------------

    def add_operation(self, operation: SceneAssetWorkingOperation) -> SceneAssetWorkingOperation:
        self.session.add(operation)
        self.session.flush()
        return operation

    def add_operation_beat(self, operation_beat: SceneAssetOperationBeat) -> SceneAssetOperationBeat:
        self.session.add(operation_beat)
        self.session.flush()
        return operation_beat

    def add_task(self, task: SceneAssetOperationTask) -> SceneAssetOperationTask:
        self.session.add(task)
        self.session.flush()
        return task

    def add_beat_state(self, state: SceneAssetBeatState) -> SceneAssetBeatState:
        self.session.add(state)
        self.session.flush()
        return state

    def add_candidate(self, candidate: SceneAssetCandidate) -> SceneAssetCandidate:
        self.session.add(candidate)
        self.session.flush()
        return candidate

    # ---- reads ------------------------------------------------------------------

    def get_operation(self, operation_id: str) -> SceneAssetWorkingOperation | None:
        return self.session.get(SceneAssetWorkingOperation, operation_id)

    def list_operations_for_project(self, project_id: str) -> list[SceneAssetWorkingOperation]:
        stmt = (
            select(SceneAssetWorkingOperation)
            .where(SceneAssetWorkingOperation.project_id == project_id)
            .order_by(SceneAssetWorkingOperation.created_at, SceneAssetWorkingOperation.operation_id)
        )
        return list(self.session.scalars(stmt))

    def list_due_operation_ids(self, *, now_iso: str, limit: int = 50) -> list[str]:
        stmt = (
            select(SceneAssetWorkingOperation.operation_id)
            .where(
                SceneAssetWorkingOperation.status.in_(ACTIVE_OPERATION_STATUSES),
                SceneAssetWorkingOperation.next_reconcile_at.is_not(None),
                SceneAssetWorkingOperation.next_reconcile_at <= now_iso,
                or_(
                    SceneAssetWorkingOperation.reconcile_token.is_(None),
                    SceneAssetWorkingOperation.reconcile_lease_expires_at.is_(None),
                    SceneAssetWorkingOperation.reconcile_lease_expires_at <= now_iso,
                ),
            )
            .order_by(
                SceneAssetWorkingOperation.next_reconcile_at,
                SceneAssetWorkingOperation.created_at,
                SceneAssetWorkingOperation.operation_id,
            )
            .limit(limit)
        )
        return [str(value) for value in self.session.scalars(stmt)]

    def get_operation_beat(self, operation_beat_id: str) -> SceneAssetOperationBeat | None:
        return self.session.get(SceneAssetOperationBeat, operation_beat_id)

    def list_operation_beats(self, operation_id: str) -> list[SceneAssetOperationBeat]:
        stmt = (
            select(SceneAssetOperationBeat)
            .where(SceneAssetOperationBeat.operation_id == operation_id)
            .order_by(SceneAssetOperationBeat.scene_id, SceneAssetOperationBeat.beat_id)
        )
        return list(self.session.scalars(stmt))

    def get_task(self, operation_task_id: str) -> SceneAssetOperationTask | None:
        return self.session.get(SceneAssetOperationTask, operation_task_id)

    def list_tasks_for_operation_beat(self, operation_beat_id: str) -> list[SceneAssetOperationTask]:
        stmt = (
            select(SceneAssetOperationTask)
            .where(SceneAssetOperationTask.operation_beat_id == operation_beat_id)
            .order_by(SceneAssetOperationTask.candidate_index, SceneAssetOperationTask.operation_task_id)
        )
        return list(self.session.scalars(stmt))

    def list_tasks_for_operation(self, operation_id: str) -> list[SceneAssetOperationTask]:
        stmt = (
            select(SceneAssetOperationTask)
            .join(
                SceneAssetOperationBeat,
                SceneAssetOperationBeat.operation_beat_id == SceneAssetOperationTask.operation_beat_id,
            )
            .where(SceneAssetOperationBeat.operation_id == operation_id)
            .order_by(
                SceneAssetOperationBeat.scene_id,
                SceneAssetOperationBeat.beat_id,
                SceneAssetOperationTask.candidate_index,
            )
        )
        return list(self.session.scalars(stmt))

    def list_stale_submitting_tasks(
        self,
        *,
        operation_id: str,
        stale_before_iso: str,
    ) -> list[SceneAssetOperationTask]:
        stmt = (
            select(SceneAssetOperationTask)
            .join(
                SceneAssetOperationBeat,
                SceneAssetOperationBeat.operation_beat_id == SceneAssetOperationTask.operation_beat_id,
            )
            .where(
                SceneAssetOperationBeat.operation_id == operation_id,
                SceneAssetOperationTask.status == "submitting",
                SceneAssetOperationTask.submit_started_at.is_not(None),
                SceneAssetOperationTask.submit_started_at <= stale_before_iso,
            )
            .order_by(
                SceneAssetOperationBeat.scene_id,
                SceneAssetOperationBeat.beat_id,
                SceneAssetOperationTask.candidate_index,
            )
        )
        return list(self.session.scalars(stmt))

    def get_beat_state(self, project_id: str, beat_id: str) -> SceneAssetBeatState | None:
        return self.session.get(SceneAssetBeatState, (project_id, beat_id))

    def get_candidate(self, candidate_id: str) -> SceneAssetCandidate | None:
        return self.session.get(SceneAssetCandidate, candidate_id)

    def get_candidate_for_operation_task(self, operation_task_id: str) -> SceneAssetCandidate | None:
        stmt = select(SceneAssetCandidate).where(SceneAssetCandidate.operation_task_id == operation_task_id)
        return self.session.scalar(stmt)

    # ---- reconciliation lease ---------------------------------------------------

    def try_acquire_reconcile_lease(
        self,
        *,
        operation_id: str,
        owner: str,
        token: str,
        now_iso: str,
        expires_at: str,
    ) -> bool:
        stmt = (
            update(SceneAssetWorkingOperation)
            .where(
                SceneAssetWorkingOperation.operation_id == operation_id,
                SceneAssetWorkingOperation.status.in_(ACTIVE_OPERATION_STATUSES),
                SceneAssetWorkingOperation.next_reconcile_at.is_not(None),
                SceneAssetWorkingOperation.next_reconcile_at <= now_iso,
                or_(
                    SceneAssetWorkingOperation.reconcile_token.is_(None),
                    SceneAssetWorkingOperation.reconcile_lease_expires_at.is_(None),
                    SceneAssetWorkingOperation.reconcile_lease_expires_at <= now_iso,
                ),
            )
            .values(
                reconcile_owner=owner,
                reconcile_token=token,
                reconcile_lease_expires_at=expires_at,
                updated_at=now_iso,
            )
        )
        return int(self.session.execute(stmt).rowcount or 0) == 1

    def renew_reconcile_lease(
        self,
        *,
        operation_id: str,
        owner: str,
        token: str,
        now_iso: str,
        expires_at: str,
    ) -> bool:
        stmt = (
            update(SceneAssetWorkingOperation)
            .where(
                SceneAssetWorkingOperation.operation_id == operation_id,
                SceneAssetWorkingOperation.status.in_(ACTIVE_OPERATION_STATUSES),
                SceneAssetWorkingOperation.reconcile_owner == owner,
                SceneAssetWorkingOperation.reconcile_token == token,
                SceneAssetWorkingOperation.reconcile_lease_expires_at.is_not(None),
                SceneAssetWorkingOperation.reconcile_lease_expires_at > now_iso,
            )
            .values(reconcile_lease_expires_at=expires_at, updated_at=now_iso)
        )
        return int(self.session.execute(stmt).rowcount or 0) == 1

    def release_reconcile_lease(
        self,
        *,
        operation_id: str,
        owner: str,
        token: str,
        now_iso: str,
    ) -> bool:
        stmt = (
            update(SceneAssetWorkingOperation)
            .where(
                SceneAssetWorkingOperation.operation_id == operation_id,
                SceneAssetWorkingOperation.reconcile_owner == owner,
                SceneAssetWorkingOperation.reconcile_token == token,
            )
            .values(
                reconcile_owner=None,
                reconcile_token=None,
                reconcile_lease_expires_at=None,
                updated_at=now_iso,
            )
        )
        return int(self.session.execute(stmt).rowcount or 0) == 1

    def fence_reconcile_write(
        self,
        *,
        operation_id: str,
        token: str,
        now_iso: str,
    ) -> bool:
        """Prove the token while obtaining SQLite's writer slot for this step."""
        stmt = (
            update(SceneAssetWorkingOperation)
            .where(
                SceneAssetWorkingOperation.operation_id == operation_id,
                SceneAssetWorkingOperation.status.in_(ACTIVE_OPERATION_STATUSES),
                SceneAssetWorkingOperation.reconcile_token == token,
                SceneAssetWorkingOperation.reconcile_lease_expires_at.is_not(None),
                SceneAssetWorkingOperation.reconcile_lease_expires_at > now_iso,
            )
            .values(updated_at=now_iso)
        )
        return int(self.session.execute(stmt).rowcount or 0) == 1

    # ---- beat-local CAS ----------------------------------------------------------

    def compare_and_set_beat_state(
        self,
        *,
        project_id: str,
        beat_id: str,
        expected_version: int,
        values: dict[str, Any],
        updated_at: str,
    ) -> bool:
        forbidden = {"project_id", "beat_id", "scene_id", "version", "created_at"}
        if forbidden.intersection(values):
            raise ValueError("BeatState CAS cannot mutate identity/version fields directly.")
        stmt = (
            update(SceneAssetBeatState)
            .where(
                SceneAssetBeatState.project_id == project_id,
                SceneAssetBeatState.beat_id == beat_id,
                SceneAssetBeatState.version == expected_version,
            )
            .values(
                **values,
                version=SceneAssetBeatState.version + 1,
                updated_at=updated_at,
            )
        )
        changed = int(self.session.execute(stmt).rowcount or 0) == 1
        if changed:
            self.session.expire_all()
        return changed

    def release_active_operation_beat(
        self,
        *,
        operation_beat_id: str,
        updated_at: str,
    ) -> int:
        stmt = (
            update(SceneAssetBeatState)
            .where(SceneAssetBeatState.active_operation_beat_id == operation_beat_id)
            .values(
                active_operation_beat_id=None,
                version=SceneAssetBeatState.version + 1,
                updated_at=updated_at,
                last_progress_at=updated_at,
            )
        )
        return int(self.session.execute(stmt).rowcount or 0)

    def fail_active_operation_beat_state(
        self,
        *,
        operation_beat_id: str,
        updated_at: str,
        error_json: dict[str, Any],
    ) -> int:
        """Mark an empty beat failed without overwriting an existing stable output."""
        first = self.session.execute(
            update(SceneAssetBeatState)
            .where(
                SceneAssetBeatState.active_operation_beat_id == operation_beat_id,
                SceneAssetBeatState.current_draft_candidate_id.is_(None),
                SceneAssetBeatState.selected_candidate_id.is_(None),
            )
            .values(
                active_operation_beat_id=None,
                resolution_status="failed",
                last_error_json=error_json,
                last_progress_at=updated_at,
                version=SceneAssetBeatState.version + 1,
                updated_at=updated_at,
            )
        )
        released = int(first.rowcount or 0)
        released += self.release_active_operation_beat(
            operation_beat_id=operation_beat_id,
            updated_at=updated_at,
        )
        return released

    # ---- guarded state transitions ---------------------------------------------

    def transition_operation(
        self,
        *,
        operation_id: str,
        expected_statuses: Iterable[str],
        target_status: str,
        values: dict[str, Any],
    ) -> bool:
        stmt = (
            update(SceneAssetWorkingOperation)
            .where(
                SceneAssetWorkingOperation.operation_id == operation_id,
                SceneAssetWorkingOperation.status.in_(tuple(expected_statuses)),
            )
            .values(status=target_status, **values)
        )
        return int(self.session.execute(stmt).rowcount or 0) == 1

    def transition_operation_beat(
        self,
        *,
        operation_beat_id: str,
        expected_statuses: Iterable[str],
        target_status: str,
        values: dict[str, Any],
    ) -> bool:
        stmt = (
            update(SceneAssetOperationBeat)
            .where(
                SceneAssetOperationBeat.operation_beat_id == operation_beat_id,
                SceneAssetOperationBeat.status.in_(tuple(expected_statuses)),
            )
            .values(
                status=target_status,
                version=SceneAssetOperationBeat.version + 1,
                **values,
            )
        )
        changed = int(self.session.execute(stmt).rowcount or 0) == 1
        if not changed or target_status not in TERMINAL_OPERATION_BEAT_STATUSES:
            return changed
        updated_at = values.get("updated_at")
        if not isinstance(updated_at, str) or not updated_at:
            raise ValueError("terminal operation beat transition requires updated_at")
        if target_status == "failed":
            self.fail_active_operation_beat_state(
                operation_beat_id=operation_beat_id,
                updated_at=updated_at,
                error_json={
                    "error_code": values.get("error_code") or "SCENE_ASSET_GENERATION_FAILED",
                    "error_message": values.get("error_message") or "Scene Asset generation failed.",
                    "error_details": values.get("error_details_json") or {},
                },
            )
        else:
            self.release_active_operation_beat(
                operation_beat_id=operation_beat_id,
                updated_at=updated_at,
            )
        return True

    def transition_task(
        self,
        *,
        operation_task_id: str,
        expected_statuses: Iterable[str],
        target_status: str,
        values: dict[str, Any],
    ) -> bool:
        stmt = (
            update(SceneAssetOperationTask)
            .where(
                SceneAssetOperationTask.operation_task_id == operation_task_id,
                SceneAssetOperationTask.status.in_(tuple(expected_statuses)),
            )
            .values(status=target_status, **values)
        )
        return int(self.session.execute(stmt).rowcount or 0) == 1

    def clear_operation_lease_and_schedule(
        self,
        *,
        operation_id: str,
        updated_at: str,
    ) -> None:
        self.session.execute(
            update(SceneAssetWorkingOperation)
            .where(SceneAssetWorkingOperation.operation_id == operation_id)
            .values(
                next_reconcile_at=None,
                reconcile_owner=None,
                reconcile_token=None,
                reconcile_lease_expires_at=None,
                updated_at=updated_at,
            )
        )
