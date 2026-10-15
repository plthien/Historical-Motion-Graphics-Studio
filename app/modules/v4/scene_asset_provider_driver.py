from __future__ import annotations

from typing import Any, Callable

from sqlalchemy.orm import Session

from app.core.time import utc_now_iso
from app.db.models import MediaTask
from app.modules.media.durable_workflow.provider_executor import (
    DurableMediaProviderExecutor,
    ProviderTransport,
)
from app.modules.media.glabs_webhook_client import GLabsWebhookClient
from app.modules.media.media_task_service import MediaTaskService
from app.modules.v4.scene_asset_provider_execution import (
    SceneAssetProviderExecutionService,
    SceneAssetProviderStepResult,
)
from app.repositories.scene_asset_working import SceneAssetReconcileLease


class _SceneAssetProviderDriver:
    """Thin Scene Assets adapter for the shared durable provider envelope.

    All Scene Assets ownership/state/materialization rules remain implemented by
    ``SceneAssetProviderExecutionService``. This adapter only exposes those
    existing domain operations to the shared sequencing contract.
    """

    def __init__(self, service: SceneAssetProviderExecutionService) -> None:
        self.service = service

    def preflight(self, operation_task_id: str) -> None:
        self.service._preflight(operation_task_id)

    def prepare_submission(
        self,
        lease: SceneAssetReconcileLease,
        *,
        operation_task_id: str,
        now_iso: str,
    ) -> str:
        prepared = self.service.coordinator.prepare_submission(
            lease,
            operation_task_id=operation_task_id,
            now_iso=now_iso,
        )
        return str(prepared.operation_task_id)

    def ensure_transport(self, operation_task_id: str, *, now_iso: str) -> ProviderTransport:
        media_task_id, batch_id, provider_settings = self.service._ensure_transport_task(
            operation_task_id,
            now_iso=now_iso,
        )
        return ProviderTransport(
            operation_task_id=operation_task_id,
            media_task_id=media_task_id,
            batch_id=batch_id,
            provider=str(provider_settings["provider"]),
            payload=provider_settings,
        )

    def load_transport(self, operation_task_id: str) -> ProviderTransport | None:
        with self.service.session_factory() as session:
            task, beat, operation, _ = self.service._load_context(session, operation_task_id)
            if task.media_task_id is None:
                session.rollback()
                return None
            media_task = session.get(MediaTask, task.media_task_id)
            if media_task is None:
                session.rollback()
                return None
            self.service._validate_media_owner(media_task, task, beat, operation)
            provider_settings = self.service._frozen_provider_settings(session, operation)
            return ProviderTransport(
                operation_task_id=operation_task_id,
                media_task_id=str(media_task.media_task_id),
                batch_id=str(media_task.batch_id),
                provider=str(provider_settings["provider"]),
                payload=provider_settings,
            )

    def release_terminal_capacity(self, *, provider: str, now_iso: str) -> None:
        self.service.release_terminal_v2_provider_capacity(provider=provider, now_iso=now_iso)

    def submission_authorized(
        self,
        operation_task_id: str,
        media_task_id: str,
        *,
        allow_waiting: bool,
    ) -> bool:
        if allow_waiting:
            return self.service._submission_still_authorized_or_waiting(operation_task_id, media_task_id)
        return self.service._submission_still_authorized(operation_task_id, media_task_id)

    def cancel_queued_transport(self, media_task_id: str, *, now_iso: str) -> None:
        self.service._cancel_queued_transport(media_task_id, now_iso=now_iso)

    def assert_transport_owner(self, operation_task_id: str, media_task_id: str) -> None:
        self.service._assert_transport_owner_before_io(operation_task_id, media_task_id)

    def advance_transport(self, transport: ProviderTransport, *, client: Any | None = None) -> None:
        provider_settings = dict(transport.payload or {})
        media = self.service._new_media_service()
        try:
            media.advance_batch_once(
                transport.batch_id,
                provider_settings=provider_settings,
                client=client,
            )
        finally:
            media.session.close()

    def reconcile_task(self, operation_task_id: str) -> SceneAssetProviderStepResult:
        return self.service.reconcile_task(operation_task_id, now_iso=utc_now_iso())

    def recover_unbridged_submission(
        self,
        operation_task_id: str,
        *,
        now_iso: str,
    ) -> SceneAssetProviderStepResult:
        return self.service._recover_unbridged_submission(operation_task_id, now_iso=now_iso)

    def snapshot_result(self, operation_task_id: str) -> SceneAssetProviderStepResult:
        return self.service._snapshot_result(operation_task_id, candidate_id=None)


class SceneAssetDurableProviderExecutor:
    """Live Scene Assets provider facade backed by the shared durable executor."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        media_service_factory: Callable[[Session], MediaTaskService] | None = None,
    ) -> None:
        self.service = SceneAssetProviderExecutionService(
            session_factory,
            media_service_factory=media_service_factory,
        )
        self.executor = DurableMediaProviderExecutor(_SceneAssetProviderDriver(self.service))

    def submit_task(
        self,
        lease: SceneAssetReconcileLease,
        *,
        operation_task_id: str,
        client: GLabsWebhookClient | None = None,
        now_iso: str | None = None,
    ) -> SceneAssetProviderStepResult:
        return self.executor.submit_task(
            lease,
            operation_task_id=operation_task_id,
            now_iso=now_iso or utc_now_iso(),
            client=client,
        )

    def resume_or_advance_task(
        self,
        *,
        operation_task_id: str,
        client: GLabsWebhookClient | None = None,
        now_iso: str | None = None,
    ) -> SceneAssetProviderStepResult:
        return self.executor.resume_or_advance_task(
            operation_task_id=operation_task_id,
            now_iso=now_iso or utc_now_iso(),
            client=client,
        )

    def recover_stale_submission(
        self,
        *,
        operation_task_id: str,
        stale_before_iso: str,
        now_iso: str | None = None,
    ) -> SceneAssetProviderStepResult:
        return self.service.recover_stale_submission(
            operation_task_id=operation_task_id,
            stale_before_iso=stale_before_iso,
            now_iso=now_iso,
        )
