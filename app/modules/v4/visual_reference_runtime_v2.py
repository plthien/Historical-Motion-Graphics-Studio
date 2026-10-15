from __future__ import annotations

from copy import deepcopy
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.errors import AppError
from app.core.ids import generate_id
from app.core.time import utc_now_iso
from app.db.models import Project
from app.db.visual_reference_anchor_state import VisualReferenceAnchorState
from app.db.visual_reference_operation_anchor import VisualReferenceOperationAnchor
from app.db.visual_reference_operation_task import VisualReferenceOperationTask
from app.db.visual_reference_working_operation import VisualReferenceWorkingOperation
from app.modules.v4.scene_asset_manifest import canonical_hash
from app.modules.v4.style_bible_consumer_loader import StyleBibleConsumerLoaderV4
from app.modules.v4.visual_reference_prompt_compiler import build_visual_reference_inventory
from app.modules.v4.visual_reference_prompt_revision import VisualReferencePromptRevisionService
from app.modules.v4.visual_reference_working_state import VisualReferenceWorkingStateService
from app.repositories import ArtifactRepository, ProjectRepository, SettingsRepository
from app.repositories.visual_reference_working import VisualReferenceWorkingRepository
from app.services.artifacts import ArtifactService
from app.services.media_settings import MediaProviderSettingsService

DEFAULT_ASPECT_RATIOS_BY_ANCHOR_TYPE = {
    "character": "1:1",
    "location": "16:9",
    "prop": "1:1",
    "recurring_visual_subject": "1:1",
}
_ALLOWED_ANCHOR_TYPES = frozenset(DEFAULT_ASPECT_RATIOS_BY_ANCHOR_TYPE)
_ALLOWED_OPERATION_TYPES = {"generate_all", "generate_anchor", "regenerate_anchor", "retry_failed"}
_PROVIDER_SNAPSHOT_FIELDS = (
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
)
_STYLE_FIELDS = ("appearance", "avoid")


def _has_non_empty_string_array(value: Any) -> bool:
    return isinstance(value, list) and any(isinstance(item, str) and item.strip() for item in value)


def _validate_category_local_entity_styles(style_bible: dict[str, Any], subjects: list[dict[str, Any]]) -> None:
    used_categories = sorted({str(item["anchor_type"]) for item in subjects})
    snapshot = style_bible.get("visual_style_snapshot")
    entity_styles = snapshot.get("entity_styles") if isinstance(snapshot, dict) else None
    for category in used_categories:
        style = entity_styles.get(category) if isinstance(entity_styles, dict) else None
        missing_fields = [
            field
            for field in _STYLE_FIELDS
            if not isinstance(style, dict) or not _has_non_empty_string_array(style.get(field))
        ]
        if missing_fields:
            raise AppError(
                "STYLE_BIBLE_ENTITY_STYLE_MISSING",
                "Style Bible entity style is required before visual-reference generation.",
                409,
                retryable=False,
                details={"category": category, "missing_fields": missing_fields},
            )


def _normalize_aspect_ratios(request: dict[str, Any]) -> dict[str, str]:
    ratios = dict(DEFAULT_ASPECT_RATIOS_BY_ANCHOR_TYPE)
    legacy = request.get("aspect_ratio")
    if legacy is not None:
        ratios = {anchor_type: str(legacy) for anchor_type in ratios}
    mapped = request.get("aspect_ratios_by_anchor_type")
    if isinstance(mapped, dict):
        for anchor_type, ratio in mapped.items():
            if anchor_type not in _ALLOWED_ANCHOR_TYPES:
                raise AppError(
                    "VISUAL_REFERENCE_ANCHOR_TYPE_INVALID",
                    "Visual Reference anchor type is invalid.",
                    422,
                    retryable=False,
                    details={"anchor_type": anchor_type},
                )
            ratios[str(anchor_type)] = str(ratio)
    allowed_ratios = {"1:1", "3:4", "4:3", "9:16", "16:9"}
    if any(value not in allowed_ratios for value in ratios.values()):
        raise AppError(
            "VISUAL_REFERENCE_ASPECT_RATIO_INVALID",
            "Visual Reference aspect ratio is invalid.",
            422,
            retryable=False,
        )
    return ratios


class VisualReferenceRuntimeV2:
    """Durable Visual Reference V2 operation submission/status/cancel owner.

    This service performs no provider I/O and owns no Job/RQ state. It freezes
    prompt/provider/options lineage into DB-backed operations; the standalone
    coordinator and shared provider executor advance that durable work later.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.projects = ProjectRepository(session)
        self.artifacts = ArtifactService(session, ArtifactRepository(session))
        self.style_loader = StyleBibleConsumerLoaderV4(self.artifacts)
        self.media_settings = MediaProviderSettingsService(session, SettingsRepository(session))
        self.prompt_revisions = VisualReferencePromptRevisionService(session)
        self.repo = VisualReferenceWorkingRepository(session)

    def submit_generation(
        self,
        project_id: str,
        request: dict[str, Any],
        *,
        operation_type: str = "generate_all",
        target_anchor_type: str | None = None,
        target_anchor_id: str | None = None,
    ) -> dict[str, Any]:
        if operation_type not in _ALLOWED_OPERATION_TYPES:
            raise AppError(
                "VISUAL_REFERENCE_OPERATION_TYPE_INVALID",
                "Visual Reference operation type is invalid.",
                422,
                retryable=False,
                details={"operation_type": operation_type},
            )
        candidate_count = request.get("candidate_count", 1)
        if type(candidate_count) is not int or not 1 <= candidate_count <= 5:
            raise AppError(
                "VISUAL_REFERENCE_CANDIDATE_COUNT_INVALID",
                "candidate_count must be between 1 and 5.",
                422,
                retryable=False,
            )
        exact = operation_type in {"generate_anchor", "regenerate_anchor"}
        if exact and (
            target_anchor_type not in _ALLOWED_ANCHOR_TYPES
            or not isinstance(target_anchor_id, str)
            or not target_anchor_id
        ):
            raise AppError(
                "VISUAL_REFERENCE_ANCHOR_NOT_FOUND",
                "An exact Visual Reference anchor target is required.",
                404,
                retryable=False,
            )

        self.session.execute(text("BEGIN IMMEDIATE"))
        try:
            project, style, inventory = self._ready_sources(
                project_id,
                str(request.get("style_bible_artifact_id") or ""),
            )
            expected_set = request.get("expected_visual_reference_set_artifact_id")
            if project.style_bible_visual_reference_set_artifact_id != expected_set:
                raise AppError(
                    "VISUAL_REFERENCE_SET_REVISION_CONFLICT",
                    "The expected published Visual Reference Set is no longer current.",
                    409,
                    retryable=False,
                    details={
                        "expected_visual_reference_set_artifact_id": expected_set,
                        "current_visual_reference_set_artifact_id": project.style_bible_visual_reference_set_artifact_id,
                    },
                )
            ratios = _normalize_aspect_ratios(request)
            provider_snapshot = self._provider_snapshot()
            now = utc_now_iso()

            current_revisions: dict[tuple[str, str], Any] = {}
            inventory_by_key: dict[tuple[str, str], dict[str, Any]] = {}
            for item in inventory:
                key = (str(item["anchor_type"]), str(item["anchor_id"]))
                if key in inventory_by_key:
                    raise AppError(
                        "VISUAL_REFERENCE_INVENTORY_INVALID",
                        "Style Bible Visual Reference inventory contains a duplicate anchor.",
                        409,
                        retryable=False,
                        details={"anchor_type": key[0], "anchor_id": key[1]},
                    )
                inventory_by_key[key] = item
                current_revisions[key] = self.prompt_revisions.ensure_compiled(
                    project_id=project.project_id,
                    style_bible_artifact_id=str(project.style_bible_artifact_id),
                    anchor=item,
                    now_iso=now,
                )
            self.session.flush()

            scheduled, blocked = self._schedule(
                project=project,
                operation_type=operation_type,
                inventory_by_key=inventory_by_key,
                target_anchor_type=target_anchor_type,
                target_anchor_id=target_anchor_id,
            )
            if not scheduled:
                raise AppError(
                    "VISUAL_REFERENCE_NO_ELIGIBLE_ANCHORS",
                    "No Visual Reference anchors are eligible for this operation.",
                    409,
                    retryable=False,
                    details={
                        "operation_type": operation_type,
                        "blocked_anchors": [
                            {"anchor_type": key[0], "anchor_id": key[1]} for key in blocked
                        ],
                    },
                )
            _validate_category_local_entity_styles(
                style,
                [inventory_by_key[key] for key in scheduled],
            )

            operation_id = generate_id("visual_reference_operation")
            operation = VisualReferenceWorkingOperation(
                operation_id=operation_id,
                project_id=project.project_id,
                operation_type=operation_type,
                status="queued",
                source_style_bible_artifact_id=str(project.style_bible_artifact_id),
                expected_visual_reference_set_artifact_id=expected_set,
                provider_settings_snapshot_json=provider_snapshot,
                provider_settings_snapshot_hash=canonical_hash(provider_snapshot),
                total_anchor_count=len(scheduled),
                completed_count=0,
                failed_count=0,
                cancelled_count=0,
                next_reconcile_at=now,
                last_progress_at=now,
                reconcile_owner=None,
                reconcile_token=None,
                reconcile_lease_expires_at=None,
                cancel_requested_at=None,
                cancelled_at=None,
                request_json={
                    "schema_version": "2.0",
                    "candidate_count": candidate_count,
                    "aspect_ratios_by_anchor_type": ratios,
                    "scheduled_anchors": [
                        {"anchor_type": key[0], "anchor_id": key[1]} for key in scheduled
                    ],
                    "blocked_anchors": [
                        {"anchor_type": key[0], "anchor_id": key[1]} for key in blocked
                    ],
                },
                result_json={},
                error_code=None,
                error_message=None,
                error_details_json=None,
                created_at=now,
                updated_at=now,
                completed_at=None,
            )
            self.session.add(operation)
            self.session.flush()

            for key in scheduled:
                anchor_type, anchor_id = key
                item = inventory_by_key[key]
                revision = current_revisions[key]
                operation_anchor_id = generate_id("visual_reference_operation_anchor")
                generation_options = {"aspect_ratio": ratios[anchor_type]}
                unit = VisualReferenceOperationAnchor(
                    operation_anchor_id=operation_anchor_id,
                    operation_id=operation_id,
                    project_id=project.project_id,
                    anchor_type=anchor_type,
                    anchor_id=anchor_id,
                    version=1,
                    status="queued",
                    candidate_count=candidate_count,
                    prompt_revision_id=revision.prompt_revision_id,
                    prompt=revision.prompt,
                    negative_prompt=revision.negative_prompt,
                    generation_options_json=generation_options,
                    generation_options_hash=canonical_hash(generation_options),
                    source_definition_hash=str(item["source_definition_hash"]),
                    error_code=None,
                    error_message=None,
                    error_details_json=None,
                    created_at=now,
                    updated_at=now,
                    completed_at=None,
                )
                self.session.add(unit)
                self.session.flush()
                state = self.repo.get_anchor_state(project.project_id, anchor_type, anchor_id)
                if state is None:
                    raise AppError(
                        "VISUAL_REFERENCE_V2_STATE_NOT_FOUND",
                        "Visual Reference AnchorState disappeared during submission.",
                        409,
                        retryable=True,
                        details={"anchor_type": anchor_type, "anchor_id": anchor_id},
                    )
                expected_version = int(state.version)
                if state.active_operation_anchor_id is not None:
                    raise AppError(
                        "VISUAL_REFERENCE_V2_ANCHOR_BUSY",
                        "Visual Reference anchor became busy before submission committed.",
                        409,
                        retryable=True,
                        details={"anchor_type": anchor_type, "anchor_id": anchor_id},
                    )
                if state.current_prompt_revision_id != revision.prompt_revision_id:
                    raise AppError(
                        "VISUAL_REFERENCE_V2_PROMPT_REVISION_CONFLICT",
                        "Visual Reference prompt changed before operation ownership committed.",
                        409,
                        retryable=True,
                        details={"anchor_type": anchor_type, "anchor_id": anchor_id},
                    )
                if not self.repo.compare_and_set_anchor_state(
                    project_id=project.project_id,
                    anchor_type=anchor_type,
                    anchor_id=anchor_id,
                    expected_version=expected_version,
                    values={
                        "active_operation_anchor_id": operation_anchor_id,
                        "last_progress_at": now,
                        "last_error_json": None,
                    },
                    updated_at=now,
                ):
                    raise AppError(
                        "VISUAL_REFERENCE_V2_ANCHOR_STATE_CONFLICT",
                        "Visual Reference anchor changed before operation ownership committed.",
                        409,
                        retryable=True,
                        details={"anchor_type": anchor_type, "anchor_id": anchor_id},
                    )
                for candidate_index in range(1, candidate_count + 1):
                    self.session.add(VisualReferenceOperationTask(
                        operation_task_id=generate_id("visual_reference_operation_task"),
                        operation_anchor_id=operation_anchor_id,
                        candidate_count=candidate_count,
                        candidate_index=candidate_index,
                        status="queued",
                        attempt_no=1,
                        media_task_id=None,
                        submit_attempt_token=None,
                        submit_started_at=None,
                        next_reconcile_at=now,
                        error_code=None,
                        error_message=None,
                        error_details_json=None,
                        created_at=now,
                        updated_at=now,
                        completed_at=None,
                    ))
            self.session.flush()
            self._assert_project_lineage(project, operation)
            self.session.commit()
            return {
                "project_id": project.project_id,
                "operation_id": operation_id,
                "operation_type": operation_type,
                "status": "queued",
                "total_anchor_count": len(scheduled),
                "scheduled_anchors": [
                    {"anchor_type": key[0], "anchor_id": key[1]} for key in scheduled
                ],
                "blocked_anchors": [
                    {"anchor_type": key[0], "anchor_id": key[1]} for key in blocked
                ],
                "next_reconcile_at": now,
            }
        except IntegrityError as exc:
            self.session.rollback()
            raise AppError(
                "VISUAL_REFERENCE_V2_ANCHOR_BUSY",
                "One or more Visual Reference anchors became busy before submission committed.",
                409,
                retryable=True,
            ) from exc
        except Exception:
            self.session.rollback()
            raise

    def submit_anchor_regeneration(
        self,
        project_id: str,
        anchor_type: str,
        anchor_id: str,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        return self.submit_generation(
            project_id,
            request,
            operation_type="regenerate_anchor",
            target_anchor_type=anchor_type,
            target_anchor_id=anchor_id,
        )

    def submit_retry_failed(self, project_id: str, request: dict[str, Any]) -> dict[str, Any]:
        return self.submit_generation(project_id, request, operation_type="retry_failed")

    def get_operation_status(self, project_id: str, operation_id: str) -> dict[str, Any]:
        operation = self.repo.get_operation(operation_id)
        if operation is None or operation.project_id != project_id:
            raise AppError(
                "VISUAL_REFERENCE_OPERATION_NOT_FOUND",
                "Visual Reference V2 operation was not found.",
                404,
                retryable=False,
            )
        units = self.repo.list_operation_anchors(operation_id)
        return {
            "project_id": project_id,
            "operation_id": operation_id,
            "operation_type": str(operation.operation_type),
            "status": str(operation.status),
            "total_anchor_count": int(operation.total_anchor_count),
            "completed_count": int(operation.completed_count),
            "failed_count": int(operation.failed_count),
            "cancelled_count": int(operation.cancelled_count),
            "next_reconcile_at": operation.next_reconcile_at,
            "error_code": operation.error_code,
            "error_message": operation.error_message,
            "anchors": [
                {
                    "operation_anchor_id": unit.operation_anchor_id,
                    "anchor_type": unit.anchor_type,
                    "anchor_id": unit.anchor_id,
                    "status": unit.status,
                    "candidate_count": int(unit.candidate_count),
                }
                for unit in units
            ],
        }

    def cancel_operation(self, project_id: str, operation_id: str) -> dict[str, object]:
        operation = self.repo.get_operation(operation_id)
        if operation is None or operation.project_id != project_id:
            raise AppError(
                "VISUAL_REFERENCE_OPERATION_NOT_FOUND",
                "Visual Reference V2 operation was not found.",
                404,
                retryable=False,
            )
        try:
            result = VisualReferenceWorkingStateService(self.session).cancel_operation(
                operation_id=operation_id,
                now_iso=utc_now_iso(),
            )
            self.session.commit()
            return dict(result)
        except Exception:
            self.session.rollback()
            raise

    def _ready_sources(
        self,
        project_id: str,
        style_bible_artifact_id: str,
    ) -> tuple[Project, dict[str, Any], list[dict[str, Any]]]:
        project = self.projects.get(project_id)
        if project is None:
            raise AppError("PROJECT_NOT_FOUND", f"Project '{project_id}' was not found.", 404, retryable=False)
        if (
            project.script_artifact_id is None
            or project.script_analysis_artifact_id is None
            or project.style_bible_artifact_id is None
        ):
            raise AppError(
                "VISUAL_REFERENCE_NOT_READY",
                "Current Script, Script Analysis, and text-approved Style Bible are required.",
                409,
                retryable=False,
            )
        if project.style_bible_artifact_id != style_bible_artifact_id:
            raise AppError(
                "VISUAL_REFERENCE_STYLE_BIBLE_NOT_CURRENT",
                "The requested Style Bible is not current.",
                409,
                retryable=False,
            )
        style = self.style_loader.load_text_approved(project, style_bible_artifact_id)
        inventory = build_visual_reference_inventory(style)
        if not inventory:
            raise AppError(
                "VISUAL_REFERENCE_INVENTORY_EMPTY",
                "The current Style Bible has no approved continuity anchors requiring reference images.",
                409,
                retryable=False,
            )
        return project, style, inventory

    def _provider_snapshot(self) -> dict[str, Any]:
        backend = self.media_settings.get_settings_for_backend()
        self.media_settings._validate_runtime_ready(backend)
        public = self.media_settings.get_settings()
        snapshot = {field: deepcopy(public[field]) for field in _PROVIDER_SNAPSHOT_FIELDS if field in public}
        if not snapshot.get("provider"):
            raise AppError(
                "VISUAL_REFERENCE_PROVIDER_SETTINGS_INVALID",
                "Visual Reference provider settings are unavailable.",
                409,
                retryable=False,
            )
        snapshot.pop("api_key", None)
        return snapshot

    def _schedule(
        self,
        *,
        project: Project,
        operation_type: str,
        inventory_by_key: dict[tuple[str, str], dict[str, Any]],
        target_anchor_type: str | None,
        target_anchor_id: str | None,
    ) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        if operation_type in {"generate_anchor", "regenerate_anchor"}:
            target = (str(target_anchor_type), str(target_anchor_id))
            if target not in inventory_by_key:
                raise AppError(
                    "VISUAL_REFERENCE_ANCHOR_NOT_FOUND",
                    "The exact Style Bible Visual Reference anchor was not found.",
                    404,
                    retryable=False,
                    details={"anchor_type": target[0], "anchor_id": target[1]},
                )
            keys = [target]
        else:
            keys = list(inventory_by_key)

        scheduled: list[tuple[str, str]] = []
        blocked: list[tuple[str, str]] = []
        for key in keys:
            state = self.repo.get_anchor_state(project.project_id, key[0], key[1])
            if state is None:
                raise AppError(
                    "VISUAL_REFERENCE_V2_STATE_NOT_FOUND",
                    "Visual Reference AnchorState is missing after prompt synchronization.",
                    409,
                    retryable=True,
                    details={"anchor_type": key[0], "anchor_id": key[1]},
                )
            if state.active_operation_anchor_id is not None:
                if operation_type in {"generate_anchor", "regenerate_anchor"}:
                    raise AppError(
                        "VISUAL_REFERENCE_V2_ANCHOR_BUSY",
                        "Visual Reference anchor already has active generation.",
                        409,
                        retryable=True,
                        details={
                            "anchor_type": key[0],
                            "anchor_id": key[1],
                            "active_operation_anchor_id": state.active_operation_anchor_id,
                        },
                    )
                blocked.append(key)
                continue
            if operation_type == "retry_failed" and state.resolution_status != "failed":
                continue
            scheduled.append(key)
        return scheduled, blocked

    @staticmethod
    def _assert_project_lineage(project: Project, operation: VisualReferenceWorkingOperation) -> None:
        if project.style_bible_artifact_id != operation.source_style_bible_artifact_id:
            raise AppError(
                "VISUAL_REFERENCE_OPERATION_STALE",
                "Visual Reference operation source Style Bible changed before submission committed.",
                409,
                retryable=True,
                details={"operation_id": operation.operation_id},
            )
