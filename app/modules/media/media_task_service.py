from __future__ import annotations

import base64
import json
import logging
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4
from urllib.parse import unquote

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.errors import AppError
from app.core.config import get_settings
from app.core.diagnostic_redaction import sanitize_diagnostic_value
from app.core.ids import generate_id
from app.core.time import utc_now_iso
from app.db.enums import ArtifactStatus, ArtifactType
from app.db.models import Artifact, MediaBatch, MediaTask
from app.db.session import build_session_factory
from app.modules.media.bounded_http_transport import BoundedHttpTransport
from app.modules.media.glabs_webhook_client import DownloadMetadata, GLabsWebhookClient
from app.modules.media.transport_policy import DEFAULT_MEDIA_TRANSPORT_POLICY, MediaTransportPolicy
from app.modules.media.media_provider_errors import normalize_provider_failure
from app.modules.media.media_secret_safety import normalize_provider_error_code, sanitize_media_provider_value
from app.modules.platform.contracts import PlatformContractValidator
from app.repositories import ArtifactRepository, DownloadOwnerSnapshot, MediaBatchRepository, MediaTaskRepository, ProjectRepository, SettingsRepository
from app.services.media_settings import MediaProviderSettingsService
from app.services.artifacts import ArtifactService

ALLOWED_MODELS = {"nano_banana_2", "nano_banana_pro"}
ALLOWED_ASPECT_RATIOS = {"1:1", "3:4", "4:3", "9:16", "16:9"}
ALLOWED_UPSCALES = {tuple(), ("2K",), ("4K",), ("2K", "4K")}
TASK_STATUSES = {
    "queued",
    "submitted",
    "provider_pending",
    "provider_running",
    "downloading",
    "completed",
    "failed",
    "canceled",
    "download_failed",
}
TERMINAL_STATUSES = {"completed", "failed", "canceled", "download_failed"}
PROVIDER_ACTIVE_STATUSES = {"submitted", "provider_pending", "provider_running"}
SUMMARY_STATUSES = (
    "queued",
    "submitted",
    "provider_pending",
    "provider_running",
    "downloading",
    "completed",
    "failed",
    "canceled",
    "download_failed",
)
ACTIVE_SUBMISSION_STATUSES = {"submitted", "provider_pending", "provider_running", "downloading"}
SAFE_PATH_SEGMENT_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
MIME_EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
REFERENCE_TAG_PATTERN = re.compile(r"^(?:char|loc|prop|subject)_[0-9]{3}$")
SCENE_ASSET_REFERENCE_TAG_PATTERN = re.compile(r"(?:(?:char|loc|prop|subject)_[0-9]{3}|prev_beat)$")
REFERENCE_TAG_IN_PROMPT_PATTERN = re.compile(r"@((?:(?:char|loc|prop|subject)_[0-9]{3})|prev_beat)\b")
PERSISTED_STYLE_BIBLE_VISUAL_REFERENCE_PURPOSE = "style_bible_visual_reference"
PERSISTED_SCENE_ASSET_PURPOSE = "scene_asset_generation"
INTERNAL_IMAGE_PURPOSES = {PERSISTED_STYLE_BIBLE_VISUAL_REFERENCE_PURPOSE, PERSISTED_SCENE_ASSET_PURPOSE}
DEFAULT_MEDIA_TASK_PAGE_SIZE = 1000
MAX_MEDIA_TASK_PAGE_SIZE = 1000
MEDIA_RQ_JOB_TIMEOUT_SECONDS = -1

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class NormalizedImageGenerationCommand:
    purpose: str
    project_id: str
    domain_revision_id: str | None
    entity_type: str
    entity_id: str
    candidate_count: int
    prompt: str
    negative_prompt: str
    model: str
    aspect_ratio: str
    upscale: tuple[str, ...]
    reference_images: tuple[dict[str, Any], ...]
    storage_namespace: str | None
    metadata: dict[str, Any]
    style_bible_revision_id: str | None = None


@dataclass(frozen=True, slots=True)
class DownloadCleanupOutcome:
    completed: bool
    errors: tuple[str, ...]




def effective_media_poll_interval_seconds(poll_interval_seconds: int) -> int:
    """Return the canonical provider polling cadence used by worker, API, and UI."""
    if type(poll_interval_seconds) is not int or poll_interval_seconds <= 0:
        raise ValueError("poll_interval_seconds must be a positive integer.")
    return poll_interval_seconds


def media_task_terminal_progress_percent(tasks: list[MediaTask], *, media_ceiling_percent: int = 95) -> int:
    """Return deterministic progress derived only from durable MediaTask states."""
    if not tasks:
        return 0
    if type(media_ceiling_percent) is not int or not 1 <= media_ceiling_percent <= 100:
        raise ValueError("media_ceiling_percent must be between 1 and 100.")
    terminal_count = sum(1 for task in tasks if task.status in TERMINAL_STATUSES)
    return min(media_ceiling_percent, int(terminal_count * media_ceiling_percent / len(tasks)))


class MediaTaskService:
    def __init__(
        self,
        session: Session,
        *,
        transport_policy: MediaTransportPolicy | None = None,
        transport: BoundedHttpTransport | None = None,
    ) -> None:
        self.session = session
        self.settings = get_settings()
        self.validator = PlatformContractValidator()
        self.projects = ProjectRepository(session)
        self.batches = MediaBatchRepository(session)
        self.tasks = MediaTaskRepository(session)
        self.artifacts = ArtifactRepository(session)
        self.artifact_service = ArtifactService(session, self.artifacts, self.settings)
        self.media_settings = MediaProviderSettingsService(session, SettingsRepository(session))
        self.transport_policy = transport_policy or DEFAULT_MEDIA_TRANSPORT_POLICY
        self.transport = transport

    def submit_image_generation(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Public Style Bible adapter preserved for backward compatibility."""
        request = self.validator.validate(
            "media_image_generate_request.schema.json",
            payload,
            error_code="MEDIA_IMAGE_REQUEST_INVALID",
        )
        command = NormalizedImageGenerationCommand(
            purpose=PERSISTED_STYLE_BIBLE_VISUAL_REFERENCE_PURPOSE,
            project_id=str(request["project_id"]),
            domain_revision_id=request.get("style_bible_revision_id"),
            style_bible_revision_id=request.get("style_bible_revision_id"),
            entity_type=str(request["entity_type"]),
            entity_id=str(request["entity_id"]),
            candidate_count=int(request["candidate_count"]),
            prompt=str(request["prompt"]),
            negative_prompt=str(request.get("negative_prompt", "")),
            model=str(request["model"]),
            aspect_ratio=str(request["aspect_ratio"]),
            upscale=tuple(str(item) for item in request.get("upscale", [])),
            reference_images=(),
            storage_namespace=None,
            metadata={},
        )
        response = self.submit_internal_image_generation(command)
        return self.validator.validate(
            "media_image_generate_response.schema.json",
            response,
            error_code="MEDIA_IMAGE_RESPONSE_INVALID",
        )

    def submit_internal_image_generation(
        self,
        command: NormalizedImageGenerationCommand,
        *,
        provider_settings: dict[str, Any] | None = None,
        client: GLabsWebhookClient | None = None,
        submit_immediately: bool = True,
        existing_batch_id: str | None = None,
    ) -> dict[str, Any]:
        """Create provider tasks from one normalized backend-owned image command.

        The public Style Bible request adapter and the Phase 11 owner both use
        this boundary. Prompt text is copied exactly. Provider-specific reference
        translation occurs only when a task is submitted.
        """
        if command.purpose not in INTERNAL_IMAGE_PURPOSES:
            raise AppError("MEDIA_IMAGE_REQUEST_INVALID", "Unsupported internal image-generation purpose.", 422, retryable=False)
        request = {
            "project_id": command.project_id,
            "entity_id": command.entity_id,
            "prompt": command.prompt,
            "negative_prompt": command.negative_prompt,
            "model": command.model,
            "aspect_ratio": command.aspect_ratio,
            "upscale": list(command.upscale),
        }
        self._validate_request_segments({
            **request,
            "style_bible_revision_id": command.style_bible_revision_id,
        })
        if command.storage_namespace is not None:
            self._validate_storage_namespace(
                command.storage_namespace,
                project_id=command.project_id,
                purpose=command.purpose,
            )
        if command.candidate_count < 1 or command.candidate_count > 5:
            raise AppError("MEDIA_IMAGE_REQUEST_INVALID", "Candidate count must be between 1 and 5.", 422, retryable=False)
        if self.projects.get(command.project_id) is None:
            raise AppError("PROJECT_NOT_FOUND", f"Project '{command.project_id}' was not found.", 404, retryable=False)
        active_settings = provider_settings or self.media_settings.get_settings_for_backend()
        self._validate_image_request(request, active_settings)
        self.media_settings._validate_runtime_ready(active_settings)
        active_client = client or self._build_client(active_settings)
        self._validate_reference_image_descriptors(
            command.reference_images,
            project_id=command.project_id,
            allow_prev_beat=command.purpose == PERSISTED_SCENE_ASSET_PURPOSE,
        )
        if command.purpose == PERSISTED_SCENE_ASSET_PURPOSE:
            self._validate_reference_prompt_bindings(command.prompt, command.reference_images)
        now = utc_now_iso()
        if existing_batch_id is None:
            batch = MediaBatch(
                batch_id=generate_id("media_batch"),
                purpose=command.purpose,
                project_id=command.project_id,
                style_bible_revision_id=command.style_bible_revision_id,
                domain_revision_id=command.domain_revision_id,
                total=command.candidate_count,
                created_at=now,
                updated_at=now,
            )
            self.batches.create(batch)
        else:
            batch = self.batches.get(existing_batch_id)
            if batch is None:
                raise AppError("MEDIA_BATCH_NOT_FOUND", f"Media batch '{existing_batch_id}' was not found.", 404, retryable=False)
            if (
                batch.purpose != command.purpose
                or batch.project_id != command.project_id
                or batch.domain_revision_id != command.domain_revision_id
                or batch.style_bible_revision_id != command.style_bible_revision_id
            ):
                raise AppError(
                    "MEDIA_BATCH_LINEAGE_MISMATCH",
                    "The existing media batch does not match the normalized generation command.",
                    409,
                    retryable=False,
                )
            batch.total += command.candidate_count
            batch.updated_at = now
            self.session.flush()
        task_ids: list[str] = []
        for candidate_index in range(1, command.candidate_count + 1):
            task = MediaTask(
                media_task_id=generate_id("media_task"),
                batch_id=batch.batch_id,
                purpose=command.purpose,
                project_id=command.project_id,
                style_bible_revision_id=command.style_bible_revision_id,
                domain_revision_id=command.domain_revision_id,
                entity_type=command.entity_type,
                entity_id=command.entity_id,
                candidate_index=candidate_index,
                status="queued",
                external_provider="glabs_webhook",
                external_task_id=None,
                provider_status=None,
                provider_submitted_at=None,
                provider_error_code=None,
                error_code=None,
                error=None,
                error_detail=None,
                prompt=command.prompt,
                negative_prompt=command.negative_prompt,
                model=command.model,
                aspect_ratio=command.aspect_ratio,
                upscale_json=list(command.upscale),
                reference_images_json=[dict(item) for item in command.reference_images],
                storage_namespace=command.storage_namespace,
                metadata_json=dict(command.metadata),
                result_json=[],
                storage_uri=None,
                mime_type=None,
                source_result_url=None,
                selected=False,
                locked=False,
                created_at=now,
                updated_at=now,
                completed_at=None,
                poll_count=0,
                poll_revision=0,
            )
            self.tasks.create(task)
            task_ids.append(task.media_task_id)
        self.session.commit()
        if submit_immediately:
            for task_id in task_ids:
                self._submit_queued_task(task_id, provider_settings=active_settings, client=active_client)
        tasks = [self._get_task_required(task_id) for task_id in task_ids]
        response = {
            "batch_id": batch.batch_id,
            "tasks": [
                {
                    "media_task_id": task.media_task_id,
                    "status": task.status,
                    "entity_type": task.entity_type,
                    "entity_id": task.entity_id,
                    "candidate_index": task.candidate_index,
                } for task in tasks
            ],
            "summary_url": f"/api/v3/media/tasks/summary?batch_id={batch.batch_id}",
        }
        return response

    def get_task_status(self, media_task_id: str) -> dict[str, Any]:
        task = self._get_task_required(media_task_id)
        if task.status not in TERMINAL_STATUSES:
            self._refresh_task(task.media_task_id)
            self.session.expire_all()
            task = self._get_task_required(media_task_id)
        response = self._status_response(task)
        return self.validator.validate(
            "media_task_status.schema.json",
            response,
            error_code="MEDIA_TASK_STATUS_INVALID",
        )

    def get_task_result(self, media_task_id: str) -> dict[str, Any]:
        task = self._get_task_required(media_task_id)
        if task.status not in TERMINAL_STATUSES:
            self._refresh_task(task.media_task_id)
            self.session.expire_all()
            task = self._get_task_required(media_task_id)
        if task.status == "completed":
            response = {
                "media_task_id": task.media_task_id,
                "status": task.status,
                "results": list(task.result_json),
            }
        elif task.status in {"failed", "download_failed", "canceled"}:
            provider_settings = self.media_settings.get_settings_for_backend()
            configured_api_key = str(provider_settings.get("api_key", "") or "")
            response = {
                "media_task_id": task.media_task_id,
                "status": task.status,
                "error_code": task.error_code,
                "provider_error_code": normalize_provider_error_code(
                    task.provider_error_code,
                    configured_api_key=configured_api_key,
                ),
                "error": task.error,
                "error_detail": task.error_detail,
            }
            response = {key: value for key, value in response.items() if value is not None}
        else:
            response = {
                "media_task_id": task.media_task_id,
                "status": task.status,
                "message": "Task not yet completed",
            }
        return self.validator.validate(
            "media_task_result.schema.json",
            response,
            error_code="MEDIA_TASK_RESULT_INVALID",
        )

    @staticmethod
    def _validate_task_status_filter(status: str | None) -> None:
        if status is not None and status not in TASK_STATUSES:
            raise AppError(
                "MEDIA_TASK_STATUS_INVALID",
                "Unsupported media task status filter.",
                422,
                retryable=False,
                details={"status": status},
            )

    def count_tasks(self, *, batch_id: str | None, status: str | None) -> int:
        self._validate_task_status_filter(status)
        return self.tasks.count_all(batch_id=batch_id, status=status)

    def list_tasks(
        self,
        *,
        batch_id: str | None,
        status: str | None,
        offset: int = 0,
        limit: int = DEFAULT_MEDIA_TASK_PAGE_SIZE,
    ) -> dict[str, Any]:
        self._validate_task_status_filter(status)
        if type(offset) is not int or offset < 0:
            raise AppError(
                "MEDIA_TASK_PAGE_INVALID",
                "Media task offset must be a non-negative integer.",
                422,
                retryable=False,
                details={"offset": offset},
            )
        if type(limit) is not int or not 1 <= limit <= MAX_MEDIA_TASK_PAGE_SIZE:
            raise AppError(
                "MEDIA_TASK_PAGE_INVALID",
                f"Media task limit must be between 1 and {MAX_MEDIA_TASK_PAGE_SIZE}.",
                422,
                retryable=False,
                details={"limit": limit},
            )
        items = self.tasks.list_all(
            batch_id=batch_id,
            status=status,
            offset=offset,
            limit=limit,
        )
        response = {
            "tasks": [
                {
                    "media_task_id": item.media_task_id,
                    "batch_id": item.batch_id,
                    "entity_type": item.entity_type,
                    "entity_id": item.entity_id,
                    "candidate_index": item.candidate_index,
                    "status": item.status,
                    "provider_status": item.provider_status,
                    "provider_submitted_at": item.provider_submitted_at,
                    "thumbnail_url": f"/api/v3/media/tasks/{item.media_task_id}/file" if item.storage_uri else None,
                    "result_url": f"/api/v3/media/tasks/{item.media_task_id}/result",
                    "prompt_preview": item.prompt[:120],
                    "model": item.model,
                    "aspect_ratio": item.aspect_ratio,
                    "selected": False,
                    "locked": False,
                    "created_at": item.created_at,
                    "completed_at": item.completed_at,
                }
                for item in items
            ]
        }
        return self.validator.validate(
            "media_task_card.schema.json",
            response,
            error_code="MEDIA_TASK_CARD_INVALID",
        )

    def _list_entire_batch(self, batch_id: str) -> list[MediaTask]:
        total = self.tasks.count_all(batch_id=batch_id)
        items: list[MediaTask] = []
        for offset in range(0, total, MAX_MEDIA_TASK_PAGE_SIZE):
            items.extend(
                self.tasks.list_all(
                    batch_id=batch_id,
                    offset=offset,
                    limit=MAX_MEDIA_TASK_PAGE_SIZE,
                )
            )
        return items

    def get_summary(self, *, batch_id: str) -> dict[str, Any]:
        batch = self.batches.get(batch_id)
        if batch is None:
            raise AppError("MEDIA_BATCH_NOT_FOUND", f"Media batch '{batch_id}' was not found.", 404, retryable=False)
        counts = {status: 0 for status in SUMMARY_STATUSES}
        counts.update(self.tasks.count_grouped_by_status(batch_id=batch_id))
        response = {
            "batch_id": batch.batch_id,
            "purpose": batch.purpose,
            "total": batch.total,
            **counts,
            "selected": 0,
            "locked": 0,
            "updated_at": batch.updated_at,
        }
        return self.validator.validate(
            "media_task_summary.schema.json",
            response,
            error_code="MEDIA_TASK_SUMMARY_INVALID",
        )

    def get_progress_snapshot(
        self,
        *,
        batch_id: str,
        poll_interval_seconds: int,
    ) -> dict[str, Any]:
        summary = self.get_summary(batch_id=batch_id)
        return {
            "batch_id": summary["batch_id"],
            "total": summary["total"],
            "queued": summary["queued"],
            "submitted": summary["submitted"],
            "provider_pending": summary["provider_pending"],
            "provider_running": summary["provider_running"],
            "downloading": summary["downloading"],
            "completed": summary["completed"],
            "failed": summary["failed"],
            "canceled": summary["canceled"],
            "download_failed": summary["download_failed"],
            "effective_poll_interval_seconds": effective_media_poll_interval_seconds(
                int(poll_interval_seconds)
            ),
            "updated_at": summary["updated_at"],
        }

    def get_active_batches(self, *, project_id: str) -> dict[str, Any]:
        if self.projects.get(project_id) is None:
            raise AppError("PROJECT_NOT_FOUND", f"Project '{project_id}' was not found.", 404, retryable=False)
        active_batches = self.batches.list_active_batches_for_project(
            project_id,
            active_statuses={"queued", "submitted", "provider_pending", "provider_running", "downloading"},
        )
        latest = self.batches.get_latest_batch_for_project(project_id)
        response = {
            "project_id": project_id,
            "active_batches": [
                {
                    "batch_id": item.batch_id,
                    "purpose": item.purpose,
                    "total": item.total,
                    "active_task_count": item.active_task_count,
                    "created_at": item.created_at,
                    "updated_at": item.updated_at,
                }
                for item in active_batches
            ],
            "latest_batch_id": latest.batch_id if latest is not None else None,
        }
        return self.validator.validate(
            "media_active_batches.schema.json",
            response,
            error_code="MEDIA_ACTIVE_BATCH_RESPONSE_INVALID",
        )

    def resolve_task_file_path(self, media_task_id: str, *, result_index: int = 1) -> Path:
        if result_index < 1:
            raise AppError("MEDIA_TASK_FILE_MISSING", "Media result index must be at least 1.", 404, retryable=False)
        task = self._get_task_required(media_task_id)
        storage_uri: str | None = None
        if result_index == 1:
            storage_uri = task.storage_uri
        if task.result_json and result_index <= len(task.result_json):
            raw = task.result_json[result_index - 1]
            if isinstance(raw, dict) and isinstance(raw.get("storage_uri"), str):
                storage_uri = raw["storage_uri"]
        if not storage_uri:
            raise AppError("MEDIA_TASK_FILE_MISSING", "Media task has no downloaded file for this result index.", 404, retryable=False)
        path = self._resolve_artifact_path(Path(storage_uri))
        if not path.is_file():
            raise AppError("MEDIA_TASK_FILE_MISSING", "Downloaded media file is missing.", 404, retryable=False)
        return path

    def resolve_task_file_mime_type(self, media_task_id: str, *, result_index: int = 1) -> str:
        task = self._get_task_required(media_task_id)
        if task.result_json and 1 <= result_index <= len(task.result_json):
            raw = task.result_json[result_index - 1]
            if isinstance(raw, dict) and isinstance(raw.get("mime_type"), str):
                return raw["mime_type"]
        return task.mime_type or "application/octet-stream"

    def execute_batch_to_terminal(
        self,
        batch_id: str,
        *,
        provider_settings: dict[str, Any],
        max_cycles: int | None = None,
        client: GLabsWebhookClient | None = None,
        on_progress: Callable[[list[MediaTask]], None] | None = None,
    ) -> list[MediaTask]:
        """Execute a pre-created media batch until provider-authoritative terminal state.

        Production callers leave ``max_cycles`` unset and therefore continue
        polling indefinitely.  ``max_cycles`` exists only as an explicit
        deterministic caller/test guard; reaching it never mutates MediaTask
        state and is not a media lifecycle timeout.
        """
        import time

        self.media_settings._validate_runtime_ready(provider_settings)
        interval = float(effective_media_poll_interval_seconds(int(provider_settings.get("poll_interval_seconds", 1))))
        if max_cycles is not None and (type(max_cycles) is not int or max_cycles <= 0):
            raise ValueError("max_cycles must be a positive integer or None.")
        cycles = 0
        while True:
            tasks = self._list_entire_batch(batch_id)
            if not tasks:
                raise AppError(
                    "MEDIA_BATCH_NOT_FOUND",
                    "The media batch has no tasks.",
                    404,
                    retryable=False,
                    details={"batch_id": batch_id},
                )
            active = [task for task in tasks if task.status not in TERMINAL_STATUSES]
            if not active:
                return tasks
            for task in active:
                self._refresh_task(
                    task.media_task_id,
                    provider_settings_override=provider_settings,
                    client_override=client,
                )
            self.session.expire_all()
            tasks = self._list_entire_batch(batch_id)
            if on_progress is not None:
                on_progress(tasks)
                self.session.expire_all()
                tasks = self._list_entire_batch(batch_id)
            if all(task.status in TERMINAL_STATUSES for task in tasks):
                return tasks
            cycles += 1
            if max_cycles is not None and cycles >= max_cycles:
                raise AppError(
                    "MEDIA_BATCH_CYCLE_LIMIT_REACHED",
                    "Media batch is still active after the caller's explicit cycle limit.",
                    409,
                    retryable=True,
                    details={"batch_id": batch_id, "max_cycles": max_cycles},
                )
            time.sleep(interval)

    def advance_batch_once(
        self,
        batch_id: str,
        *,
        provider_settings: dict[str, Any],
        client: GLabsWebhookClient | None = None,
        on_progress: Callable[[list[MediaTask]], None] | None = None,
    ) -> list[MediaTask]:
        """Advance queued/pending provider work once without waiting for terminal."""
        self.media_settings._validate_runtime_ready(provider_settings)
        tasks = self._list_entire_batch(batch_id)
        if not tasks:
            raise AppError(
                "MEDIA_BATCH_NOT_FOUND",
                "The media batch has no tasks.",
                404,
                retryable=False,
                details={"batch_id": batch_id},
            )
        active = [task for task in tasks if task.status not in TERMINAL_STATUSES]
        for task in active:
            self._refresh_task(
                task.media_task_id,
                provider_settings_override=provider_settings,
                client_override=client,
            )
        self.session.expire_all()
        tasks = self._list_entire_batch(batch_id)
        if on_progress is not None:
            on_progress(tasks)
            self.session.expire_all()
            tasks = self._list_entire_batch(batch_id)
        return tasks

    def _refresh_task(
        self,
        media_task_id: str,
        *,
        provider_settings_override: dict[str, Any] | None = None,
        client_override: GLabsWebhookClient | None = None,
    ) -> None:
        task = self._get_task_required(media_task_id)
        if task.status in TERMINAL_STATUSES or task.status == "downloading":
            return

        provider_settings = provider_settings_override or self.media_settings.get_settings_for_backend()
        api_key = str(provider_settings.get("api_key", "") or "")
        now = utc_now_iso()

        if task.status == "queued":
            self.media_settings._validate_runtime_ready(provider_settings)
            submitted_or_terminal = self._submit_queued_task(
                task.media_task_id,
                provider_settings=provider_settings,
                client=client_override or self._build_client(provider_settings),
            )
            if submitted_or_terminal:
                return
            self.session.expire_all()
            task = self._get_task_required(media_task_id)
            if task.status != "queued":
                return
            return

        if task.status not in PROVIDER_ACTIVE_STATUSES:
            return
        if not task.external_task_id or not task.provider_submitted_at:
            return

        self.media_settings._validate_runtime_ready(provider_settings)
        lease_seconds = self.transport_policy.poll_claim_lease_seconds(
            effective_media_poll_interval_seconds(int(provider_settings["poll_interval_seconds"]))
        )
        claim_expires_at = self._add_seconds(now, lease_seconds)
        claim_token = uuid4().hex
        try:
            snapshot = self.tasks.claim_provider_poll(
                task.media_task_id,
                claim_token=claim_token,
                claimed_at=now,
                expires_at=claim_expires_at,
            )
        except Exception as exc:
            self.session.rollback()
            raise self._stable_poll_exception(
                exc,
                configured_api_key=api_key,
                message="Media provider poll claim failed.",
            ) from exc
        self.session.expire_all()
        if snapshot is None:
            return

        client: GLabsWebhookClient | None = None
        status_payload: dict[str, Any] | None = None
        provider_status: str | None = None
        response_applied = False
        response_time = utc_now_iso()

        try:
            client = client_override or self._build_client(provider_settings)
            status_payload = client.get_status(snapshot.external_task_id)
            provider_status = status_payload["status"]
            response_time = utc_now_iso()

            if provider_status == "pending":
                response_applied = self.tasks.apply_provider_pending_from_claim(
                    snapshot.media_task_id,
                    claim_token=snapshot.claim_token,
                    updated_at=response_time,
                )
            elif provider_status == "running":
                response_applied = self.tasks.apply_provider_running_from_claim(
                    snapshot.media_task_id,
                    claim_token=snapshot.claim_token,
                    updated_at=response_time,
                )
            elif provider_status == "failed":
                provider_error_code = normalize_provider_error_code(
                    status_payload.get("error_code"),
                    configured_api_key=api_key,
                )
                normalized = normalize_provider_failure(
                    provider_error_code,
                    status_payload.get("error"),
                    status_payload.get("error_detail"),
                )
                error = self._safe_provider_text(
                    status_payload.get("error"),
                    "Provider task failed.",
                    configured_api_key=api_key,
                )
                error_detail = self._safe_provider_text(
                    status_payload.get("error_detail"),
                    error,
                    configured_api_key=api_key,
                )
                response_applied = self.tasks.apply_provider_failure_from_claim(
                    snapshot.media_task_id,
                    claim_token=snapshot.claim_token,
                    provider_error_code=provider_error_code,
                    error_code=normalized.error_code,
                    error=error,
                    error_detail=error_detail,
                    updated_at=response_time,
                )
            elif provider_status != "completed":
                response_applied = self.tasks.apply_provider_failure_from_claim(
                    snapshot.media_task_id,
                    claim_token=snapshot.claim_token,
                    provider_error_code=None,
                    error_code="MEDIA_PROVIDER_UPSTREAM_ERROR",
                    error="Media provider returned an unknown task status.",
                    error_detail="Media provider returned an unknown task status.",
                    updated_at=response_time,
                )
        except AppError as exc:
            if exc.retryable:
                # A transient control-plane failure (request deadline, provider
                # unavailability, temporary upstream capacity) is not terminal
                # authority for a provider-owned task. Release the poll claim so
                # the next scheduled tick can ask the provider again.
                self._release_poll_claim_or_raise(
                    snapshot.media_task_id,
                    snapshot.claim_token,
                    configured_api_key=api_key,
                )
                return
            # A non-retryable provider/control-plane AppError after this task already owns a
            # provider id is a terminal failure of this MediaTask, not a reason
            # to leave the task provider-active and fail only the outer worker.
            # Leaving it active would consume global provider capacity indefinitely
            # and make Scene Asset operations appear "Generating"
            # after the provider has already rejected the request (for example
            # HTTP 400/401/403 from a status poll).
            response_time = utc_now_iso()
            raw_provider_error_code = self._detail_value(exc, "provider_error_code")
            if raw_provider_error_code is None:
                raw_provider_error_code = self._detail_value(exc, "status_code")
            provider_error_code = normalize_provider_error_code(
                raw_provider_error_code,
                configured_api_key=api_key,
            )
            try:
                response_applied = self.tasks.apply_provider_failure_from_claim(
                    snapshot.media_task_id,
                    claim_token=snapshot.claim_token,
                    provider_error_code=provider_error_code,
                    error_code=exc.error_code,
                    error=self._safe_provider_text(
                        exc.message,
                        "Media provider status refresh failed.",
                        configured_api_key=api_key,
                    ),
                    error_detail=self._error_detail_from_exception(
                        exc,
                        configured_api_key=api_key,
                    ),
                    updated_at=response_time,
                )
            except Exception as apply_exc:
                self.session.rollback()
                raise self._stable_poll_exception(
                    apply_exc,
                    configured_api_key=api_key,
                    message="Media provider failure state could not be persisted.",
                ) from apply_exc
            if response_applied:
                self._touch_batch_and_commit(task.batch_id, response_time)
            return
        except Exception as exc:
            self._release_poll_claim_or_raise(
                snapshot.media_task_id,
                snapshot.claim_token,
                configured_api_key=api_key,
            )
            raise self._stable_poll_exception(
                exc,
                configured_api_key=api_key,
                message="Media provider status refresh failed.",
            ) from exc

        self.session.expire_all()
        if provider_status != "completed":
            if not response_applied:
                self._release_poll_claim_or_raise(
                    snapshot.media_task_id,
                    snapshot.claim_token,
                    configured_api_key=api_key,
                )
                return
            self._touch_batch_and_commit(task.batch_id, response_time)
            return

        try:
            download_owner = self.tasks.claim_download_from_poll_claim_and_touch_batch(
                snapshot.media_task_id,
                claim_token=snapshot.claim_token,
                updated_at=response_time,
            )
        except Exception as exc:
            # The atomic handoff transaction rolled back.  The provider-poll
            # claim remains durable and no result/download I/O may start.
            self.session.rollback()
            raise self._stable_poll_exception(
                exc,
                configured_api_key=api_key,
                message="Media download ownership handoff failed.",
            ) from exc
        if download_owner is None:
            return

        assert client is not None
        assert status_payload is not None
        try:
            results = status_payload.get("results")
            if not isinstance(results, list) or not results:
                result_payload = client.get_result(download_owner.external_task_id)
                results = result_payload["results"]
            self._download_completed_results(download_owner, results, client, configured_api_key=api_key)
        except AppError as exc:
            self._finalize_download_owner_failure(download_owner, exc, configured_api_key=api_key)
        except Exception as exc:
            reason = sanitize_media_provider_value(
                str(exc),
                configured_api_key=api_key,
                max_string_chars=512,
            )
            normalized = AppError(
                "MEDIA_RESULT_DOWNLOAD_FAILED",
                "Media result download failed.",
                502,
                retryable=False,
                details={"reason": reason},
            )
            self._finalize_download_owner_failure(download_owner, normalized, configured_api_key=api_key)

    def _download_completed_results(
        self,
        owner: DownloadOwnerSnapshot,
        result_urls: list[Any],
        client: GLabsWebhookClient,
        *,
        configured_api_key: str,
    ) -> None:
        normalized_urls = self._normalize_result_urls(result_urls, configured_api_key=configured_api_key)
        now = utc_now_iso()
        result_records: list[dict[str, Any]] = []
        first_storage_uri: str | None = None
        first_mime_type: str | None = None
        first_source_url: str | None = None
        temp_paths: list[Path] = []
        final_paths: list[Path] = []

        try:
            for index, raw_url in enumerate(normalized_urls, start=1):
                temp_path = self._new_temp_path(owner.media_task_id, index)
                temp_paths.append(temp_path)
                metadata = client.download_file(
                    raw_url,
                    temp_path,
                    int(self.settings.media_provider_max_download_bytes),
                )
                extension = MIME_EXTENSIONS.get(metadata.mime_type)
                if extension is None:
                    raise AppError(
                        "MEDIA_RESULT_DOWNLOAD_FAILED",
                        "Downloaded media has an unsupported image type.",
                        502,
                        retryable=False,
                    )
                logical_id = self._logical_id_for_download_owner(owner, index=index)
                version_no = 1
                relative_path = self._storage_uri_for_download_owner(
                    owner,
                    index=index,
                    version_no=version_no,
                    extension=extension,
                )
                full_path = self._resolve_artifact_path(relative_path)
                full_path.parent.mkdir(parents=True, exist_ok=True)
                if full_path.exists() or self.artifacts.get_current_by_logical_id(logical_id) is not None:
                    raise AppError(
                        "MEDIA_RESULT_DOWNLOAD_FAILED",
                        "A durable result already exists for this media task result index.",
                        409,
                        retryable=False,
                    )
                os.replace(temp_path, full_path)
                final_paths.append(full_path)

                artifact = Artifact(
                    artifact_id=generate_id("artifact"),
                    logical_id=logical_id,
                    version_no=version_no,
                    is_current=True,
                    superseded_by_artifact_id=None,
                    artifact_type=ArtifactType.IMAGE,
                    artifact_role=None,
                    path=relative_path.as_posix(),
                    content_hash=metadata.sha256,
                    sha256=metadata.sha256,
                    mime_type=metadata.mime_type,
                    size_bytes=metadata.size_bytes,
                    width=None,
                    height=None,
                    duration_sec=None,
                    provider_result_url=raw_url,
                    provider_source="glabs_webhook",
                    source_job_id=None,
                    status=ArtifactStatus.READY,
                    created_at=now,
                    deleted_at=None,
                )
                self.artifacts.create(artifact)
                result_record = {
                    "artifact_id": artifact.artifact_id,
                    "image_id": f"img_{owner.media_task_id}_{index:02d}",
                    "storage_uri": relative_path.as_posix(),
                    "file_url": f"/api/v3/media/tasks/{owner.media_task_id}/file?result_index={index}",
                    "mime_type": metadata.mime_type,
                    "source_result_url": raw_url,
                    "downloaded_at": now,
                }
                result_records.append(result_record)
                if first_storage_uri is None:
                    first_storage_uri = relative_path.as_posix()
                    first_mime_type = metadata.mime_type
                    first_source_url = raw_url

            completed = self.tasks.complete_download_and_touch_batch(
                owner.media_task_id,
                batch_id=owner.batch_id,
                result_json=result_records,
                storage_uri=first_storage_uri,
                mime_type=first_mime_type,
                source_result_url=first_source_url,
                completed_at=now,
            )
            if not completed:
                raise AppError(
                    "MEDIA_RESULT_DOWNLOAD_FAILED",
                    "Media download ownership was lost before completion.",
                    409,
                    retryable=False,
                )
        except Exception as exc:
            self.session.rollback()
            self._cleanup_paths(temp_paths)
            self._cleanup_paths(final_paths)
            if isinstance(exc, AppError):
                raise
            if isinstance(exc, (SQLAlchemyError, OSError)):
                raise AppError(
                    "MEDIA_RESULT_DOWNLOAD_FAILED",
                    "Media result could not be stored durably.",
                    502,
                    retryable=False,
                ) from exc
            raise
        finally:
            self._cleanup_paths(temp_paths)

    def _finalize_download_owner_failure(
        self,
        owner: DownloadOwnerSnapshot,
        exc: AppError,
        *,
        configured_api_key: str,
    ) -> None:
        rollback_failed = False
        rollback_failure: Exception | None = None
        try:
            self.session.rollback()
        except Exception as rollback_exc:
            rollback_failed = True
            rollback_failure = rollback_exc

        # Physical cleanup is best-effort only. It must never prevent durable
        # ownership termination from being attempted. Any residue remains
        # deterministic and is recovered at startup.
        first_cleanup = self._try_cleanup_download_owner_files(
            owner, configured_api_key=configured_api_key
        )

        now = utc_now_iso()
        provider_error_code = normalize_provider_error_code(
            self._detail_value(exc, "provider_error_code")
            if self._detail_value(exc, "provider_error_code") is not None
            else self._detail_value(exc, "status_code"),
            configured_api_key=configured_api_key,
        )
        error_code = exc.error_code or "MEDIA_RESULT_DOWNLOAD_FAILED"
        error = self._safe_provider_text(
            exc.message,
            "Media result download failed.",
            configured_api_key=configured_api_key,
        )
        error_detail = self._error_detail_from_exception(exc, configured_api_key=configured_api_key)
        if first_cleanup.errors:
            error_detail = self._append_deferred_cleanup_detail(error_detail)
        try:
            logical_prefix: str | None = self._logical_prefix_for_download_owner(owner).as_posix()
        except AppError:
            # A hostile or corrupted pre-production identity must not escape the
            # artifact root.  No path cleanup is attempted for an untrusted
            # prefix, but the task can still be finalized safely.
            logical_prefix = None

        current_failed: Exception | None = None
        finalized = False
        if rollback_failed:
            current_failed = rollback_failure
        else:
            try:
                finalized = self.tasks.finalize_download_failure_and_touch_batch(
                    owner.media_task_id,
                    batch_id=owner.batch_id,
                    logical_id_prefix=logical_prefix,
                    error_code=error_code,
                    provider_error_code=provider_error_code,
                    error=error,
                    error_detail=error_detail,
                    updated_at=now,
                )
                try:
                    self.session.expire_all()
                except Exception:
                    pass
            except Exception as finalize_exc:
                try:
                    self.session.rollback()
                except Exception:
                    pass
                current_failed = finalize_exc

        if not finalized and current_failed is not None:
            try:
                with build_session_factory(self.settings)() as fresh_session:
                    finalized = MediaTaskRepository(fresh_session).finalize_download_failure_and_touch_batch(
                        owner.media_task_id,
                        batch_id=owner.batch_id,
                        logical_id_prefix=logical_prefix,
                        error_code=error_code,
                        provider_error_code=provider_error_code,
                        error=error,
                        error_detail=error_detail,
                        updated_at=now,
                    )
            except Exception as fresh_exc:
                raise self._stable_download_finalization_error(
                    fresh_exc, configured_api_key=configured_api_key
                ) from current_failed

        if finalized:
            # Retry physical cleanup once after durable terminalization. It is
            # still best-effort; a second failure leaves deterministic residue
            # for startup recovery and must not mask the original media error.
            self._try_cleanup_download_owner_files(owner, configured_api_key=configured_api_key)
            return

        # Another durable path already ended ownership. Do not overwrite it and
        # do not perform provider/result/download retries.
        return

    def _make_download_cleanup_outcome(
        self,
        *,
        completed: bool,
        errors: list[str],
    ) -> DownloadCleanupOutcome:
        try:
            bounded_errors = tuple(str(error)[:96] for error in errors[:8])
            return DownloadCleanupOutcome(completed=completed, errors=bounded_errors)
        except Exception:
            return DownloadCleanupOutcome(completed=False, errors=("cleanup_outcome_unavailable",))

    def _resolve_artifact_root_for_cleanup(
        self,
        record_error: Any,
    ) -> Path | None:
        try:
            configured_value = self.settings.artifact_root
        except Exception:
            record_error("artifact_root_configuration_unavailable")
            return None
        try:
            configured = Path(configured_value)
        except Exception:
            record_error("artifact_root_path_unavailable")
            return None
        try:
            root_base = configured if configured.is_absolute() else self.settings.root_dir / configured
        except Exception:
            record_error("artifact_root_path_unavailable")
            return None
        try:
            resolved = root_base.resolve()
        except Exception:
            record_error("artifact_root_resolution_unavailable")
            return None
        try:
            if resolved.exists() and not resolved.is_dir():
                record_error("artifact_root_not_directory")
                return None
        except Exception:
            record_error("artifact_root_metadata_unavailable")
            return None
        return resolved

    def _try_cleanup_download_owner_files(
        self,
        owner: DownloadOwnerSnapshot,
        *,
        configured_api_key: str,
    ) -> DownloadCleanupOutcome:
        errors: list[str] = []

        def record_error(reason: str) -> None:
            try:
                safe = sanitize_media_provider_value(
                    reason,
                    configured_api_key=configured_api_key,
                    max_string_chars=96,
                )
                text = str(safe) if isinstance(safe, str) and safe else "cleanup_error"
            except Exception:
                text = "cleanup_error"
            if len(errors) < 8:
                errors.append(text[:96])

        root = self._resolve_artifact_root_for_cleanup(record_error)
        if root is None:
            return self._make_download_cleanup_outcome(completed=False, errors=errors)

        temp_root: Path | None = None
        try:
            temp_root = root / ".tmp"
        except Exception:
            record_error("temporary_root_unavailable")

        if temp_root is not None:
            try:
                temp_root_exists = temp_root.exists()
            except Exception:
                record_error("temporary_root_metadata_unavailable")
                temp_root_exists = False
            if temp_root_exists:
                try:
                    temp_prefix = self.safe_path_segment(owner.media_task_id, "media_task_id")
                    temp_paths = list(temp_root.glob(f"{temp_prefix}_*.part"))
                except Exception:
                    record_error("temporary_file_scan_unavailable")
                    temp_paths = []
                for path in temp_paths:
                    try:
                        path.unlink()
                    except Exception:
                        record_error("temporary_file_remove_failed")

        task_root: Path | None = None
        try:
            relative_root = self._logical_prefix_for_download_owner(owner)
        except Exception:
            record_error("task_logical_prefix_unavailable")
            relative_root = None

        if relative_root is not None:
            try:
                task_root = (root / relative_root).resolve()
            except Exception:
                record_error("task_root_resolution_unavailable")
                task_root = None
        if task_root is not None:
            try:
                task_root.relative_to(root)
            except Exception:
                record_error("task_root_outside_artifact_root")
                task_root = None

        if task_root is not None:
            try:
                task_root_exists = task_root.exists()
            except Exception:
                record_error("task_root_metadata_unavailable")
                task_root_exists = False
            if task_root_exists:
                try:
                    task_root_is_dir = task_root.is_dir()
                except Exception:
                    record_error("task_root_metadata_unavailable")
                    task_root_is_dir = False
                try:
                    if task_root_is_dir:
                        shutil.rmtree(task_root)
                    else:
                        task_root.unlink()
                except Exception:
                    record_error("task_root_remove_failed")

        return self._make_download_cleanup_outcome(completed=not errors, errors=errors)

    def _cleanup_download_owner_files(self, owner: DownloadOwnerSnapshot) -> None:
        outcome = self._try_cleanup_download_owner_files(owner, configured_api_key="")
        if outcome.errors:
            raise RuntimeError("Media download owner cleanup failed.")

    @staticmethod
    def _append_deferred_cleanup_detail(error_detail: str) -> str:
        note = "Local cleanup deferred."
        if note in error_detail:
            return error_detail
        combined = f"{error_detail} {note}".strip()
        return combined[:1000]

    @staticmethod
    def _stable_download_finalization_error(
        exc: Exception, *, configured_api_key: str
    ) -> AppError:
        reason = sanitize_media_provider_value(
            "media_download_finalization_failed",
            configured_api_key=configured_api_key,
            max_string_chars=96,
        )
        return AppError(
            "MEDIA_RESULT_DOWNLOAD_FAILED",
            "Media download failure could not be finalized durably.",
            500,
            retryable=False,
            details={"reason": reason},
        )

    @staticmethod
    def _namespace_root_for_purpose(purpose: str | None) -> str:
        if purpose == PERSISTED_STYLE_BIBLE_VISUAL_REFERENCE_PURPOSE:
            return "visual_references"
        return "scene_assets"

    def _validated_namespace_path(
        self,
        value: str,
        *,
        project_id: str,
        purpose: str | None = None,
    ) -> Path:
        parts = Path(value).parts
        if not parts or any(part in {"", ".", ".."} or SAFE_PATH_SEGMENT_PATTERN.fullmatch(part) is None for part in parts):
            raise AppError("MEDIA_IMAGE_REQUEST_INVALID", "Storage namespace is invalid.", 422, retryable=False)
        expected = (
            "project",
            self.safe_path_segment(project_id, "project_id"),
            self._namespace_root_for_purpose(purpose),
        )
        if tuple(parts[:3]) != expected:
            raise AppError(
                "MEDIA_IMAGE_REQUEST_INVALID",
                "Storage namespace is not valid for the requested media purpose.",
                422,
                retryable=False,
            )
        return Path(*parts)

    def _validate_storage_namespace(
        self,
        value: str,
        *,
        project_id: str,
        purpose: str | None = None,
    ) -> None:
        self._validated_namespace_path(value, project_id=project_id, purpose=purpose)

    def _validate_reference_image_descriptors(
        self,
        values: tuple[dict[str, Any], ...],
        *,
        project_id: str,
        allow_prev_beat: bool = False,
    ) -> None:
        seen_tags: set[str] = set()
        tag_pattern = SCENE_ASSET_REFERENCE_TAG_PATTERN if allow_prev_beat else REFERENCE_TAG_PATTERN
        for item in values:
            if set(item) != {"reference_tag", "artifact_id", "sha256", "mime_type"}:
                raise AppError("MEDIA_REFERENCE_IMAGE_INVALID", "Reference image descriptor is invalid.", 422, retryable=False)
            reference_tag = str(item["reference_tag"])
            if tag_pattern.fullmatch(reference_tag) is None or reference_tag in seen_tags:
                raise AppError("MEDIA_REFERENCE_IMAGE_INVALID", "Reference image tag is invalid or duplicated.", 422, retryable=False)
            seen_tags.add(reference_tag)
            artifact = self.artifact_service.get_required(str(item["artifact_id"]))
            if artifact.status != ArtifactStatus.READY or artifact.deleted_at is not None or artifact.artifact_type != ArtifactType.IMAGE:
                raise AppError("MEDIA_REFERENCE_IMAGE_INVALID", "Reference image Artifact is unavailable.", 409, retryable=False)
            if artifact.sha256 != item["sha256"] or artifact.mime_type != item["mime_type"]:
                raise AppError("MEDIA_REFERENCE_IMAGE_INVALID", "Reference image integrity metadata does not match.", 409, retryable=False)
            if artifact.mime_type not in MIME_EXTENSIONS:
                raise AppError("MEDIA_REFERENCE_IMAGE_INVALID", "Reference image MIME type is unsupported.", 422, retryable=False)
            if not artifact.logical_id.startswith(f"project/{project_id}/"):
                raise AppError("MEDIA_REFERENCE_IMAGE_PROJECT_MISMATCH", "Reference image belongs to another project.", 409, retryable=False)
            self.artifact_service.resolve_file_path(artifact)

    @staticmethod
    def _validate_reference_prompt_bindings(prompt: str, values: tuple[dict[str, Any], ...]) -> None:
        expected_tags = {str(item["reference_tag"]) for item in values}
        prompt_tags = set(REFERENCE_TAG_IN_PROMPT_PATTERN.findall(prompt))
        if prompt_tags != expected_tags:
            raise AppError(
                "MEDIA_REFERENCE_IMAGE_INVALID",
                "Prompt reference tags must exactly match the frozen named reference images.",
                422,
                retryable=False,
                details={"expected_reference_tags": sorted(expected_tags), "prompt_reference_tags": sorted(prompt_tags)},
            )

    def _provider_reference_images(self, task: MediaTask) -> list[dict[str, str]]:
        """Translate frozen Artifact descriptors into provider-native named data-URI references.

        Physical Artifact filenames are storage details. Provider-facing names are deterministic
        aliases derived only from the canonical reference tag plus the verified MIME type.
        """
        descriptors = tuple(task.reference_images_json or [])
        self._validate_reference_image_descriptors(
            descriptors,
            project_id=str(task.project_id),
            allow_prev_beat=task.purpose == PERSISTED_SCENE_ASSET_PURPOSE,
        )
        if task.purpose == PERSISTED_SCENE_ASSET_PURPOSE:
            self._validate_reference_prompt_bindings(task.prompt, descriptors)
        result: list[dict[str, str]] = []
        for item in descriptors:
            artifact = self.artifact_service.get_required(str(item["artifact_id"]))
            raw = self.artifact_service.resolve_file_path(artifact).read_bytes()
            extension = MIME_EXTENSIONS[str(artifact.mime_type)]
            reference_tag = str(item["reference_tag"])
            result.append({
                "data": f"data:{artifact.mime_type};base64,{base64.b64encode(raw).decode('ascii')}",
                "name": f"{reference_tag}{extension}",
            })
        return result

    def _validate_image_request(self, payload: dict[str, Any], provider_settings: dict[str, Any]) -> None:
        if not provider_settings.get("enabled"):
            raise AppError("MEDIA_PROVIDER_CONFIG_INVALID", "Media provider is disabled.", 409, retryable=False)
        if str(payload.get("prompt", "")).strip() == "":
            raise AppError("MEDIA_IMAGE_REQUEST_INVALID", "Prompt must not be empty.", 422, retryable=False)
        if payload["model"] not in ALLOWED_MODELS:
            raise AppError("MEDIA_PROVIDER_CONFIG_INVALID", "Invalid image model.", 422, retryable=False)
        if payload["aspect_ratio"] not in ALLOWED_ASPECT_RATIOS:
            raise AppError("MEDIA_PROVIDER_CONFIG_INVALID", "Invalid aspect ratio.", 422, retryable=False)
        upscale_tuple = tuple(payload.get("upscale", []))
        if upscale_tuple not in ALLOWED_UPSCALES:
            raise AppError("MEDIA_PROVIDER_CONFIG_INVALID", "Invalid upscale configuration.", 422, retryable=False)
        if provider_settings.get("enabled_media_kinds") != ["image"]:
            raise AppError("MEDIA_PROVIDER_CONFIG_INVALID", "Only image media kind is supported in V1.", 409, retryable=False)
        if not provider_settings.get("base_url"):
            raise AppError("MEDIA_PROVIDER_CONFIG_INVALID", "Media provider base URL is missing.", 409, retryable=False)
        if not provider_settings.get("api_key"):
            raise AppError("MEDIA_PROVIDER_CONFIG_INVALID", "Media provider API key is missing.", 409, retryable=False)
        if any(key in payload for key in ("mode", "resolution", "video_length")):
            raise AppError("MEDIA_IMAGE_REQUEST_INVALID", "Video or Grok-style request fields are not supported.", 422, retryable=False)

    def _validate_request_segments(self, payload: dict[str, Any]) -> None:
        self.safe_path_segment(str(payload["project_id"]), "project_id")
        self.safe_path_segment(str(payload["entity_id"]), "entity_id")
        style_bible_revision_id = payload.get("style_bible_revision_id")
        if style_bible_revision_id is not None:
            self.safe_path_segment(str(style_bible_revision_id), "style_bible_revision_id")

    def _status_response(self, task: MediaTask) -> dict[str, Any]:
        provider_settings = self.media_settings.get_settings()
        return {
            "media_task_id": task.media_task_id,
            "batch_id": task.batch_id,
            "external_provider": task.external_provider,
            "external_task_id": task.external_task_id,
            "status": task.status,
            "provider_status": task.provider_status,
            "provider_submitted_at": task.provider_submitted_at,
            "entity_type": task.entity_type,
            "entity_id": task.entity_id,
            "candidate_index": task.candidate_index,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
            "poll_count": task.poll_count,
            "next_poll_after_seconds": effective_media_poll_interval_seconds(
                int(provider_settings["poll_interval_seconds"])
            ),
        }

    def _logical_id_for_task_result(self, task: MediaTask, *, index: int) -> str:
        task_id = self.safe_path_segment(str(task.media_task_id), "media_task_id")
        result_name = f"result_{index:03d}"
        if task.storage_namespace:
            namespace = self._validated_namespace_path(
                task.storage_namespace,
                project_id=str(task.project_id),
                purpose=str(task.purpose),
            )
            return (namespace / task_id / result_name).as_posix()
        if task.style_bible_revision_id:
            style_bible_revision_id = self.safe_path_segment(str(task.style_bible_revision_id), "style_bible_revision_id")
            entity_id = self.safe_path_segment(str(task.entity_id), "entity_id")
            entity_plural = self._entity_plural(task.entity_type)
            project_id = self.safe_path_segment(str(task.project_id), "project_id")
            return (
                Path("project")
                / project_id
                / "style_bible"
                / style_bible_revision_id
                / entity_plural
                / entity_id
                / "candidates"
                / task_id
                / result_name
            ).as_posix()
        batch_id = self.safe_path_segment(str(task.batch_id), "batch_id")
        return (Path("media") / batch_id / task_id / result_name).as_posix()

    def _storage_uri_for_task(self, task: MediaTask, *, index: int, version_no: int, extension: str) -> Path:
        logical_id = Path(self._logical_id_for_task_result(task, index=index))
        return logical_id.parent / f"{logical_id.name}_v{version_no:03d}{extension}"

    def _logical_prefix_for_download_owner(self, owner: DownloadOwnerSnapshot) -> Path:
        task_id = self.safe_path_segment(owner.media_task_id, "media_task_id")
        if owner.storage_namespace:
            namespace = self._validated_namespace_path(
                owner.storage_namespace,
                project_id=str(owner.project_id),
                purpose=owner.purpose,
            )
            return namespace / task_id
        if owner.style_bible_revision_id:
            style_bible_revision_id = self.safe_path_segment(
                owner.style_bible_revision_id, "style_bible_revision_id"
            )
            entity_id = self.safe_path_segment(owner.entity_id, "entity_id")
            project_id = self.safe_path_segment(str(owner.project_id), "project_id")
            return (
                Path("project")
                / project_id
                / "style_bible"
                / style_bible_revision_id
                / self._entity_plural(owner.entity_type)
                / entity_id
                / "candidates"
                / task_id
            )
        return Path("media") / self.safe_path_segment(owner.batch_id, "batch_id") / task_id

    def _logical_id_for_download_owner(self, owner: DownloadOwnerSnapshot, *, index: int) -> str:
        return (self._logical_prefix_for_download_owner(owner) / f"result_{index:03d}").as_posix()

    def _storage_uri_for_download_owner(
        self,
        owner: DownloadOwnerSnapshot,
        *,
        index: int,
        version_no: int,
        extension: str,
    ) -> Path:
        logical_id = Path(self._logical_id_for_download_owner(owner, index=index))
        return logical_id.parent / f"{logical_id.name}_v{version_no:03d}{extension}"

    def _artifact_root(self) -> Path:
        configured = Path(self.settings.artifact_root)
        root = configured if configured.is_absolute() else (self.settings.root_dir / configured)
        resolved = root.resolve()
        resolved.mkdir(parents=True, exist_ok=True)
        return resolved

    def _new_temp_path(self, media_task_id: str, result_index: int) -> Path:
        temp_root = self._artifact_root() / ".tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        prefix = f"{self.safe_path_segment(media_task_id, 'media_task_id')}_{result_index:03d}_"
        descriptor, raw_path = tempfile.mkstemp(prefix=prefix, suffix=".part", dir=temp_root)
        os.close(descriptor)
        path = Path(raw_path)
        path.unlink()
        return path

    def _resolve_artifact_path(self, relative_path: Path) -> Path:
        root = self._artifact_root()
        full_path = (root / relative_path).resolve()
        try:
            full_path.relative_to(root)
        except ValueError as exc:
            raise AppError("MEDIA_IMAGE_REQUEST_INVALID", "Artifact path is outside artifact root.", 422, retryable=False) from exc
        return full_path

    def _build_client(self, provider_settings: dict[str, Any]) -> GLabsWebhookClient:
        return GLabsWebhookClient(
            provider_settings,
            policy=self.transport_policy,
            transport=self.transport,
        )

    def _submit_queued_task(
        self,
        media_task_id: str,
        *,
        provider_settings: dict[str, Any],
        client: GLabsWebhookClient,
    ) -> bool:
        now = utc_now_iso()
        claimed = self.tasks.claim_submit(
            media_task_id,
            max_parallel_tasks=int(provider_settings["max_parallel_tasks"]),
            active_statuses=PROVIDER_ACTIVE_STATUSES,
            updated_at=now,
        )
        self.session.expire_all()
        if not claimed:
            return False
        task = self._get_task_required(media_task_id)
        provider_prompt = self._build_provider_prompt(task.prompt, task.negative_prompt)
        provider_references = self._provider_reference_images(task)
        provider_payload = {
            "prompt": provider_prompt,
            "model": task.model,
            "aspect_ratio": task.aspect_ratio,
            "reference_images": provider_references,
            "upscale": list(task.upscale_json),
        }
        reference_tags = [
            str(item.get("reference_tag"))
            for item in (task.reference_images_json or [])
            if isinstance(item, dict) and isinstance(item.get("reference_tag"), str)
        ]
        metadata = dict(task.metadata_json or {})
        request_body_size = (
            len(provider_prompt.encode("utf-8"))
            + sum(
                len(str(item.get("name", "")).encode("utf-8"))
                + len(str(item.get("data", "")).encode("utf-8"))
                for item in provider_references
            )
            + sum(len(str(item).encode("utf-8")) for item in task.upscale_json)
            + len(str(task.model).encode("utf-8"))
            + len(str(task.aspect_ratio).encode("utf-8"))
        )
        logger.info(
            "Media provider image submit operation_id=%s scene_id=%s beat_id=%s model=%s aspect_ratio=%s "
            "reference_count=%s reference_tags=%s prompt_length=%s negative_prompt_length=%s request_body_size=%s",
            metadata.get("scene_asset_operation_id"),
            metadata.get("scene_id"),
            task.entity_id,
            task.model,
            task.aspect_ratio,
            len(provider_references),
            reference_tags,
            len(task.prompt or ""),
            len(task.negative_prompt or ""),
            request_body_size,
        )
        try:
            provider_response = client.submit_image(provider_payload)
            accepted_at = utc_now_iso()
            task.external_task_id = provider_response["task_id"]
            task.provider_status = provider_response["status"]
            task.provider_submitted_at = accepted_at
            task.provider_error_code = None
            task.error_code = None
            task.error = None
            task.error_detail = None
            task.updated_at = accepted_at
            self._touch_batch(task.batch_id, accepted_at)
            self.session.commit()
        except AppError as exc:
            safe_details = sanitize_diagnostic_value(dict(exc.details or {}), max_string_chars=1024)
            logger.warning(
                "Media provider image submit failed operation_id=%s scene_id=%s beat_id=%s error_code=%s details=%s",
                metadata.get("scene_asset_operation_id"),
                metadata.get("scene_id"),
                task.entity_id,
                exc.error_code,
                safe_details,
            )
            self._mark_submit_failure(
                task,
                exc,
                configured_api_key=str(provider_settings.get("api_key", "") or ""),
            )
            self.session.commit()
        except Exception as exc:
            api_key = str(provider_settings.get("api_key", "") or "")
            self._mark_submit_failure(
                task,
                AppError(
                    "MEDIA_PROVIDER_UPSTREAM_ERROR",
                    "Media provider submit failed.",
                    502,
                    retryable=False,
                    details={
                        "reason": sanitize_media_provider_value(
                            str(exc),
                            configured_api_key=api_key,
                            max_string_chars=512,
                        )
                    },
                ),
                configured_api_key=api_key,
            )
            self.session.commit()
        return True

    def _mark_submit_failure(self, task: MediaTask, exc: AppError, *, configured_api_key: str) -> None:
        now = utc_now_iso()
        task.status = "failed"
        raw_provider_error_code = self._detail_value(exc, "provider_error_code")
        if raw_provider_error_code is None:
            raw_provider_error_code = self._detail_value(exc, "status_code")
        task.provider_error_code = normalize_provider_error_code(
            raw_provider_error_code,
            configured_api_key=configured_api_key,
        )
        task.error_code = exc.error_code
        task.error = self._safe_provider_text(
            exc.message,
            "Media provider submit failed.",
            configured_api_key=configured_api_key,
        )
        task.error_detail = self._error_detail_from_exception(exc, configured_api_key=configured_api_key)
        task.updated_at = now
        task.completed_at = now
        task.provider_submitted_at = None
        self._touch_batch(task.batch_id, now)

    @staticmethod
    def _normalize_result_urls(result_urls: list[Any], *, configured_api_key: str) -> list[str]:
        if not isinstance(result_urls, list) or not result_urls:
            raise AppError(
                "MEDIA_RESULT_DOWNLOAD_FAILED",
                "Provider completed without result URLs.",
                502,
                retryable=False,
            )
        if len(result_urls) > 4:
            raise AppError(
                "MEDIA_RESULT_DOWNLOAD_FAILED",
                "Provider returned more than four result URLs.",
                502,
                retryable=False,
            )
        normalized: list[str] = []
        seen: set[str] = set()
        for raw_url in result_urls:
            if not isinstance(raw_url, str) or not raw_url.strip():
                raise AppError(
                    "MEDIA_RESULT_DOWNLOAD_FAILED",
                    "Provider returned an invalid result URL.",
                    502,
                    retryable=False,
                )
            if configured_api_key and configured_api_key in raw_url:
                raise AppError(
                    "MEDIA_RESULT_DOWNLOAD_FAILED",
                    "Provider result URL contains configured credentials.",
                    502,
                    retryable=False,
                )
            GLabsWebhookClient.validate_result_url(raw_url)
            if raw_url in seen:
                raise AppError(
                    "MEDIA_RESULT_DOWNLOAD_FAILED",
                    "Provider returned duplicate result URLs.",
                    502,
                    retryable=False,
                )
            seen.add(raw_url)
            normalized.append(raw_url)
        return normalized

    @staticmethod
    def _build_provider_prompt(prompt: str, negative_prompt: str | None) -> str:
        clean_prompt = str(prompt).strip()
        clean_negative_prompt = str(negative_prompt or "").strip()
        if clean_negative_prompt == "":
            return clean_prompt
        return f"{clean_prompt}\n\nAvoid: {clean_negative_prompt}"

    @staticmethod
    def _add_seconds(value: str, seconds: int) -> str:
        current = datetime.fromisoformat(value)
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        return (current + timedelta(seconds=seconds)).isoformat()

    def _release_poll_claim_or_raise(
        self,
        media_task_id: str,
        claim_token: str,
        *,
        configured_api_key: str,
    ) -> None:
        self.session.rollback()
        try:
            self.tasks.release_provider_poll_claim(
                media_task_id,
                claim_token=claim_token,
                updated_at=utc_now_iso(),
            )
        except Exception as exc:
            self.session.rollback()
            raise self._stable_poll_exception(
                exc,
                configured_api_key=configured_api_key,
                message="Media provider poll claim release failed.",
            ) from exc

    @staticmethod
    def _stable_poll_exception(
        exc: Exception,
        *,
        configured_api_key: str,
        message: str,
    ) -> AppError:
        reason = sanitize_media_provider_value(
            str(exc),
            configured_api_key=configured_api_key,
            max_string_chars=512,
        )
        return AppError(
            "MEDIA_PROVIDER_UPSTREAM_ERROR",
            message,
            502,
            retryable=False,
            details={"reason": reason},
        )

    @staticmethod
    def _error_detail_from_exception(exc: AppError, *, configured_api_key: str) -> str:
        details = exc.details if isinstance(exc.details, dict) else {}
        safe = sanitize_media_provider_value(
            details,
            configured_api_key=configured_api_key,
            max_string_chars=4096,
        )
        if safe:
            return json.dumps(safe, ensure_ascii=False, sort_keys=True)
        fallback = sanitize_media_provider_value(
            exc.message,
            configured_api_key=configured_api_key,
            max_string_chars=2048,
        )
        return str(fallback)

    @staticmethod
    def _detail_value(exc: AppError, key: str) -> Any:
        return exc.details.get(key) if isinstance(exc.details, dict) else None

    @staticmethod
    def _safe_provider_text(value: Any, fallback: str, *, configured_api_key: str) -> str:
        if not isinstance(value, str) or not value.strip():
            return fallback
        safe = sanitize_media_provider_value(
            value,
            configured_api_key=configured_api_key,
            max_string_chars=2048,
        )
        return safe if isinstance(safe, str) and safe else fallback

    @staticmethod
    def _entity_plural(entity_type: str) -> str:
        mapping = {
            "character": "characters",
            "location": "locations",
            "prop": "recurring_props",
            "recurring_visual_subject": "recurring_visual_subjects",
        }
        if entity_type not in mapping:
            raise AppError("MEDIA_IMAGE_REQUEST_INVALID", "Unsupported entity_type for storage path.", 422, retryable=False)
        return mapping[entity_type]

    @staticmethod
    def safe_path_segment(value: str, field_name: str) -> str:
        candidate = str(value or "")
        if candidate == "":
            raise AppError("MEDIA_IMAGE_REQUEST_INVALID", f"{field_name} must not be empty.", 422, retryable=False)
        if candidate in {".", ".."}:
            raise AppError("MEDIA_IMAGE_REQUEST_INVALID", f"{field_name} contains an invalid path segment.", 422, retryable=False)
        decoded = unquote(candidate)
        if decoded in {".", ".."} or "/" in decoded or "\\" in decoded:
            raise AppError("MEDIA_IMAGE_REQUEST_INVALID", f"{field_name} contains an invalid path segment.", 422, retryable=False)
        if not SAFE_PATH_SEGMENT_PATTERN.fullmatch(candidate):
            raise AppError("MEDIA_IMAGE_REQUEST_INVALID", f"{field_name} must match ^[A-Za-z0-9_-]+$.", 422, retryable=False)
        return candidate

    def _touch_batch(self, batch_id: str, updated_at: str) -> None:
        batch = self.batches.get(batch_id)
        if batch is not None:
            batch.updated_at = updated_at

    def _touch_batch_and_commit(self, batch_id: str, updated_at: str) -> None:
        self.session.expire_all()
        self._touch_batch(batch_id, updated_at)
        self.session.commit()

    def _get_task_required(self, media_task_id: str) -> MediaTask:
        task = self.tasks.get(media_task_id)
        if task is None:
            raise AppError("MEDIA_TASK_NOT_FOUND", f"Media task '{media_task_id}' was not found.", 404, retryable=False)
        return task

    @staticmethod
    def _cleanup_paths(paths: list[Path]) -> None:
        for path in paths:
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                pass
