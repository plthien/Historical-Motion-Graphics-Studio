from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.errors import AppError
from app.core.ids import generate_id
from app.core.time import utc_now_iso
from app.db.enums import ArtifactStatus, ArtifactType
from app.db.models import Artifact, MediaBatch, MediaTask
from app.db.visual_reference_anchor_state import VisualReferenceAnchorState
from app.db.visual_reference_candidate import VisualReferenceCandidate
from app.db.visual_reference_operation_anchor import VisualReferenceOperationAnchor
from app.db.visual_reference_operation_task import VisualReferenceOperationTask
from app.db.visual_reference_prompt_revision import VisualReferencePromptRevision
from app.db.visual_reference_working_operation import VisualReferenceWorkingOperation
from app.modules.media.media_task_service import (
    ALLOWED_ASPECT_RATIOS,
    ALLOWED_MODELS,
    ALLOWED_UPSCALES,
    PERSISTED_STYLE_BIBLE_VISUAL_REFERENCE_PURPOSE,
    MediaTaskService,
    NormalizedImageGenerationCommand,
)
from app.modules.v4.scene_asset_manifest import canonical_hash
from app.modules.v4.visual_reference_coordinator import VisualReferenceCoordinator
from app.modules.v4.visual_reference_working_state import VisualReferenceWorkingStateService
from app.repositories import SettingsRepository
from app.repositories.visual_reference_working import ACTIVE_OPERATION_STATUSES, VisualReferenceReconcileLease
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
    "enabled",
    "base_url",
    "api_key_configured",
    "default_image_model",
    "default_image_aspect_ratio",
    "default_upscale",
    "poll_interval_seconds",
    "max_parallel_tasks",
    "enabled_media_kinds",
})
_RETIRED_PROVIDER_SNAPSHOT_FIELDS = frozenset({
    "supported_image_aspect_ratios",
    "max_poll_attempts",
})


@dataclass(frozen=True, slots=True)
class VisualReferenceProviderStepResult:
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
            "VISUAL_REFERENCE_V2_TIMESTAMP_INVALID",
            "Visual Reference V2 timestamp is invalid.",
            409,
            retryable=False,
        ) from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _plus_seconds(value: str, seconds: int) -> str:
    return (_parse_iso(value) + timedelta(seconds=seconds)).isoformat()


class VisualReferenceProviderExecutionService:
    """Visual Reference V2 domain bridge behind the shared provider executor.

    This class owns Visual Reference lineage/ownership validation, atomic media
    transport staging, task reconciliation, and immutable candidate
    materialization. It intentionally does not own the submit/poll sequencing
    envelope; that is shared by DurableMediaProviderExecutor.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        media_service_factory: Callable[[Session], MediaTaskService] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.coordinator = VisualReferenceCoordinator(session_factory)
        self.media_service_factory = media_service_factory or (lambda session: MediaTaskService(session))

    def _preflight(self, operation_task_id: str) -> None:
        with self.session_factory() as session:
            task, unit, operation, state, prompt_revision = self._load_context(session, operation_task_id)
            if (
                task.status != "queued"
                or operation.status not in ACTIVE_OPERATION_STATUSES
                or operation.cancel_requested_at is not None
                or unit.status not in {"queued", "running"}
                or state.active_operation_anchor_id != unit.operation_anchor_id
            ):
                raise AppError(
                    "VISUAL_REFERENCE_V2_SUBMISSION_NOT_ACTIVE",
                    "Visual Reference task is not eligible for provider submission.",
                    409,
                    retryable=True,
                    details={"operation_task_id": operation_task_id, "status": task.status},
                )
            settings = self._frozen_provider_settings(session, operation)
            self._build_command(operation, unit, task, prompt_revision, settings)

    def _ensure_transport_task(
        self,
        operation_task_id: str,
        *,
        now_iso: str,
    ) -> tuple[str, str, dict[str, Any]]:
        """Atomically stage MediaBatch + MediaTask + durable owner link."""
        with self.session_factory() as session:
            task, unit, operation, _, prompt_revision = self._load_context(session, operation_task_id)
            if task.status != "submitting":
                raise AppError(
                    "VISUAL_REFERENCE_V2_SUBMISSION_NOT_ACTIVE",
                    "Visual Reference task is not in submitting state.",
                    409,
                    retryable=True,
                    details={"operation_task_id": operation_task_id, "status": task.status},
                )
            settings = self._frozen_provider_settings(session, operation)
            if task.media_task_id:
                media_task = session.get(MediaTask, task.media_task_id)
                if media_task is None:
                    raise AppError(
                        "VISUAL_REFERENCE_V2_STATE_INTEGRITY_INVALID",
                        "Visual Reference operation task references a missing MediaTask.",
                        409,
                        retryable=False,
                        details={"operation_task_id": operation_task_id, "media_task_id": task.media_task_id},
                    )
                self._validate_media_owner(media_task, task, unit, operation, prompt_revision)
                return str(media_task.media_task_id), str(media_task.batch_id), settings

            command = self._build_command(operation, unit, task, prompt_revision, settings)
            self._validate_command(session, command, settings)
            batch_id = generate_id("media_batch")
            media_task_id = generate_id("media_task")
            batch = MediaBatch(
                batch_id=batch_id,
                purpose=PERSISTED_STYLE_BIBLE_VISUAL_REFERENCE_PURPOSE,
                project_id=operation.project_id,
                style_bible_revision_id=operation.source_style_bible_artifact_id,
                domain_revision_id=operation.operation_id,
                total=1,
                created_at=now_iso,
                updated_at=now_iso,
            )
            media_task = MediaTask(
                media_task_id=media_task_id,
                batch_id=batch_id,
                purpose=PERSISTED_STYLE_BIBLE_VISUAL_REFERENCE_PURPOSE,
                project_id=operation.project_id,
                style_bible_revision_id=operation.source_style_bible_artifact_id,
                domain_revision_id=operation.operation_id,
                entity_type=unit.anchor_type,
                entity_id=unit.anchor_id,
                candidate_index=int(task.candidate_index),
                status="queued",
                external_provider=str(settings["provider"]),
                external_task_id=None,
                provider_status=None,
                provider_submitted_at=None,
                provider_error_code=None,
                error_code=None,
                error=None,
                error_detail=None,
                prompt=unit.prompt,
                negative_prompt=unit.negative_prompt,
                model=command.model,
                aspect_ratio=command.aspect_ratio,
                upscale_json=list(command.upscale),
                reference_images_json=[],
                storage_namespace=command.storage_namespace,
                metadata_json=dict(command.metadata),
                result_json=[],
                storage_uri=None,
                mime_type=None,
                source_result_url=None,
                selected=False,
                locked=False,
                created_at=now_iso,
                updated_at=now_iso,
                completed_at=None,
                poll_count=0,
                poll_revision=0,
                poll_claim_token=None,
                poll_claimed_at=None,
                poll_claim_expires_at=None,
            )
            session.add(batch)
            session.add(media_task)
            task.media_task_id = media_task_id
            task.updated_at = now_iso
            session.flush()
            self._validate_media_owner(media_task, task, unit, operation, prompt_revision)
            session.commit()
            return media_task_id, batch_id, settings

    def reconcile_task(
        self,
        operation_task_id: str,
        *,
        now_iso: str | None = None,
    ) -> VisualReferenceProviderStepResult:
        now = now_iso or utc_now_iso()
        candidate_id: str | None = None
        with self.session_factory() as session:
            task, unit, operation, _, prompt_revision = self._load_context(session, operation_task_id)
            if operation.status not in ACTIVE_OPERATION_STATUSES or operation.cancel_requested_at is not None:
                session.rollback()
                return self._snapshot_result(operation_task_id, candidate_id=None)
            media_task = session.get(MediaTask, task.media_task_id) if task.media_task_id else None
            if media_task is None:
                if task.status == "submitting":
                    state = VisualReferenceWorkingStateService(session)
                    state.mark_submit_outcome_unknown(
                        operation_task_id=task.operation_task_id,
                        now_iso=now,
                        details={"reason": "media_task_missing_after_submit_intent"},
                    )
                    self._refresh_aggregates(session, unit, operation, now_iso=now)
                    session.commit()
                return self._snapshot_result(operation_task_id, candidate_id=None)

            self._validate_media_owner(media_task, task, unit, operation, prompt_revision)
            media_status = str(media_task.status)
            state = VisualReferenceWorkingStateService(session)
            poll_interval = self._poll_interval(session, operation)

            if media_status == "queued":
                if media_task.external_task_id or media_task.provider_submitted_at:
                    raise AppError(
                        "VISUAL_REFERENCE_V2_MEDIA_STATE_INVALID",
                        "Queued Visual Reference transport unexpectedly has provider identity.",
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
                    state.mark_submit_outcome_unknown(
                        operation_task_id=task.operation_task_id,
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
                    unit=unit,
                    operation=operation,
                    media_task=media_task,
                    now_iso=now,
                )
            elif media_status in _MEDIA_FAILURE_STATUSES:
                if not media_task.external_task_id and task.status == "submitting":
                    state.mark_submit_outcome_unknown(
                        operation_task_id=task.operation_task_id,
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
                    state._transition_task(
                        task.operation_task_id,
                        "cancelled",
                        now_iso=now,
                        error_code="VISUAL_REFERENCE_OPERATION_CANCELLED",
                        error_message="Visual Reference transport task was cancelled locally.",
                        error_details_json={"media_task_id": media_task.media_task_id},
                    )
            else:
                raise AppError(
                    "VISUAL_REFERENCE_V2_MEDIA_STATE_INVALID",
                    "Visual Reference transport task has an unsupported status.",
                    409,
                    retryable=False,
                    details={"media_task_id": media_task.media_task_id, "status": media_status},
                )

            session.flush()
            session.expire_all()
            task = session.get(VisualReferenceOperationTask, operation_task_id)
            unit = session.get(VisualReferenceOperationAnchor, unit.operation_anchor_id)
            operation = session.get(VisualReferenceWorkingOperation, operation.operation_id)
            assert task is not None and unit is not None and operation is not None
            self._refresh_aggregates(session, unit, operation, now_iso=now)
            self._recompute_operation_due(session, operation.operation_id, now_iso=now)
            session.commit()
        return self._snapshot_result(operation_task_id, candidate_id=candidate_id)

    def release_terminal_v2_provider_capacity(
        self,
        *,
        provider: str | None = None,
        now_iso: str | None = None,
    ) -> int:
        now = now_iso or utc_now_iso()
        released = 0
        with self.session_factory() as session:
            stmt = select(MediaTask).where(MediaTask.status.in_(tuple(sorted(_PROVIDER_CAPACITY_STATUSES))))
            if provider is not None:
                stmt = stmt.where(MediaTask.external_provider == provider)
            for media_task in session.scalars(stmt):
                metadata = dict(media_task.metadata_json or {})
                if metadata.get("visual_reference_v2") is not True:
                    continue
                operation_task_id = metadata.get("visual_reference_operation_task_id")
                task = session.get(VisualReferenceOperationTask, str(operation_task_id)) if operation_task_id else None
                unit = session.get(VisualReferenceOperationAnchor, task.operation_anchor_id) if task is not None else None
                operation = session.get(VisualReferenceWorkingOperation, unit.operation_id) if unit is not None else None
                state = (
                    session.get(VisualReferenceAnchorState, (unit.project_id, unit.anchor_type, unit.anchor_id))
                    if unit is not None
                    else None
                )
                owner_active = bool(
                    task is not None
                    and unit is not None
                    and operation is not None
                    and state is not None
                    and task.media_task_id == media_task.media_task_id
                    and task.status in {"submitting", "waiting_provider", "downloading", "materializing"}
                    and operation.status in ACTIVE_OPERATION_STATUSES
                    and operation.cancel_requested_at is None
                    and unit.status in {"queued", "running"}
                    and state.current_prompt_revision_id == unit.prompt_revision_id
                    and state.active_operation_anchor_id == unit.operation_anchor_id
                )
                if owner_active:
                    continue
                media_task.status = "canceled"
                media_task.provider_status = None
                media_task.error_code = "VISUAL_REFERENCE_V2_MEDIA_OWNER_TERMINAL"
                media_task.error = "Visual Reference media task owner is no longer active."
                media_task.error_detail = (
                    "Visual Reference V2 released this local provider-capacity slot after its durable owner "
                    "became terminal or disappeared. Any late remote result remains fenced."
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

    def _build_command(
        self,
        operation: VisualReferenceWorkingOperation,
        unit: VisualReferenceOperationAnchor,
        task: VisualReferenceOperationTask,
        prompt_revision: VisualReferencePromptRevision,
        provider_settings: dict[str, Any],
    ) -> NormalizedImageGenerationCommand:
        if canonical_hash(unit.generation_options_json) != unit.generation_options_hash:
            raise self._frozen_input_error(unit, "generation options")
        if unit.prompt_revision_id != prompt_revision.prompt_revision_id:
            raise self._frozen_input_error(unit, "prompt revision")
        if unit.prompt != prompt_revision.prompt or unit.negative_prompt != prompt_revision.negative_prompt:
            raise self._frozen_input_error(unit, "prompt text")
        if unit.source_definition_hash != prompt_revision.source_definition_hash:
            raise self._frozen_input_error(unit, "source definition")
        aspect_ratio = unit.generation_options_json.get("aspect_ratio")
        if not isinstance(aspect_ratio, str) or aspect_ratio not in ALLOWED_ASPECT_RATIOS:
            raise self._frozen_input_error(unit, "aspect ratio")
        model = str(provider_settings["default_image_model"])
        upscale = tuple(str(item) for item in provider_settings.get("default_upscale", []))
        if model not in ALLOWED_MODELS or upscale not in ALLOWED_UPSCALES:
            raise self._frozen_input_error(unit, "provider image settings")
        metadata = {
            "visual_reference_v2": True,
            "visual_reference_operation_id": operation.operation_id,
            "visual_reference_operation_anchor_id": unit.operation_anchor_id,
            "visual_reference_operation_task_id": task.operation_task_id,
            "anchor_type": unit.anchor_type,
            "anchor_id": unit.anchor_id,
            "candidate_index": int(task.candidate_index),
            "prompt_revision_id": unit.prompt_revision_id,
            "source_definition_hash": unit.source_definition_hash,
            "generation_options_hash": unit.generation_options_hash,
            "provider_settings_snapshot_hash": operation.provider_settings_snapshot_hash,
            "source_style_bible_artifact_id": operation.source_style_bible_artifact_id,
        }
        return NormalizedImageGenerationCommand(
            purpose=PERSISTED_STYLE_BIBLE_VISUAL_REFERENCE_PURPOSE,
            project_id=operation.project_id,
            domain_revision_id=operation.operation_id,
            style_bible_revision_id=operation.source_style_bible_artifact_id,
            entity_type=unit.anchor_type,
            entity_id=unit.anchor_id,
            candidate_count=1,
            prompt=unit.prompt,
            negative_prompt=unit.negative_prompt,
            model=model,
            aspect_ratio=aspect_ratio,
            upscale=upscale,
            reference_images=tuple(),
            storage_namespace=(
                f"project/{operation.project_id}/visual_references/"
                f"{unit.prompt_revision_id}/{unit.anchor_type}/{unit.anchor_id}"
            ),
            metadata=metadata,
        )

    def _validate_command(
        self,
        session: Session,
        command: NormalizedImageGenerationCommand,
        provider_settings: dict[str, Any],
    ) -> None:
        media = self.media_service_factory(session)
        request = {
            "project_id": command.project_id,
            "entity_id": command.entity_id,
            "prompt": command.prompt,
            "negative_prompt": command.negative_prompt,
            "model": command.model,
            "aspect_ratio": command.aspect_ratio,
            "upscale": list(command.upscale),
            "style_bible_revision_id": command.style_bible_revision_id,
        }
        media._validate_request_segments(request)
        media._validate_storage_namespace(
            command.storage_namespace or "",
            project_id=command.project_id,
            purpose=command.purpose,
        )
        media._validate_image_request(request, provider_settings)
        media.media_settings._validate_runtime_ready(provider_settings)
        media._validate_reference_image_descriptors(
            command.reference_images,
            project_id=command.project_id,
            allow_prev_beat=False,
        )

    def _frozen_provider_settings(
        self,
        session: Session,
        operation: VisualReferenceWorkingOperation,
    ) -> dict[str, Any]:
        frozen = dict(operation.provider_settings_snapshot_json or {})
        if canonical_hash(frozen) != operation.provider_settings_snapshot_hash:
            raise AppError(
                "VISUAL_REFERENCE_V2_PROVIDER_SNAPSHOT_INVALID",
                "Frozen Visual Reference provider settings failed integrity validation.",
                409,
                retryable=False,
                details={"operation_id": operation.operation_id},
            )
        missing = sorted(_PROVIDER_SNAPSHOT_REQUIRED - set(frozen))
        if missing or "api_key" in frozen:
            raise AppError(
                "VISUAL_REFERENCE_V2_PROVIDER_SNAPSHOT_INVALID",
                "Frozen Visual Reference provider settings are incomplete or contain credentials.",
                409,
                retryable=False,
                details={"operation_id": operation.operation_id, "missing_fields": missing},
            )
        unknown = sorted(set(frozen) - _PROVIDER_SNAPSHOT_FIELDS - _RETIRED_PROVIDER_SNAPSHOT_FIELDS)
        if unknown:
            raise AppError(
                "VISUAL_REFERENCE_V2_PROVIDER_SNAPSHOT_INVALID",
                "Frozen Visual Reference provider settings contain unsupported fields.",
                409,
                retryable=False,
                details={"operation_id": operation.operation_id, "unknown_fields": unknown},
            )
        runtime_frozen = {key: value for key, value in frozen.items() if key in _PROVIDER_SNAPSHOT_FIELDS}
        settings_service = MediaProviderSettingsService(session, SettingsRepository(session))
        current = settings_service.get_settings_for_backend()
        if str(runtime_frozen["provider"]) != str(current.get("provider", "")):
            raise AppError(
                "VISUAL_REFERENCE_V2_PROVIDER_SNAPSHOT_INVALID",
                "Current provider identity does not match the frozen Visual Reference provider.",
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
        state: VisualReferenceWorkingStateService,
        task: VisualReferenceOperationTask,
        unit: VisualReferenceOperationAnchor,
        operation: VisualReferenceWorkingOperation,
        media_task: MediaTask,
        now_iso: str,
    ) -> str:
        self._advance_to_materializing(state, task, media_task, now_iso=now_iso)
        session.flush()
        session.expire_all()
        task = session.get(VisualReferenceOperationTask, task.operation_task_id)
        if task is None:
            raise AppError("VISUAL_REFERENCE_V2_STATE_NOT_FOUND", "Visual Reference operation task disappeared.", 409, retryable=True)
        existing = state.repo.get_candidate_for_operation_task(task.operation_task_id)
        if existing is not None:
            return existing.candidate_id
        fence = state.assert_task_can_materialize(operation_task_id=task.operation_task_id)
        results = [item for item in (media_task.result_json or []) if isinstance(item, dict)]
        if len(results) != 1 or not isinstance(results[0].get("artifact_id"), str):
            raise AppError(
                "VISUAL_REFERENCE_V2_MEDIA_RESULT_INVALID",
                "Visual Reference V2 requires exactly one downloaded image Artifact per operation task.",
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
            or not artifact.logical_id.startswith(
                f"project/{operation.project_id}/visual_references/"
            )
            or not artifact.sha256
            or not artifact.mime_type
        ):
            raise AppError(
                "VISUAL_REFERENCE_V2_MEDIA_RESULT_INVALID",
                "Downloaded Visual Reference Artifact failed ownership/integrity validation.",
                409,
                retryable=False,
                details={"media_task_id": media_task.media_task_id, "artifact_id": artifact_id},
            )
        candidate = VisualReferenceCandidate(
            candidate_id=generate_id("visual_reference_candidate"),
            project_id=operation.project_id,
            anchor_type=unit.anchor_type,
            anchor_id=unit.anchor_id,
            candidate_index=int(task.candidate_index),
            source_prompt_revision_id=unit.prompt_revision_id,
            source_operation_task_id=task.operation_task_id,
            media_artifact_id=artifact_id,
            media_task_id=media_task.media_task_id,
            mime_type=str(artifact.mime_type),
            media_sha256=str(artifact.sha256),
            provenance_json={
                "source_kind": "generated",
                "operation_id": operation.operation_id,
                "operation_anchor_id": unit.operation_anchor_id,
                "operation_task_id": task.operation_task_id,
                "provider_settings_snapshot_hash": operation.provider_settings_snapshot_hash,
                "source_definition_hash": unit.source_definition_hash,
                "source_style_bible_artifact_id": operation.source_style_bible_artifact_id,
            },
            created_at=now_iso,
        )
        session.add(candidate)
        session.flush()

        current_state = state.repo.get_anchor_state(operation.project_id, unit.anchor_type, unit.anchor_id)
        if current_state is None:
            raise AppError("VISUAL_REFERENCE_V2_STATE_NOT_FOUND", "Visual Reference AnchorState disappeared.", 409, retryable=True)
        selected = (
            state.repo.get_candidate(current_state.selected_candidate_id)
            if current_state.selected_candidate_id
            else None
        )
        should_select = (
            selected is None
            or selected.source_prompt_revision_id != unit.prompt_revision_id
            or int(candidate.candidate_index) < int(selected.candidate_index)
        )
        values: dict[str, Any] = {"last_progress_at": now_iso, "last_error_json": None}
        if should_select:
            values.update(
                resolution_status="candidate_ready",
                review_status="draft_selected",
                selected_candidate_id=candidate.candidate_id,
                approved_candidate_id=None,
            )
        state.compare_and_set_anchor_state(
            project_id=fence.project_id,
            anchor_type=fence.anchor_type,
            anchor_id=fence.anchor_id,
            expected_version=fence.anchor_state_version,
            values=values,
            now_iso=now_iso,
        )
        state.mark_task_completed(operation_task_id=task.operation_task_id, now_iso=now_iso)
        return candidate.candidate_id

    def _advance_to_waiting(
        self,
        state: VisualReferenceWorkingStateService,
        task: VisualReferenceOperationTask,
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
        state: VisualReferenceWorkingStateService,
        task: VisualReferenceOperationTask,
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
        state: VisualReferenceWorkingStateService,
        task: VisualReferenceOperationTask,
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
        state: VisualReferenceWorkingStateService,
        task: VisualReferenceOperationTask,
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
            error_message=str(media_task.error or "Visual Reference media transport failed."),
            error_details={
                "media_task_id": media_task.media_task_id,
                "media_status": media_task.status,
                "provider_error_code": media_task.provider_error_code,
                "error_detail": media_task.error_detail,
            },
        )

    @staticmethod
    def _load_context(
        session: Session,
        operation_task_id: str,
    ) -> tuple[
        VisualReferenceOperationTask,
        VisualReferenceOperationAnchor,
        VisualReferenceWorkingOperation,
        VisualReferenceAnchorState,
        VisualReferencePromptRevision,
    ]:
        task = session.get(VisualReferenceOperationTask, operation_task_id)
        if task is None:
            raise AppError("VISUAL_REFERENCE_V2_STATE_NOT_FOUND", "Visual Reference operation task was not found.", 404, retryable=False)
        unit = session.get(VisualReferenceOperationAnchor, task.operation_anchor_id)
        if unit is None:
            raise AppError("VISUAL_REFERENCE_V2_STATE_NOT_FOUND", "Visual Reference operation anchor was not found.", 404, retryable=False)
        operation = session.get(VisualReferenceWorkingOperation, unit.operation_id)
        if operation is None:
            raise AppError("VISUAL_REFERENCE_V2_STATE_NOT_FOUND", "Visual Reference working operation was not found.", 404, retryable=False)
        state = session.get(VisualReferenceAnchorState, (unit.project_id, unit.anchor_type, unit.anchor_id))
        if state is None:
            raise AppError("VISUAL_REFERENCE_V2_STATE_NOT_FOUND", "Visual Reference AnchorState was not found.", 404, retryable=False)
        prompt_revision = session.get(VisualReferencePromptRevision, unit.prompt_revision_id)
        if prompt_revision is None:
            raise AppError("VISUAL_REFERENCE_V2_STATE_NOT_FOUND", "Visual Reference PromptRevision was not found.", 404, retryable=False)
        if (
            prompt_revision.project_id != unit.project_id
            or prompt_revision.anchor_type != unit.anchor_type
            or prompt_revision.anchor_id != unit.anchor_id
            or prompt_revision.source_style_bible_artifact_id != operation.source_style_bible_artifact_id
            or prompt_revision.source_definition_hash != unit.source_definition_hash
            or state.current_prompt_revision_id != unit.prompt_revision_id
        ):
            raise AppError(
                "VISUAL_REFERENCE_V2_STATE_INTEGRITY_INVALID",
                "Visual Reference frozen prompt/anchor ownership is inconsistent.",
                409,
                retryable=False,
                details={"operation_task_id": operation_task_id, "operation_anchor_id": unit.operation_anchor_id},
            )
        return task, unit, operation, state, prompt_revision

    @staticmethod
    def _validate_media_owner(
        media_task: MediaTask,
        task: VisualReferenceOperationTask,
        unit: VisualReferenceOperationAnchor,
        operation: VisualReferenceWorkingOperation,
        prompt_revision: VisualReferencePromptRevision,
    ) -> None:
        metadata = dict(media_task.metadata_json or {})
        if (
            media_task.purpose != PERSISTED_STYLE_BIBLE_VISUAL_REFERENCE_PURPOSE
            or media_task.project_id != operation.project_id
            or media_task.style_bible_revision_id != operation.source_style_bible_artifact_id
            or media_task.domain_revision_id != operation.operation_id
            or media_task.entity_type != unit.anchor_type
            or media_task.entity_id != unit.anchor_id
            or int(media_task.candidate_index) != int(task.candidate_index)
            or metadata.get("visual_reference_v2") is not True
            or metadata.get("visual_reference_operation_id") != operation.operation_id
            or metadata.get("visual_reference_operation_anchor_id") != unit.operation_anchor_id
            or metadata.get("visual_reference_operation_task_id") != task.operation_task_id
            or metadata.get("anchor_type") != unit.anchor_type
            or metadata.get("anchor_id") != unit.anchor_id
            or metadata.get("candidate_index") != int(task.candidate_index)
            or metadata.get("prompt_revision_id") != prompt_revision.prompt_revision_id
            or metadata.get("source_definition_hash") != unit.source_definition_hash
            or metadata.get("generation_options_hash") != unit.generation_options_hash
            or metadata.get("provider_settings_snapshot_hash") != operation.provider_settings_snapshot_hash
            or metadata.get("source_style_bible_artifact_id") != operation.source_style_bible_artifact_id
        ):
            raise AppError(
                "VISUAL_REFERENCE_V2_MEDIA_OWNERSHIP_INVALID",
                "MediaTask does not match the frozen Visual Reference V2 owner/lineage.",
                409,
                retryable=False,
                details={"operation_task_id": task.operation_task_id, "media_task_id": media_task.media_task_id},
            )

    def _assert_transport_owner_before_io(self, operation_task_id: str, media_task_id: str) -> None:
        with self.session_factory() as session:
            task, unit, operation, state, prompt_revision = self._load_context(session, operation_task_id)
            media_task = session.get(MediaTask, media_task_id)
            if media_task is None:
                raise AppError("VISUAL_REFERENCE_V2_STATE_NOT_FOUND", "Visual Reference MediaTask was not found.", 409, retryable=True)
            self._validate_media_owner(media_task, task, unit, operation, prompt_revision)
            if (
                task.media_task_id != media_task_id
                or task.status not in {"submitting", "waiting_provider", "downloading"}
                or operation.status not in ACTIVE_OPERATION_STATUSES
                or operation.cancel_requested_at is not None
                or unit.status not in {"queued", "running"}
                or state.active_operation_anchor_id != unit.operation_anchor_id
            ):
                raise AppError(
                    "VISUAL_REFERENCE_V2_ANCHOR_OWNERSHIP_LOST",
                    "Visual Reference provider I/O is no longer owned by the active anchor operation.",
                    409,
                    retryable=True,
                    details={"operation_task_id": operation_task_id, "media_task_id": media_task_id},
                )

    def _submission_still_authorized(self, operation_task_id: str, media_task_id: str) -> bool:
        with self.session_factory() as session:
            task, unit, operation, state, _ = self._load_context(session, operation_task_id)
            return (
                task.status == "submitting"
                and task.media_task_id == media_task_id
                and operation.status in ACTIVE_OPERATION_STATUSES
                and operation.cancel_requested_at is None
                and unit.status in {"queued", "running"}
                and state.active_operation_anchor_id == unit.operation_anchor_id
            )

    def _submission_still_authorized_or_waiting(self, operation_task_id: str, media_task_id: str) -> bool:
        with self.session_factory() as session:
            task, unit, operation, state, _ = self._load_context(session, operation_task_id)
            return (
                task.status in {"submitting", "waiting_provider", "downloading"}
                and task.media_task_id == media_task_id
                and operation.status in ACTIVE_OPERATION_STATUSES
                and operation.cancel_requested_at is None
                and unit.status in {"queued", "running"}
                and state.active_operation_anchor_id == unit.operation_anchor_id
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
            update(VisualReferenceOperationTask)
            .where(
                VisualReferenceOperationTask.operation_task_id == operation_task_id,
                VisualReferenceOperationTask.status.in_(expected_statuses),
            )
            .values(**values)
        )
        if int(changed.rowcount or 0) != 1:
            raise AppError(
                "VISUAL_REFERENCE_V2_OPERATION_TASK_CONFLICT",
                "Visual Reference operation task changed before transport reconciliation committed.",
                409,
                retryable=True,
                details={"operation_task_id": operation_task_id},
            )

    def _set_task_due(self, session: Session, task: VisualReferenceOperationTask, due: str, *, now_iso: str) -> None:
        self._guarded_task_update(
            session,
            task.operation_task_id,
            expected_statuses=(str(task.status),),
            values={"next_reconcile_at": due, "updated_at": now_iso},
        )

    @staticmethod
    def _set_operation_due(session: Session, operation_id: str, due: str | None, *, now_iso: str) -> None:
        session.execute(
            update(VisualReferenceWorkingOperation)
            .where(
                VisualReferenceWorkingOperation.operation_id == operation_id,
                VisualReferenceWorkingOperation.status.in_(tuple(ACTIVE_OPERATION_STATUSES)),
            )
            .values(next_reconcile_at=due, updated_at=now_iso)
        )

    def _recompute_operation_due(self, session: Session, operation_id: str, *, now_iso: str) -> None:
        operation = session.get(VisualReferenceWorkingOperation, operation_id)
        if operation is None or operation.status not in ACTIVE_OPERATION_STATUSES:
            return
        units = list(session.scalars(select(VisualReferenceOperationAnchor).where(VisualReferenceOperationAnchor.operation_id == operation_id)))
        unit_ids = [unit.operation_anchor_id for unit in units]
        if not unit_ids:
            self._set_operation_due(session, operation_id, None, now_iso=now_iso)
            return
        tasks = list(session.scalars(select(VisualReferenceOperationTask).where(VisualReferenceOperationTask.operation_anchor_id.in_(unit_ids))))
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
        unit: VisualReferenceOperationAnchor,
        operation: VisualReferenceWorkingOperation,
        *,
        now_iso: str,
    ) -> None:
        state = VisualReferenceWorkingStateService(session)
        state.refresh_operation_anchor(operation_anchor_id=unit.operation_anchor_id, now_iso=now_iso)
        state.refresh_operation(operation_id=operation.operation_id, now_iso=now_iso)

    def _poll_interval(self, session: Session, operation: VisualReferenceWorkingOperation) -> int:
        value = self._frozen_provider_settings(session, operation).get("poll_interval_seconds")
        if type(value) is not int or value <= 0:
            raise AppError(
                "VISUAL_REFERENCE_V2_PROVIDER_SNAPSHOT_INVALID",
                "Frozen Visual Reference poll interval is invalid.",
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

    def _recover_unbridged_submission(self, operation_task_id: str, *, now_iso: str) -> VisualReferenceProviderStepResult:
        with self.session_factory() as session:
            task, unit, operation, _, _ = self._load_context(session, operation_task_id)
            if task.status == "submitting":
                state = VisualReferenceWorkingStateService(session)
                state.mark_submit_outcome_unknown(
                    operation_task_id=task.operation_task_id,
                    now_iso=now_iso,
                    details={"reason": "submission_has_no_transport_identity"},
                )
                self._refresh_aggregates(session, unit, operation, now_iso=now_iso)
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

    def _snapshot_result(self, operation_task_id: str, *, candidate_id: str | None) -> VisualReferenceProviderStepResult:
        with self.session_factory() as session:
            task, _, operation, _, _ = self._load_context(session, operation_task_id)
            media_task = session.get(MediaTask, task.media_task_id) if task.media_task_id else None
            return VisualReferenceProviderStepResult(
                operation_id=operation.operation_id,
                operation_task_id=task.operation_task_id,
                media_task_id=task.media_task_id,
                media_status=str(media_task.status) if media_task is not None else None,
                v2_task_status=str(task.status),
                operation_status=str(operation.status),
                candidate_id=candidate_id,
            )

    @staticmethod
    def _frozen_input_error(unit: VisualReferenceOperationAnchor, label: str) -> AppError:
        return AppError(
            "VISUAL_REFERENCE_V2_FROZEN_INPUT_INVALID",
            f"Frozen Visual Reference {label} failed integrity validation.",
            409,
            retryable=False,
            details={"operation_anchor_id": unit.operation_anchor_id},
        )
