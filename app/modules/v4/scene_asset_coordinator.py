from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Mapping

from sqlalchemy.orm import Session

from app.api.errors import AppError
from app.core.ids import generate_id
from app.modules.media.durable_workflow.contracts import ReconcileLease
from app.modules.media.durable_workflow.coordinator import DurableMediaCoordinator
from app.modules.media.durable_workflow.states import ACTIVE_OPERATION_STATUSES
from app.modules.media.durable_workflow.timing import parse_iso, plus_seconds
from app.modules.v4.scene_asset_working_state import SceneAssetWorkingStateService
from app.repositories.scene_asset_workflow_adapter import SceneAssetWorkflowRepositoryAdapter
from app.repositories.scene_asset_working import SceneAssetReconcileLease, SceneAssetWorkingRepository


@dataclass(frozen=True, slots=True)
class SceneAssetReconcileAction:
    action: str
    operation_id: str
    reconcile_token: str
    operation_beat_id: str | None = None
    operation_task_id: str | None = None
    task_status: str | None = None
    next_reconcile_at: str | None = None


@dataclass(frozen=True, slots=True)
class PreparedSceneAssetSubmission:
    operation_id: str
    operation_beat_id: str
    operation_task_id: str
    submit_attempt_token: str


def _parse_iso(value: str) -> datetime:
    return parse_iso(value)


def _plus_seconds(value: str, seconds: int) -> str:
    return plus_seconds(value, seconds)


class _SceneAssetWorkflowDriver:
    def __init__(self, session: Session) -> None:
        self.state = SceneAssetWorkingStateService(session)

    def begin_task_submission(
        self,
        *,
        operation_task_id: str,
        submit_attempt_token: str,
        now_iso: str,
    ) -> None:
        self.state.begin_task_submission(
            operation_task_id=operation_task_id,
            submit_attempt_token=submit_attempt_token,
            now_iso=now_iso,
        )

    def refresh_unit(self, *, operation_unit_id: str, now_iso: str) -> None:
        self.state.refresh_operation_beat(operation_beat_id=operation_unit_id, now_iso=now_iso)

    def refresh_operation(self, *, operation_id: str, now_iso: str) -> str:
        return self.state.refresh_operation(operation_id=operation_id, now_iso=now_iso)

    def recover_stale_submitting(
        self,
        *,
        operation_id: str,
        reconcile_token: str,
        stale_before_iso: str,
        now_iso: str,
    ) -> int:
        return self.state.recover_stale_submitting(
            operation_id=operation_id,
            reconcile_token=reconcile_token,
            stale_before_iso=stale_before_iso,
            now_iso=now_iso,
        )

    def cancel_operation(self, *, operation_id: str, now_iso: str) -> Mapping[str, object]:
        return self.state.cancel_operation(operation_id=operation_id, now_iso=now_iso)


class _SceneAssetWorkflowErrors:
    @staticmethod
    def lease_lost(operation_id: str) -> Exception:
        return SceneAssetCoordinator._lease_lost(operation_id)

    @staticmethod
    def operation_not_found(operation_id: str) -> Exception:
        return AppError(
            "SCENE_ASSET_V2_STATE_NOT_FOUND",
            "Scene Asset working operation was not found.",
            404,
            retryable=False,
            details={"operation_id": operation_id},
        )

    @staticmethod
    def task_not_found(operation_task_id: str) -> Exception:
        return AppError(
            "SCENE_ASSET_V2_STATE_NOT_FOUND",
            "Scene Asset operation task was not found.",
            404,
            retryable=False,
            details={"operation_task_id": operation_task_id},
        )

    @staticmethod
    def task_operation_mismatch(operation_id: str, operation_task_id: str) -> Exception:
        return AppError(
            "SCENE_ASSET_V2_STATE_INTEGRITY_INVALID",
            "Scene Asset operation task does not belong to the leased operation.",
            409,
            retryable=False,
            details={"operation_id": operation_id, "operation_task_id": operation_task_id},
        )


class SceneAssetCoordinator:
    """Compatibility facade over the shared durable media coordinator.

    Scene Asset domain state, error contracts, token prefixes, and public import
    paths remain unchanged while generic durable execution mechanics are shared.
    """

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self.session_factory = session_factory
        self._shared = DurableMediaCoordinator(
            session_factory,
            repository_factory=lambda session: SceneAssetWorkflowRepositoryAdapter(
                SceneAssetWorkingRepository(session)
            ),
            driver_factory=_SceneAssetWorkflowDriver,
            reconcile_token_factory=lambda: generate_id("scene_asset_reconcile"),
            submit_token_factory=lambda: generate_id("scene_asset_submit"),
            errors=_SceneAssetWorkflowErrors(),
        )

    @staticmethod
    def _shared_lease(lease: SceneAssetReconcileLease) -> ReconcileLease:
        return ReconcileLease(lease.operation_id, lease.owner, lease.token, lease.expires_at)

    @staticmethod
    def _scene_lease(lease: ReconcileLease) -> SceneAssetReconcileLease:
        return SceneAssetReconcileLease(lease.operation_id, lease.owner, lease.token, lease.expires_at)

    def claim_due_operation(
        self,
        *,
        owner: str,
        now_iso: str | None = None,
        lease_seconds: int = 15,
        scan_limit: int = 50,
    ) -> SceneAssetReconcileLease | None:
        lease = self._shared.claim_due_operation(
            owner=owner,
            now_iso=now_iso,
            lease_seconds=lease_seconds,
            scan_limit=scan_limit,
        )
        return self._scene_lease(lease) if lease is not None else None

    def renew_lease(
        self,
        lease: SceneAssetReconcileLease,
        *,
        now_iso: str | None = None,
        lease_seconds: int = 15,
    ) -> SceneAssetReconcileLease:
        renewed = self._shared.renew_lease(
            self._shared_lease(lease),
            now_iso=now_iso,
            lease_seconds=lease_seconds,
        )
        return self._scene_lease(renewed)

    def release_lease(
        self,
        lease: SceneAssetReconcileLease,
        *,
        now_iso: str | None = None,
        require_current: bool = True,
    ) -> bool:
        return self._shared.release_lease(
            self._shared_lease(lease),
            now_iso=now_iso,
            require_current=require_current,
        )

    def plan_next_action(
        self,
        lease: SceneAssetReconcileLease,
        *,
        now_iso: str | None = None,
    ) -> SceneAssetReconcileAction:
        action = self._shared.plan_next_action(self._shared_lease(lease), now_iso=now_iso)
        return SceneAssetReconcileAction(
            action=action.action,
            operation_id=action.operation_id,
            reconcile_token=action.reconcile_token,
            operation_beat_id=action.operation_unit_id,
            operation_task_id=action.operation_task_id,
            task_status=action.task_status,
            next_reconcile_at=action.next_reconcile_at,
        )

    def prepare_submission(
        self,
        lease: SceneAssetReconcileLease,
        *,
        operation_task_id: str,
        now_iso: str | None = None,
    ) -> PreparedSceneAssetSubmission:
        prepared = self._shared.prepare_submission(
            self._shared_lease(lease),
            operation_task_id=operation_task_id,
            now_iso=now_iso,
        )
        return PreparedSceneAssetSubmission(
            operation_id=prepared.operation_id,
            operation_beat_id=prepared.operation_unit_id,
            operation_task_id=prepared.operation_task_id,
            submit_attempt_token=prepared.submit_attempt_token,
        )

    def refresh_local_aggregate(
        self,
        lease: SceneAssetReconcileLease,
        *,
        now_iso: str | None = None,
    ) -> str:
        return self._shared.refresh_local_aggregate(self._shared_lease(lease), now_iso=now_iso)

    def recover_stale_submissions(
        self,
        lease: SceneAssetReconcileLease,
        *,
        stale_before_iso: str,
        now_iso: str | None = None,
    ) -> int:
        return self._shared.recover_stale_submissions(
            self._shared_lease(lease),
            stale_before_iso=stale_before_iso,
            now_iso=now_iso,
        )

    def cancel_operation(self, *, operation_id: str, now_iso: str | None = None) -> dict[str, object]:
        return self._shared.cancel_operation(operation_id=operation_id, now_iso=now_iso)

    @classmethod
    def _assert_read_lease(
        cls,
        operation: object | None,
        lease: SceneAssetReconcileLease,
        now_dt: datetime,
    ) -> None:
        if operation is None:
            raise AppError(
                "SCENE_ASSET_V2_STATE_NOT_FOUND",
                "Scene Asset working operation was not found.",
                404,
                retryable=False,
                details={"operation_id": lease.operation_id},
            )
        status = getattr(operation, "status", None)
        owner = getattr(operation, "reconcile_owner", None)
        token = getattr(operation, "reconcile_token", None)
        expires_at = getattr(operation, "reconcile_lease_expires_at", None)
        if (
            status not in ACTIVE_OPERATION_STATUSES
            or owner != lease.owner
            or token != lease.token
            or not isinstance(expires_at, str)
            or _parse_iso(expires_at) <= now_dt
        ):
            raise cls._lease_lost(lease.operation_id)

    @staticmethod
    def _lease_lost(operation_id: str) -> AppError:
        return AppError(
            "SCENE_ASSET_V2_RECONCILE_LEASE_LOST",
            "Scene Asset reconciliation lease is no longer current.",
            409,
            retryable=True,
            details={"operation_id": operation_id},
        )
