from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.errors import AppError
from app.core.ids import generate_id
from app.core.time import utc_now_iso
from app.db import scene_asset_working_models as _scene_asset_working_models  # noqa: F401
from app.db.enums import ArtifactStatus, ArtifactType
from app.db.models import Artifact, MediaTask
from app.db.scene_asset_beat_state import SceneAssetBeatState
from app.db.scene_asset_candidate import SceneAssetCandidate
from app.db.scene_asset_operation_beat import SceneAssetOperationBeat
from app.db.scene_asset_operation_task import SceneAssetOperationTask
from app.db.scene_asset_working_operation import SceneAssetWorkingOperation
from app.modules.media.glabs_webhook_client import GLabsWebhookClient
from app.modules.media.media_task_service import (
    NormalizedImageGenerationCommand,
    PERSISTED_SCENE_ASSET_PURPOSE,
    MediaTaskService,
)
from app.modules.v4.scene_asset_coordinator import SceneAssetCoordinator
from app.modules.v4.scene_asset_manifest import canonical_hash
from app.modules.v4.scene_asset_working_state import SceneAssetWorkingStateService
from app.repositories import SettingsRepository
from app.repositories.scene_asset_working import ACTIVE_OPERATION_STATUSES, SceneAssetReconcileLease
from app.services.media_settings import MediaProviderSettingsService


_MEDIA_FAILURE_STATUSES = {"failed", "download_failed"}
_PROVIDER_CAPACITY_STATUSES = {"submitted", "provider_pending", "provider_running"}
_PROVIDER_SNAPSHOT_REQUIRED = {
    "provider",
    "default_image_model",
    "default_upscale",
    "poll_interval_seconds",
    "max_parallel_tasks",
    "enabled_media_kinds",
    "enabled",
}
_PROVIDER_SNAPSHOT_FIELDS = frozenset({
    "provider",
    "default_image_model",
    "default_image_aspect_ratio",
    "default_upscale",
    "poll_interval_seconds",
    "max_parallel_tasks",
    "enabled_media_kinds",
    "enabled",
})
_RETIRED_PROVIDER_SNAPSHOT_FIELDS = frozenset({
    "supported_image_aspect_ratios",
    "max_poll_attempts",
})


@dataclass(frozen=True, slots=True)
class SceneAssetProviderStepResult:
    operation_id: str
    operation_task_id: str
    media_task_id: str | None
    media_status: str | None
    v2_task_status: str
    operation_status: str
    candidate_id: str | None


def _parse_iso(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise AppError(
            "SCENE_ASSET_V2_TIMESTAMP_INVALID",
            "Scene Asset V2 timestamp is invalid.",
            409,
            retryable=False,
        ) from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _plus_seconds(value: str, seconds: int) -> str:
    return (_parse_iso(value) + timedelta(seconds=seconds)).isoformat()


class SceneAssetProviderExecutionService:
    """Scene Assets V2 domain provider/materialization service.

    Provider lifecycle ordering is owned by the shared durable-media executor.
    This service keeps Scene Asset validation, transport ownership, task-state
    reconciliation, candidate materialization, and compatibility entrypoints.
    MediaTask/MediaBatch remain transport/download records only and never become
    the V2 workflow owner.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        media_service_factory: Callable[[Session], MediaTaskService] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.coordinator = SceneAssetCoordinator(session_factory)
        self.media_service_factory = media_service_factory or (lambda session: MediaTaskService(session))

    def submit_task(
        self,
        lease: SceneAssetReconcileLease,
        *,
        operation_task_id: str,
        client: GLabsWebhookClient | None = None,
        now_iso: str | None = None,
    ) -> SceneAssetProviderStepResult:
        """Compatibility entrypoint delegating provider ordering to the shared executor."""
        from app.modules.v4.scene_asset_provider_driver import SceneAssetDurableProviderExecutor

        return SceneAssetDurableProviderExecutor(
            self.session_factory,
            media_service_factory=self.media_service_factory,
        ).submit_task(
            lease,
            operation_task_id=operation_task_id,
            client=client,
            now_iso=now_iso,
        )

    def resume_or_advance_task(
        self,
        *,
        operation_task_id: str,
        client: GLabsWebhookClient | None = None,
        now_iso: str | None = None,
    ) -> SceneAssetProviderStepResult:
        """Compatibility entrypoint delegating resume/poll ordering to the shared executor."""
        from app.modules.v4.scene_asset_provider_driver import SceneAssetDurableProviderExecutor

        return SceneAssetDurableProviderExecutor(
            self.session_factory,
            media_service_factory=self.media_service_factory,
        ).resume_or_advance_task(
            operation_task_id=operation_task_id,
            client=client,
            now_iso=now_iso,
        )

    def reconcile_task(
        self,
        operation_task_id: str,
        *,
        now_iso: str | None = None,
    ) -> SceneAssetProviderStepResult:
        now = now_iso or utc_now_iso()
        candidate_id: str | None = None
        with self.session_factory() as session:
            task, beat, operation, beat_state = self._load_context(session, operation_task_id)
            if operation.status not in ACTIVE_OPERATION_STATUSES or operation.cancel_requested_at is not None:
                session.rollback()
                return self._snapshot_result(operation_task_id, candidate_id=None)

            media_task = session.get(MediaTask, task.media_task_id) if task.media_task_id else None
            if media_task is None:
                if task.status == "submitting":
                    self._mark_submit_unknown(
                        session,
                        task,
                        now_iso=now,
                        details={"reason": "media_task_missing_after_submit_intent"},
                    )
                    self._refresh_aggregates(session, beat, operation, now_iso=now)
                    session.commit()
                return self._snapshot_result(operation_task_id, candidate_id=None)

            self._validate_media_owner(media_task, task, beat, operation)
            media_status = str(media_task.status)
            state = SceneAssetWorkingStateService(session)
            poll_interval = self._poll_interval(session, operation)

            if media_status == "queued":
                if media_task.external_task_id or media_task.provider_submitted_at:
                    raise AppError(
                        "SCENE_ASSET_V2_MEDIA_STATE_INVALID",
                        "Queued Scene Asset transport unexpectedly has provider identity.",
                        409,
                        retryable=False,
                        details={"media_task_id": media_task.media_task_id},
                    )
                due = _plus_seconds(now, poll_interval)
                state.defer_task_for_provider_capacity(
                    operation_task_id=task.operation_task_id,
                    media_task_id=media_task.media_task_id,
                    now_iso=now,
                    next_reconcile_at=due,
                )
                self._set_operation_due(session, operation.operation_id, due, now_iso=now)
            elif media_status in {"submitted", "provider_pending", "provider_running"}:
                if not media_task.external_task_id or not media_task.provider_submitted_at:
                    self._mark_submit_unknown(
                        session,
                        task,
                        now_iso=now,
                        details={
                            "reason": "provider_submit_claim_without_durable_provider_identity",
                            "media_task_id": media_task.media_task_id,
                            "media_status": media_status,
                        },
                    )
                else:
                    due = _plus_seconds(now, poll_interval)
                    self._advance_to_waiting(state, task, media_task, now_iso=now, due=due)
                    self._set_operation_due(session, operation.operation_id, due, now_iso=now)
            elif media_status == "downloading":
                self._advance_to_downloading(state, task, media_task, now_iso=now)
                self._set_operation_due(session, operation.operation_id, now, now_iso=now)
            elif media_status == "completed":
                candidate_id = self._materialize_completed(
                    session,
                    state=state,
                    task=task,
                    beat=beat,
                    operation=operation,
                    beat_state=beat_state,
                    media_task=media_task,
                    now_iso=now,
                )
            elif media_status in _MEDIA_FAILURE_STATUSES:
                if not media_task.external_task_id and task.status == "submitting":
                    self._mark_submit_unknown(
                        session,
                        task,
                        now_iso=now,
                        details={
                            "reason": "provider_submit_failed_without_durable_provider_identity",
                            "media_task_id": media_task.media_task_id,
                            "media_error_code": media_task.error_code,
                        },
                    )
                else:
                    self._advance_to_failed(state, task, media_task, now_iso=now)
            elif media_status == "canceled":
                if task.status not in {"completed", "failed", "cancelled", "submit_outcome_unknown"}:
                    self._guarded_task_update(
                        session,
                        task.operation_task_id,
                        expected_statuses=(str(task.status),),
                        values={
                            "status": "cancelled",
                            "completed_at": now,
                            "next_reconcile_at": None,
                            "updated_at": now,
                            "error_code": "SCENE_ASSET_OPERATION_CANCELLED",
                            "error_message": "Scene Asset transport task was cancelled locally.",
                            "error_details_json": {"media_task_id": media_task.media_task_id},
                        },
                    )
            else:
                raise AppError(
                    "SCENE_ASSET_V2_MEDIA_STATE_INVALID",
                    "Scene Asset transport task has an unsupported status.",
                    409,
                    retryable=False,
                    details={"media_task_id": media_task.media_task_id, "status": media_status},
                )

            session.flush()
            session.expire_all()
            task = session.get(SceneAssetOperationTask, operation_task_id)
            beat = session.get(SceneAssetOperationBeat, beat.operation_beat_id)
            operation = session.get(SceneAssetWorkingOperation, operation.operation_id)
            assert task is not None and beat is not None and operation is not None
            self._refresh_aggregates(session, beat, operation, now_iso=now)
            self._recompute_operation_due(session, operation.operation_id, now_iso=now)
            session.commit()
        return self._snapshot_result(operation_task_id, candidate_id=candidate_id)

    def recover_stale_submission(
        self,
        *,
        operation_task_id: str,
        stale_before_iso: str,
        now_iso: str | None = None,
    ) -> SceneAssetProviderStepResult:
        now = now_iso or utc_now_iso()
        with self.session_factory() as session:
            task, beat, operation, _ = self._load_context(session, operation_task_id)
            if task.status != "submitting" or not task.submit_started_at:
                session.rollback()
                return self._snapshot_result(operation_task_id, candidate_id=None)
            if _parse_iso(task.submit_started_at) > _parse_iso(stale_before_iso):
                session.rollback()
                return self._snapshot_result(operation_task_id, candidate_id=None)
            media_task = session.get(MediaTask, task.media_task_id) if task.media_task_id else None
            if media_task is not None:
                self._validate_media_owner(media_task, task, beat, operation)

            if media_task is not None and media_task.status == "queued" and media_task.external_task_id is None:
                self._set_task_due(session, task, now, now_iso=now)
                self._set_operation_due(session, operation.operation_id, now, now_iso=now)
            elif media_task is not None and media_task.external_task_id and media_task.provider_submitted_at:
                self._advance_to_waiting(
                    SceneAssetWorkingStateService(session),
                    task,
                    media_task,
                    now_iso=now,
                    due=now,
                )
                self._set_operation_due(session, operation.operation_id, now, now_iso=now)
            else:
                self._mark_submit_unknown(
                    session,
                    task,
                    now_iso=now,
                    details={
                        "reason": "stale_submit_outcome_ambiguous",
                        "media_task_id": task.media_task_id,
                        "media_status": media_task.status if media_task is not None else None,
                    },
                )
            self._refresh_aggregates(session, beat, operation, now_iso=now)
            self._recompute_operation_due(session, operation.operation_id, now_iso=now)
            session.commit()
        return self._snapshot_result(operation_task_id, candidate_id=None)

    def release_terminal_v2_provider_capacity(
        self,
        *,
        provider: str | None = None,
        now_iso: str | None = None,
    ) -> int:
        """Release local provider slots whose Scene Assets V2 owner is terminal.

        Cancellation is local by contract, so a remote provider may still finish
        after this transition. Late results remain fenced by the V2 ownership
        checks and can never materialize into a terminal operation.
        """
        now = now_iso or utc_now_iso()
        released = 0
        with self.session_factory() as session:
            stmt = select(MediaTask).where(MediaTask.status.in_(tuple(sorted(_PROVIDER_CAPACITY_STATUSES))))
            if provider is not None:
                stmt = stmt.where(MediaTask.external_provider == provider)
            media_tasks = list(session.scalars(stmt))
            for media_task in media_tasks:
                metadata = dict(media_task.metadata_json or {})
                if metadata.get("scene_asset_v2") is not True:
                    continue
                operation_task_id = metadata.get("scene_asset_operation_task_id")
                task = (
                    session.get(SceneAssetOperationTask, str(operation_task_id))
                    if isinstance(operation_task_id, str) and operation_task_id
                    else None
                )
                beat = session.get(SceneAssetOperationBeat, task.operation_beat_id) if task is not None else None
                operation = session.get(SceneAssetWorkingOperation, beat.operation_id) if beat is not None else None
                beat_state = (
                    session.get(SceneAssetBeatState, (operation.project_id, beat.beat_id))
                    if operation is not None and beat is not None
                    else None
                )
                owner_active = bool(
                    task is not None
                    and beat is not None
                    and operation is not None
                    and beat_state is not None
                    and task.media_task_id == media_task.media_task_id
                    and task.status in {"submitting", "waiting_provider", "downloading", "materializing"}
                    and operation.status in ACTIVE_OPERATION_STATUSES
                    and operation.cancel_requested_at is None
                    and beat.status in {"queued", "running"}
                    and beat_state.active_operation_beat_id == beat.operation_beat_id
                )
                if owner_active:
                    continue
                media_task.status = "canceled"
                media_task.provider_status = None
                media_task.error_code = "SCENE_ASSET_V2_MEDIA_OWNER_TERMINAL"
                media_task.error = "Scene Asset media task owner is no longer active."
                media_task.error_detail = (
                    "Scene Assets V2 released this local provider-capacity slot after its durable owner "
                    "became terminal or disappeared. Any late remote result remains fenced and cannot publish."
                )
                media_task.poll_claim_token = None
                media_task.poll_claimed_at = None
                media_task.poll_claim_expires_at = None
                media_task.completed_at = now
                media_task.updated_at = now
                released += 1
            if released:
                session.commit()
            else:
                session.rollback()
        return released

    def _preflight(self, operation_task_id: str) -> None:
        with self.session_factory() as session:
            task, beat, operation, beat_state = self._load_context(session, operation_task_id)
            if (
                task.status != "queued"
                or operation.status not in ACTIVE_OPERATION_STATUSES
                or operation.cancel_requested_at is not None
                or beat.status not in {"queued", "running"}
                or beat_state.active_operation_beat_id != beat.operation_beat_id
            ):
                raise AppError(
                    "SCENE_ASSET_V2_SUBMISSION_NOT_ACTIVE",
                    "Scene Asset task is not eligible for provider submission.",
                    409,
                    retryable=True,
                    details={"operation_task_id": operation_task_id, "status": task.status},
                )
            settings = self._frozen_provider_settings(session, operation)
            self._build_command(operation, beat, task, settings)

    def _ensure_transport_task(
        self,
        operation_task_id: str,
        *,
        now_iso: str,
    ) -> tuple[str, str, dict[str, Any]]:
        with self.session_factory() as session:
            task, beat, operation, _ = self._load_context(session, operation_task_id)
            if task.status != "submitting":
                raise AppError(
                    "SCENE_ASSET_V2_SUBMISSION_NOT_ACTIVE",
                    "Scene Asset task is not in submitting state.",
                    409,
                    retryable=True,
                    details={"operation_task_id": operation_task_id, "status": task.status},
                )
            settings = self._frozen_provider_settings(session, operation)
            if task.media_task_id:
                media_task = session.get(MediaTask, task.media_task_id)
                if media_task is None:
                    raise AppError(
                        "SCENE_ASSET_V2_STATE_INTEGRITY_INVALID",
                        "Scene Asset operation task references a missing MediaTask.",
                        409,
                        retryable=False,
                        details={"operation_task_id": operation_task_id, "media_task_id": task.media_task_id},
                    )
                self._validate_media_owner(media_task, task, beat, operation)
                return str(media_task.media_task_id), str(media_task.batch_id), settings
            command = self._build_command(operation, beat, task, settings)

        media = self._new_media_service()
        try:
            response = media.submit_internal_image_generation(
                command,
                provider_settings=settings,
                submit_immediately=False,
            )
            response_tasks = response.get("tasks", [])
            if not isinstance(response_tasks, list) or len(response_tasks) != 1:
                raise AppError(
                    "SCENE_ASSET_V2_MEDIA_STATE_INVALID",
                    "Scene Asset V2 transport bridge must create exactly one MediaTask per operation task.",
                    409,
                    retryable=False,
                    details={"operation_task_id": operation_task_id},
                )
            media_task_id = str(response_tasks[0]["media_task_id"])
            batch_id = str(response["batch_id"])
        finally:
            media.session.close()

        with self.session_factory() as session:
            changed = session.execute(
                update(SceneAssetOperationTask)
                .where(
                    SceneAssetOperationTask.operation_task_id == operation_task_id,
                    SceneAssetOperationTask.status == "submitting",
                    SceneAssetOperationTask.media_task_id.is_(None),
                )
                .values(media_task_id=media_task_id, updated_at=now_iso)
            )
            if int(changed.rowcount or 0) != 1:
                session.rollback()
                self._cancel_queued_transport(media_task_id, now_iso=now_iso)
                raise AppError(
                    "SCENE_ASSET_V2_SUBMISSION_NOT_ACTIVE",
                    "Scene Asset task changed before the transport bridge could be linked.",
                    409,
                    retryable=True,
                    details={"operation_task_id": operation_task_id},
                )
            session.commit()
        self._assert_transport_owner_before_io(operation_task_id, media_task_id)
        return media_task_id, batch_id, settings

    def _build_command(
        self,
        operation: SceneAssetWorkingOperation,
        beat: SceneAssetOperationBeat,
        task: SceneAssetOperationTask,
        provider_settings: dict[str, Any],
    ) -> NormalizedImageGenerationCommand:
        if canonical_hash(beat.generation_options_json) != beat.generation_options_hash:
            raise self._frozen_input_error(beat, "generation options")
        if canonical_hash(beat.reference_bindings_json) != beat.reference_bindings_hash:
            raise self._frozen_input_error(beat, "reference bindings")
        aspect_ratio = beat.generation_options_json.get("aspect_ratio")
        if not isinstance(aspect_ratio, str) or not aspect_ratio:
            raise self._frozen_input_error(beat, "aspect ratio")
        references = self._reference_descriptors(beat)
        metadata: dict[str, Any] = {
            "scene_asset_v2": True,
            "scene_asset_operation_id": operation.operation_id,
            "scene_asset_operation_beat_id": beat.operation_beat_id,
            "scene_asset_operation_task_id": task.operation_task_id,
            "scene_id": beat.scene_id,
            "beat_id": beat.beat_id,
            "candidate_index": int(task.candidate_index),
            "prompt_revision_id": beat.prompt_revision_id,
            "prompt_bundle_artifact_id": beat.prompt_bundle_artifact_id,
            "prompt_item_id": beat.prompt_item_id,
            "prompt_hash": beat.prompt_hash,
            "reference_binding_snapshot_hash": beat.reference_bindings_hash,
            "generation_settings_snapshot_hash": beat.generation_options_hash,
            "provider_settings_snapshot_hash": operation.provider_settings_snapshot_hash,
        }
        if beat.continuity_snapshot_json is not None:
            metadata["continuity_snapshot"] = dict(beat.continuity_snapshot_json)
        return NormalizedImageGenerationCommand(
            purpose=PERSISTED_SCENE_ASSET_PURPOSE,
            project_id=operation.project_id,
            domain_revision_id=operation.operation_id,
            entity_type="beat",
            entity_id=beat.beat_id,
            candidate_count=1,
            prompt=beat.image_prompt,
            negative_prompt=beat.negative_prompt,
            model=str(provider_settings["default_image_model"]),
            aspect_ratio=aspect_ratio,
            upscale=tuple(str(item) for item in provider_settings.get("default_upscale", [])),
            reference_images=tuple(references),
            storage_namespace=(
                f"project/{operation.project_id}/scene_assets/"
                f"{beat.prompt_revision_id}/{beat.beat_id}"
            ),
            metadata=metadata,
            style_bible_revision_id=None,
        )

    @staticmethod
    def _reference_descriptors(beat: SceneAssetOperationBeat) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for binding in beat.reference_bindings_json:
            if not isinstance(binding, dict):
                raise SceneAssetProviderExecutionService._frozen_input_error(beat, "reference binding")
            required = ("anchor_id", "media_artifact_id", "media_sha256", "mime_type")
            if not all(isinstance(binding.get(key), str) and binding.get(key) for key in required):
                raise SceneAssetProviderExecutionService._frozen_input_error(beat, "reference binding")
            result.append({
                "reference_tag": str(binding["anchor_id"]),
                "artifact_id": str(binding["media_artifact_id"]),
                "sha256": str(binding["media_sha256"]),
                "mime_type": str(binding["mime_type"]),
            })
        continuity = beat.continuity_snapshot_json
        if continuity is not None:
            artifact_id = continuity.get("source_asset_artifact_id") or continuity.get("artifact_id")
            sha256 = continuity.get("source_asset_sha256") or continuity.get("sha256")
            mime_type = continuity.get("mime_type")
            if not all(isinstance(value, str) and value for value in (artifact_id, sha256, mime_type)):
                raise SceneAssetProviderExecutionService._frozen_input_error(beat, "continuity snapshot")
            result.append({
                "reference_tag": "prev_beat",
                "artifact_id": artifact_id,
                "sha256": sha256,
                "mime_type": mime_type,
            })
        return result

    def _frozen_provider_settings(
        self,
        session: Session,
        operation: SceneAssetWorkingOperation,
    ) -> dict[str, Any]:
        frozen = dict(operation.provider_settings_snapshot_json or {})
        if canonical_hash(frozen) != operation.provider_settings_snapshot_hash:
            raise AppError(
                "SCENE_ASSET_V2_PROVIDER_SNAPSHOT_INVALID",
                "Frozen Scene Asset provider settings failed integrity validation.",
                409,
                retryable=False,
                details={"operation_id": operation.operation_id},
            )
        missing = sorted(_PROVIDER_SNAPSHOT_REQUIRED - set(frozen))
        if missing:
            raise AppError(
                "SCENE_ASSET_V2_PROVIDER_SNAPSHOT_INVALID",
                "Frozen Scene Asset provider settings are incomplete.",
                409,
                retryable=False,
                details={"operation_id": operation.operation_id, "missing_fields": missing},
            )
        if "api_key" in frozen:
            raise AppError(
                "SCENE_ASSET_V2_PROVIDER_SNAPSHOT_INVALID",
                "Frozen Scene Asset provider settings must not contain credentials.",
                409,
                retryable=False,
            )
        unknown = sorted(set(frozen) - _PROVIDER_SNAPSHOT_FIELDS - _RETIRED_PROVIDER_SNAPSHOT_FIELDS)
        if unknown:
            raise AppError(
                "SCENE_ASSET_V2_PROVIDER_SNAPSHOT_INVALID",
                "Frozen Scene Asset provider settings contain unsupported fields.",
                409,
                retryable=False,
                details={"operation_id": operation.operation_id, "unknown_fields": unknown},
            )
        runtime_frozen = {key: value for key, value in frozen.items() if key in _PROVIDER_SNAPSHOT_FIELDS}
        settings_service = MediaProviderSettingsService(session, SettingsRepository(session))
        current = settings_service.get_settings_for_backend()
        if str(runtime_frozen["provider"]) != str(current.get("provider", "")):
            raise AppError(
                "SCENE_ASSET_V2_PROVIDER_SNAPSHOT_INVALID",
                "Current provider identity does not match the frozen Scene Asset provider.",
                409,
                retryable=False,
                details={"operation_id": operation.operation_id},
            )
        merged = dict(current)
        for key, value in runtime_frozen.items():
            if key != "api_key_configured":
                merged[key] = value
        merged["api_key"] = current.get("api_key", "")
        merged["api_key_configured"] = bool(merged["api_key"])
        settings_service._validate_runtime_ready(merged)
        return merged

    def _materialize_completed(
        self,
        session: Session,
        *,
        state: SceneAssetWorkingStateService,
        task: SceneAssetOperationTask,
        beat: SceneAssetOperationBeat,
        operation: SceneAssetWorkingOperation,
        beat_state: SceneAssetBeatState,
        media_task: MediaTask,
        now_iso: str,
    ) -> str:
        del beat_state
        self._advance_to_materializing(state, task, media_task, now_iso=now_iso)
        session.flush()
        session.expire_all()
        task = session.get(SceneAssetOperationTask, task.operation_task_id)
        if task is None:
            raise AppError("SCENE_ASSET_V2_STATE_NOT_FOUND", "Scene Asset operation task disappeared.", 409, retryable=True)
        fence = state.assert_task_can_materialize(operation_task_id=task.operation_task_id)

        existing = session.scalar(
            select(SceneAssetCandidate).where(SceneAssetCandidate.operation_task_id == task.operation_task_id)
        )
        if existing is not None:
            return existing.candidate_id
        results = [item for item in (media_task.result_json or []) if isinstance(item, dict)]
        if len(results) != 1 or not isinstance(results[0].get("artifact_id"), str):
            raise AppError(
                "SCENE_ASSET_V2_MEDIA_RESULT_INVALID",
                "Scene Asset V2 requires exactly one downloaded image Artifact per operation task.",
                409,
                retryable=False,
                details={"media_task_id": media_task.media_task_id, "result_count": len(results)},
            )
        artifact_id = str(results[0]["artifact_id"])
        artifact = session.get(Artifact, artifact_id)
        if (
            artifact is None
            or artifact.status != ArtifactStatus.READY
            or artifact.deleted_at is not None
            or artifact.artifact_type != ArtifactType.IMAGE
            or not artifact.logical_id.startswith(f"project/{operation.project_id}/")
            or not artifact.sha256
            or not artifact.mime_type
        ):
            raise AppError(
                "SCENE_ASSET_V2_MEDIA_RESULT_INVALID",
                "Downloaded Scene Asset Artifact failed V2 ownership/integrity validation.",
                409,
                retryable=False,
                details={"media_task_id": media_task.media_task_id, "artifact_id": artifact_id},
            )
        candidate = SceneAssetCandidate(
            candidate_id=generate_id("scene_asset_candidate"),
            project_id=operation.project_id,
            scene_id=beat.scene_id,
            beat_id=beat.beat_id,
            artifact_id=artifact_id,
            source_kind="generated",
            operation_beat_id=beat.operation_beat_id,
            operation_task_id=task.operation_task_id,
            source_prompt_revision_id=beat.prompt_revision_id,
            source_prompt_bundle_artifact_id=beat.prompt_bundle_artifact_id,
            source_prompt_item_id=beat.prompt_item_id,
            source_prompt_hash=beat.prompt_hash,
            continuity_snapshot_json=(dict(beat.continuity_snapshot_json) if beat.continuity_snapshot_json is not None else None),
            created_at=now_iso,
        )
        session.add(candidate)
        session.flush()

        current_state = session.get(SceneAssetBeatState, (operation.project_id, beat.beat_id))
        if current_state is None:
            raise AppError("SCENE_ASSET_V2_STATE_NOT_FOUND", "Scene Asset BeatState disappeared.", 409, retryable=True)
        seed_current = (
            current_state.current_draft_candidate_id is None
            and current_state.selected_candidate_id is None
            and not operation.operation_type.startswith("regenerate")
        )
        values: dict[str, Any] = {"last_progress_at": now_iso, "last_error_json": None}
        if seed_current:
            values.update(
                resolution_status="candidate_ready",
                review_status="current_draft",
                current_draft_candidate_id=candidate.candidate_id,
            )
        state.compare_and_set_beat_state(
            project_id=fence.project_id,
            beat_id=fence.beat_id,
            expected_version=fence.beat_state_version,
            values=values,
            now_iso=now_iso,
        )
        state.mark_task_completed(operation_task_id=task.operation_task_id, now_iso=now_iso)
        return candidate.candidate_id

    def _advance_to_waiting(
        self,
        state: SceneAssetWorkingStateService,
        task: SceneAssetOperationTask,
        media_task: MediaTask,
        *,
        now_iso: str,
        due: str,
    ) -> None:
        if task.status == "submitting":
            state.mark_task_waiting_provider(
                operation_task_id=task.operation_task_id,
                media_task_id=media_task.media_task_id,
                now_iso=now_iso,
                next_reconcile_at=due,
            )
        elif task.status == "waiting_provider":
            self._set_task_due(state.session, task, due, now_iso=now_iso)

    def _advance_to_downloading(
        self,
        state: SceneAssetWorkingStateService,
        task: SceneAssetOperationTask,
        media_task: MediaTask,
        *,
        now_iso: str,
    ) -> None:
        if task.status == "submitting":
            self._advance_to_waiting(state, task, media_task, now_iso=now_iso, due=now_iso)
            state.session.flush()
            state.session.expire_all()
            task = state.repo.get_task(task.operation_task_id) or task
        if task.status == "waiting_provider":
            state.mark_task_downloading(operation_task_id=task.operation_task_id, now_iso=now_iso)

    def _advance_to_materializing(
        self,
        state: SceneAssetWorkingStateService,
        task: SceneAssetOperationTask,
        media_task: MediaTask,
        *,
        now_iso: str,
    ) -> None:
        self._advance_to_downloading(state, task, media_task, now_iso=now_iso)
        state.session.flush()
        state.session.expire_all()
        refreshed = state.repo.get_task(task.operation_task_id)
        if refreshed is not None and refreshed.status == "downloading":
            state.mark_task_materializing(operation_task_id=task.operation_task_id, now_iso=now_iso)

    def _advance_to_failed(
        self,
        state: SceneAssetWorkingStateService,
        task: SceneAssetOperationTask,
        media_task: MediaTask,
        *,
        now_iso: str,
    ) -> None:
        if task.status in {"completed", "failed", "cancelled", "submit_outcome_unknown"}:
            return
        state.mark_task_failed(
            operation_task_id=task.operation_task_id,
            now_iso=now_iso,
            error_code=str(media_task.error_code or "MEDIA_PROVIDER_UPSTREAM_ERROR"),
            error_message=str(media_task.error or "Scene Asset media transport failed."),
            error_details={
                "media_task_id": media_task.media_task_id,
                "media_status": media_task.status,
                "provider_error_code": media_task.provider_error_code,
                "error_detail": media_task.error_detail,
            },
        )

    def _mark_submit_unknown(
        self,
        session: Session,
        task: SceneAssetOperationTask,
        *,
        now_iso: str,
        details: dict[str, Any],
    ) -> None:
        if task.status == "submitting":
            SceneAssetWorkingStateService(session).mark_submit_outcome_unknown(
                operation_task_id=task.operation_task_id,
                now_iso=now_iso,
                details=details,
            )

    @staticmethod
    def _load_context(
        session: Session,
        operation_task_id: str,
    ) -> tuple[SceneAssetOperationTask, SceneAssetOperationBeat, SceneAssetWorkingOperation, SceneAssetBeatState]:
        task = session.get(SceneAssetOperationTask, operation_task_id)
        if task is None:
            raise AppError("SCENE_ASSET_V2_STATE_NOT_FOUND", "Scene Asset operation task was not found.", 404, retryable=False)
        beat = session.get(SceneAssetOperationBeat, task.operation_beat_id)
        if beat is None:
            raise AppError("SCENE_ASSET_V2_STATE_NOT_FOUND", "Scene Asset operation beat was not found.", 404, retryable=False)
        operation = session.get(SceneAssetWorkingOperation, beat.operation_id)
        if operation is None:
            raise AppError("SCENE_ASSET_V2_STATE_NOT_FOUND", "Scene Asset working operation was not found.", 404, retryable=False)
        beat_state = session.get(SceneAssetBeatState, (operation.project_id, beat.beat_id))
        if beat_state is None:
            raise AppError("SCENE_ASSET_V2_STATE_NOT_FOUND", "Scene Asset BeatState was not found.", 404, retryable=False)
        return task, beat, operation, beat_state

    @staticmethod
    def _validate_media_owner(
        media_task: MediaTask,
        task: SceneAssetOperationTask,
        beat: SceneAssetOperationBeat,
        operation: SceneAssetWorkingOperation,
    ) -> None:
        metadata = dict(media_task.metadata_json or {})
        if (
            media_task.purpose != PERSISTED_SCENE_ASSET_PURPOSE
            or media_task.project_id != operation.project_id
            or media_task.domain_revision_id != operation.operation_id
            or media_task.entity_type != "beat"
            or media_task.entity_id != beat.beat_id
            or metadata.get("scene_asset_v2") is not True
            or metadata.get("scene_asset_operation_task_id") != task.operation_task_id
            or metadata.get("scene_asset_operation_beat_id") != beat.operation_beat_id
            or metadata.get("candidate_index") != int(task.candidate_index)
            or metadata.get("prompt_revision_id") != beat.prompt_revision_id
            or metadata.get("prompt_bundle_artifact_id") != beat.prompt_bundle_artifact_id
            or metadata.get("prompt_item_id") != beat.prompt_item_id
            or metadata.get("prompt_hash") != beat.prompt_hash
            or metadata.get("reference_binding_snapshot_hash") != beat.reference_bindings_hash
            or metadata.get("generation_settings_snapshot_hash") != beat.generation_options_hash
            or metadata.get("provider_settings_snapshot_hash") != operation.provider_settings_snapshot_hash
        ):
            raise AppError(
                "SCENE_ASSET_V2_MEDIA_OWNERSHIP_INVALID",
                "MediaTask does not match the frozen Scene Asset V2 owner/lineage.",
                409,
                retryable=False,
                details={"operation_task_id": task.operation_task_id, "media_task_id": media_task.media_task_id},
            )

    def _assert_transport_owner_before_io(self, operation_task_id: str, media_task_id: str) -> None:
        with self.session_factory() as session:
            task, beat, operation, beat_state = self._load_context(session, operation_task_id)
            media_task = session.get(MediaTask, media_task_id)
            if media_task is None:
                raise AppError("SCENE_ASSET_V2_STATE_NOT_FOUND", "Scene Asset MediaTask was not found.", 409, retryable=True)
            self._validate_media_owner(media_task, task, beat, operation)
            if (
                task.media_task_id != media_task_id
                or task.status not in {"submitting", "waiting_provider", "downloading"}
                or operation.status not in ACTIVE_OPERATION_STATUSES
                or operation.cancel_requested_at is not None
                or beat.status not in {"queued", "running"}
                or beat_state.active_operation_beat_id != beat.operation_beat_id
            ):
                raise AppError(
                    "SCENE_ASSET_V2_BEAT_OWNERSHIP_LOST",
                    "Scene Asset provider I/O is no longer owned by the active beat operation.",
                    409,
                    retryable=True,
                    details={"operation_task_id": operation_task_id, "media_task_id": media_task_id},
                )

    def _submission_still_authorized(self, operation_task_id: str, media_task_id: str) -> bool:
        with self.session_factory() as session:
            task, beat, operation, state = self._load_context(session, operation_task_id)
            return (
                task.status == "submitting"
                and task.media_task_id == media_task_id
                and operation.status in ACTIVE_OPERATION_STATUSES
                and operation.cancel_requested_at is None
                and beat.status in {"queued", "running"}
                and state.active_operation_beat_id == beat.operation_beat_id
            )

    def _submission_still_authorized_or_waiting(self, operation_task_id: str, media_task_id: str) -> bool:
        with self.session_factory() as session:
            task, beat, operation, state = self._load_context(session, operation_task_id)
            return (
                task.status in {"submitting", "waiting_provider", "downloading"}
                and task.media_task_id == media_task_id
                and operation.status in ACTIVE_OPERATION_STATUSES
                and operation.cancel_requested_at is None
                and beat.status in {"queued", "running"}
                and state.active_operation_beat_id == beat.operation_beat_id
            )

    @staticmethod
    def _guarded_task_update(
        session: Session,
        operation_task_id: str,
        *,
        expected_statuses: tuple[str, ...],
        values: dict[str, Any],
    ) -> None:
        changed = session.execute(
            update(SceneAssetOperationTask)
            .where(
                SceneAssetOperationTask.operation_task_id == operation_task_id,
                SceneAssetOperationTask.status.in_(expected_statuses),
            )
            .values(**values)
        )
        if int(changed.rowcount or 0) != 1:
            raise AppError(
                "SCENE_ASSET_V2_OPERATION_TASK_CONFLICT",
                "Scene Asset operation task changed before transport reconciliation committed.",
                409,
                retryable=True,
                details={"operation_task_id": operation_task_id},
            )

    def _set_task_due(self, session: Session, task: SceneAssetOperationTask, due: str, *, now_iso: str) -> None:
        self._guarded_task_update(
            session,
            task.operation_task_id,
            expected_statuses=(str(task.status),),
            values={"next_reconcile_at": due, "updated_at": now_iso},
        )

    @staticmethod
    def _set_operation_due(session: Session, operation_id: str, due: str | None, *, now_iso: str) -> None:
        session.execute(
            update(SceneAssetWorkingOperation)
            .where(
                SceneAssetWorkingOperation.operation_id == operation_id,
                SceneAssetWorkingOperation.status.in_(tuple(ACTIVE_OPERATION_STATUSES)),
            )
            .values(next_reconcile_at=due, updated_at=now_iso)
        )

    def _recompute_operation_due(self, session: Session, operation_id: str, *, now_iso: str) -> None:
        operation = session.get(SceneAssetWorkingOperation, operation_id)
        if operation is None or operation.status not in ACTIVE_OPERATION_STATUSES:
            return
        beats = list(session.scalars(select(SceneAssetOperationBeat).where(SceneAssetOperationBeat.operation_id == operation_id)))
        beat_ids = [beat.operation_beat_id for beat in beats]
        if not beat_ids:
            self._set_operation_due(session, operation_id, None, now_iso=now_iso)
            return
        tasks = list(session.scalars(select(SceneAssetOperationTask).where(SceneAssetOperationTask.operation_beat_id.in_(beat_ids))))
        due_values: list[str] = []
        for task in tasks:
            if task.status in {"completed", "failed", "cancelled", "submit_outcome_unknown"}:
                continue
            if task.status == "queued" and task.next_reconcile_at:
                due_values.append(str(task.next_reconcile_at))
            elif task.status in {"queued", "downloading", "materializing"}:
                due_values.append(now_iso)
            elif task.next_reconcile_at:
                due_values.append(str(task.next_reconcile_at))
            else:
                due_values.append(now_iso)
        if operation.status == "finalizing":
            due_values.append(now_iso)
        due = min(due_values, key=_parse_iso) if due_values else None
        self._set_operation_due(session, operation_id, due, now_iso=now_iso)

    def _refresh_aggregates(
        self,
        session: Session,
        beat: SceneAssetOperationBeat,
        operation: SceneAssetWorkingOperation,
        *,
        now_iso: str,
    ) -> None:
        state = SceneAssetWorkingStateService(session)
        state.refresh_operation_beat(operation_beat_id=beat.operation_beat_id, now_iso=now_iso)
        state.refresh_operation(operation_id=operation.operation_id, now_iso=now_iso)

    def _poll_interval(self, session: Session, operation: SceneAssetWorkingOperation) -> int:
        value = self._frozen_provider_settings(session, operation).get("poll_interval_seconds")
        if type(value) is not int or value <= 0:
            raise AppError(
                "SCENE_ASSET_V2_PROVIDER_SNAPSHOT_INVALID",
                "Frozen Scene Asset poll interval is invalid.",
                409,
                retryable=False,
            )
        return value

    def _cancel_queued_transport(self, media_task_id: str, *, now_iso: str) -> None:
        with self.session_factory() as session:
            media_task = session.get(MediaTask, media_task_id)
            if media_task is None or media_task.status != "queued":
                session.rollback()
                return
            media_task.status = "canceled"
            media_task.completed_at = now_iso
            media_task.updated_at = now_iso
            session.commit()

    def _recover_unbridged_submission(self, operation_task_id: str, *, now_iso: str) -> SceneAssetProviderStepResult:
        with self.session_factory() as session:
            task, beat, operation, _ = self._load_context(session, operation_task_id)
            if task.status == "submitting":
                self._mark_submit_unknown(
                    session,
                    task,
                    now_iso=now_iso,
                    details={"reason": "submission_has_no_transport_identity"},
                )
                self._refresh_aggregates(session, beat, operation, now_iso=now_iso)
                self._recompute_operation_due(session, operation.operation_id, now_iso=now_iso)
                session.commit()
        return self._snapshot_result(operation_task_id, candidate_id=None)

    def _new_media_service(self) -> MediaTaskService:
        session = self.session_factory()
        try:
            return self.media_service_factory(session)
        except Exception:
            session.close()
            raise

    def _snapshot_result(self, operation_task_id: str, *, candidate_id: str | None) -> SceneAssetProviderStepResult:
        with self.session_factory() as session:
            task, _, operation, _ = self._load_context(session, operation_task_id)
            media_task = session.get(MediaTask, task.media_task_id) if task.media_task_id else None
            return SceneAssetProviderStepResult(
                operation_id=operation.operation_id,
                operation_task_id=task.operation_task_id,
                media_task_id=task.media_task_id,
                media_status=str(media_task.status) if media_task is not None else None,
                v2_task_status=str(task.status),
                operation_status=str(operation.status),
                candidate_id=candidate_id,
            )

    @staticmethod
    def _frozen_input_error(beat: SceneAssetOperationBeat, label: str) -> AppError:
        return AppError(
            "SCENE_ASSET_V2_FROZEN_INPUT_INVALID",
            f"Frozen Scene Asset {label} failed integrity validation.",
            409,
            retryable=False,
            details={"operation_beat_id": beat.operation_beat_id},
        )
