from __future__ import annotations

from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.api.errors import AppError
from app.core.ids import generate_id
from app.core.time import utc_now_iso
from app.db.enums import ArtifactStatus, ArtifactType
from app.db.models import Artifact, Project
from app.db.scene_asset_beat_state import SceneAssetBeatState
from app.db.scene_asset_candidate import SceneAssetCandidate
from app.db.scene_asset_operation_beat import SceneAssetOperationBeat
from app.db.scene_asset_operation_task import SceneAssetOperationTask
from app.db.scene_asset_prompt_revision import SceneAssetPromptRevision
from app.db.scene_asset_working_operation import SceneAssetWorkingOperation
from app.modules.v4.contracts import V4ContractValidator
from app.modules.v4.production_artifact_loader import ProductionArtifactLoaderV4
from app.modules.v4.scene_asset_coordinator import SceneAssetCoordinator
from app.modules.v4.scene_asset_manifest import canonical_hash
from app.modules.v4.scene_asset_prompt_revision import SceneAssetPromptRevisionService
from app.repositories import ArtifactRepository, ProjectRepository, SettingsRepository
from app.repositories.scene_asset_working import ACTIVE_OPERATION_STATUSES, SceneAssetWorkingRepository
from app.services.artifacts import ArtifactService
from app.services.media_settings import MediaProviderSettingsService


_PROVIDER_SNAPSHOT_FIELDS = (
    "provider",
    "default_image_model",
    "default_image_aspect_ratio",
    "default_upscale",
    "poll_interval_seconds",
    "max_parallel_tasks",
    "enabled_media_kinds",
    "enabled",
)
_EXACT_OPERATION_TYPES = {"generate_beat", "retry_beat", "regenerate_beat"}
_TERMINAL_OPERATION_STATUSES = {"completed", "partial", "failed", "cancelled", "superseded"}
_TERMINAL_TASK_STATUSES = {"completed", "failed", "cancelled", "submit_outcome_unknown"}


class SceneAssetRuntimeV2:
    """Scene Assets V2 submission/status/cancel API runtime owner.

    Provider execution is intentionally absent from this service. The standalone
    coordinator is the only process that advances persisted Scene Assets work.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.projects = ProjectRepository(session)
        self.artifacts = ArtifactService(session, ArtifactRepository(session))
        self.loader = ProductionArtifactLoaderV4(self.artifacts, V4ContractValidator())
        self.media_settings = MediaProviderSettingsService(session, SettingsRepository(session))
        self.prompt_revisions = SceneAssetPromptRevisionService(session)

    # -------------------------------------------------------------- submission --

    def submit_generation(
        self,
        project_id: str,
        request: dict[str, Any],
        *,
        target_beat_id: str | None = None,
        target_mode: str | None = None,
    ) -> dict[str, Any]:
        operation_type = str(target_mode or request.get("operation_type") or "generate_all")
        if operation_type not in {
            "generate_all", "generate_section", "generate_beat", "generate_missing",
            "retry_failed", "retry_beat", "regenerate_beat", "regenerate_section",
        }:
            raise AppError("SCENE_ASSET_OPERATION_TYPE_INVALID", "Scene Asset operation type is invalid.", 422, retryable=False)
        candidate_count = request.get("candidate_count", 1)
        if type(candidate_count) is not int or not 1 <= candidate_count <= 5:
            raise AppError("SCENE_ASSET_CANDIDATE_COUNT_INVALID", "candidate_count must be between 1 and 5.", 422, retryable=False)
        section_id = request.get("section_id")
        if section_id is not None and not isinstance(section_id, str):
            raise AppError("SCENE_ASSET_SECTION_INVALID", "section_id is invalid.", 422, retryable=False)
        if operation_type in _EXACT_OPERATION_TYPES and not target_beat_id:
            raise AppError("SCENE_ASSET_BEAT_NOT_FOUND", "An exact beat target is required.", 404, retryable=False)

        self.session.execute(text("BEGIN IMMEDIATE"))
        try:
            project = self._project_required(project_id)
            _scene_plan, visual_plan, prompt_bundle = self._load_upstream(project)
            provider_snapshot = self._provider_snapshot()
            prompt_items = list(prompt_bundle.get("prompt_items", []))
            prompt_by_beat = self._exact_index(prompt_items, "Prompt Bundle")
            prompt_revision_by_beat = {
                beat_id: self._current_prompt_revision(project, item)
                for beat_id, item in prompt_by_beat.items()
            }
            visual_beats = [beat for scene in visual_plan.get("scenes", []) for beat in scene.get("beat_plans", [])]
            if [str(beat.get("beat_id")) for beat in visual_beats] != [str(item.get("beat_id")) for item in prompt_items]:
                raise AppError(
                    "SCENE_ASSET_PROMPT_BUNDLE_MISMATCH",
                    "Prompt Bundle must contain exactly one ordered prompt for every Visual Plan beat.",
                    409,
                    retryable=False,
                )
            section_by_beat: dict[str, str | None] = {}
            for scene in visual_plan.get("scenes", []):
                section = scene.get("section_id")
                for beat in scene.get("beat_plans", []):
                    section_by_beat[str(beat["beat_id"])] = str(section) if section else None

            self._ensure_beat_states(project, prompt_items)
            self.session.flush()
            scheduled, blocked, continuity_snapshots = self._schedule(
                project=project,
                operation_type=operation_type,
                prompt_items=prompt_items,
                prompt_by_beat=prompt_by_beat,
                prompt_revision_by_beat=prompt_revision_by_beat,
                section_by_beat=section_by_beat,
                section_id=section_id,
                target_beat_id=target_beat_id,
            )
            if not scheduled:
                raise AppError(
                    "SCENE_ASSET_NO_ELIGIBLE_BEATS",
                    "No Scene Asset beats are eligible for this operation.",
                    409,
                    retryable=False,
                    details={"operation_type": operation_type, "blocked_beat_ids": blocked},
                )

            now = utc_now_iso()
            operation_id = generate_id("scene_asset_operation")
            operation = SceneAssetWorkingOperation(
                operation_id=operation_id,
                project_id=project.project_id,
                operation_type=operation_type,
                status="queued",
                source_script_artifact_id=str(project.script_artifact_id),
                source_script_analysis_artifact_id=str(project.script_analysis_artifact_id),
                source_style_bible_artifact_id=str(project.style_bible_artifact_id),
                source_scene_plan_artifact_id=str(project.scene_plan_artifact_id),
                source_visual_plan_artifact_id=str(project.visual_plan_artifact_id),
                source_prompt_bundle_artifact_id=str(project.prompt_bundle_artifact_id),
                source_reference_approval_set_artifact_id=project.style_bible_visual_reference_approval_set_artifact_id,
                provider_settings_snapshot_json=provider_snapshot,
                provider_settings_snapshot_hash=canonical_hash(provider_snapshot),
                total_beat_count=len(scheduled),
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
                    "section_id": section_id,
                    "target_beat_id": target_beat_id,
                    "candidate_count": candidate_count,
                    "scheduled_beat_ids": list(scheduled),
                    "blocked_beat_ids": list(blocked),
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

            for beat_id in scheduled:
                item = prompt_by_beat[beat_id]
                revision = prompt_revision_by_beat[beat_id]
                operation_beat_id = generate_id("scene_asset_operation_beat")
                operation_beat = SceneAssetOperationBeat(
                    operation_beat_id=operation_beat_id,
                    operation_id=operation_id,
                    project_id=project.project_id,
                    scene_id=str(item["scene_id"]),
                    beat_id=beat_id,
                    version=1,
                    status="queued",
                    candidate_count=candidate_count,
                    prompt_revision_id=revision.prompt_revision_id,
                    prompt_bundle_artifact_id=revision.source_prompt_bundle_artifact_id,
                    prompt_item_id=revision.source_prompt_item_id,
                    prompt_hash=revision.source_prompt_hash,
                    image_prompt=revision.image_prompt,
                    negative_prompt=revision.negative_prompt,
                    generation_options_json=dict(item["generation_options"]),
                    generation_options_hash=canonical_hash(item["generation_options"]),
                    reference_bindings_json=list(item.get("reference_bindings", [])),
                    reference_bindings_hash=canonical_hash(item.get("reference_bindings", [])),
                    continuity_snapshot_json=continuity_snapshots.get(beat_id),
                    error_code=None,
                    error_message=None,
                    error_details_json=None,
                    created_at=now,
                    updated_at=now,
                    completed_at=None,
                )
                self.session.add(operation_beat)
                self.session.flush()
                state = self.session.get(SceneAssetBeatState, (project.project_id, beat_id))
                if state is None:
                    raise AppError("SCENE_ASSET_V2_STATE_NOT_FOUND", "Scene Asset BeatState disappeared during submission.", 409, retryable=True)
                expected_version = int(state.version)
                changed = SceneAssetWorkingRepository(self.session).compare_and_set_beat_state(
                    project_id=project.project_id,
                    beat_id=beat_id,
                    expected_version=expected_version,
                    values={"active_operation_beat_id": operation_beat_id, "last_progress_at": now, "last_error_json": None},
                    updated_at=now,
                )
                if not changed:
                    raise AppError(
                        "SCENE_ASSET_V2_BEAT_STATE_CONFLICT",
                        "Scene Asset beat changed before operation ownership committed.",
                        409,
                        retryable=True,
                        details={"beat_id": beat_id},
                    )
                for candidate_index in range(1, candidate_count + 1):
                    self.session.add(SceneAssetOperationTask(
                        operation_task_id=generate_id("scene_asset_operation_task"),
                        operation_beat_id=operation_beat_id,
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
                "total_beat_count": len(scheduled),
                "scheduled_beat_ids": scheduled,
                "blocked_beat_ids": blocked,
                "next_reconcile_at": now,
            }
        except IntegrityError as exc:
            self.session.rollback()
            raise AppError(
                "SCENE_ASSET_V2_BEAT_BUSY",
                "One or more Scene Asset beats became busy before submission committed.",
                409,
                retryable=True,
            ) from exc
        except Exception:
            self.session.rollback()
            raise

    # ------------------------------------------------------------------ status --

    def get_operation_status(self, project_id: str, operation_id: str) -> dict[str, Any]:
        operation = self.session.get(SceneAssetWorkingOperation, operation_id)
        if operation is None or operation.project_id != project_id:
            raise AppError("SCENE_ASSET_OPERATION_NOT_FOUND", "Scene Asset V2 operation was not found.", 404, retryable=False)
        repo = SceneAssetWorkingRepository(self.session)
        beats = repo.list_operation_beats(operation_id)
        beat_payloads: list[dict[str, Any]] = []
        for beat in beats:
            tasks = repo.list_tasks_for_operation_beat(beat.operation_beat_id)
            beat_payloads.append({
                "operation_beat_id": beat.operation_beat_id,
                "beat_id": beat.beat_id,
                "status": beat.status,
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
        self._project_required(project_id)
        repo = SceneAssetWorkingRepository(self.session)
        active = [op for op in repo.list_operations_for_project(project_id) if op.status in ACTIVE_OPERATION_STATUSES]
        return {
            "project_id": project_id,
            "active": bool(active),
            "active_operation_count": len(active),
            "operations": [self.get_operation_status(project_id, op.operation_id) for op in active],
        }

    def cancel_operation(self, project_id: str, operation_id: str) -> dict[str, Any]:
        operation = self.session.get(SceneAssetWorkingOperation, operation_id)
        if operation is None or operation.project_id != project_id:
            raise AppError("SCENE_ASSET_OPERATION_NOT_FOUND", "Scene Asset V2 operation was not found.", 404, retryable=False)
        self.session.rollback()
        factory = sessionmaker(bind=self.session.get_bind(), autoflush=False, autocommit=False, expire_on_commit=False)
        result = SceneAssetCoordinator(factory).cancel_operation(operation_id=operation_id)
        return {"project_id": project_id, **result}

    # --------------------------------------------------------------- planning --

    def _schedule(
        self,
        *,
        project: Project,
        operation_type: str,
        prompt_items: list[dict[str, Any]],
        prompt_by_beat: dict[str, dict[str, Any]],
        prompt_revision_by_beat: dict[str, SceneAssetPromptRevision],
        section_by_beat: dict[str, str | None],
        section_id: str | None,
        target_beat_id: str | None,
    ) -> tuple[list[str], list[str], dict[str, dict[str, Any]]]:
        if section_id is not None and section_id not in {value for value in section_by_beat.values() if value is not None}:
            raise AppError("SCENE_ASSET_SECTION_NOT_FOUND", "Selected Section is not in the current Visual Plan.", 404, retryable=False)
        if operation_type in _EXACT_OPERATION_TYPES and target_beat_id not in prompt_by_beat:
            raise AppError("SCENE_ASSET_BEAT_NOT_FOUND", "Selected Scene Asset beat was not found.", 404, retryable=False)

        scheduled: list[str] = []
        blocked: list[str] = []
        continuity: dict[str, dict[str, Any]] = {}
        for item in prompt_items:
            beat_id = str(item["beat_id"])
            if operation_type in _EXACT_OPERATION_TYPES and beat_id != target_beat_id:
                continue
            if operation_type in {"generate_section", "regenerate_section", "retry_failed"} and section_id is not None:
                if section_by_beat.get(beat_id) != section_id:
                    continue
            state = self.session.get(SceneAssetBeatState, (project.project_id, beat_id))
            assert state is not None
            if state.active_operation_beat_id is not None:
                if operation_type in _EXACT_OPERATION_TYPES:
                    raise AppError("SCENE_ASSET_V2_BEAT_BUSY", "Selected Scene Asset beat is already active.", 409, retryable=True, details={"beat_id": beat_id})
                continue
            if not self._operation_wants_state(
                operation_type,
                state,
                prompt_revision_by_beat[beat_id],
            ):
                continue
            requirement = item.get("continuity_reference_requirement")
            if requirement is not None:
                snapshot = self._approved_continuity_snapshot(project, requirement)
                if snapshot is None:
                    blocked.append(beat_id)
                    if operation_type in _EXACT_OPERATION_TYPES:
                        raise AppError(
                            "SCENE_ASSET_CONTINUITY_NOT_READY",
                            "Previous-beat approved continuity source is required before generation.",
                            409,
                            retryable=False,
                            details={"beat_id": beat_id, "source_beat_id": requirement.get("source_beat_id")},
                        )
                    continue
                continuity[beat_id] = snapshot
            scheduled.append(beat_id)
        return scheduled, blocked, continuity

    def _operation_wants_state(
        self,
        operation_type: str,
        state: SceneAssetBeatState,
        prompt_revision: SceneAssetPromptRevision,
    ) -> bool:
        if state.resolution_status == "approved" or state.review_status == "approved":
            return False
        reviewable = self._has_current_reviewable_candidate(state, prompt_revision)
        if operation_type in {"regenerate_beat", "regenerate_section"}:
            return reviewable
        if operation_type in {"retry_failed", "retry_beat"}:
            return state.resolution_status == "failed"
        if operation_type == "generate_missing":
            return state.resolution_status in {"not_generated", "failed"} and not reviewable
        return state.resolution_status == "not_generated" and not reviewable

    def _has_current_reviewable_candidate(
        self,
        state: SceneAssetBeatState,
        prompt_revision: SceneAssetPromptRevision,
    ) -> bool:
        candidate_id = self.session.scalar(
            select(SceneAssetCandidate.candidate_id)
            .join(Artifact, Artifact.artifact_id == SceneAssetCandidate.artifact_id)
            .where(
                SceneAssetCandidate.project_id == state.project_id,
                SceneAssetCandidate.beat_id == state.beat_id,
                SceneAssetCandidate.source_prompt_revision_id == prompt_revision.prompt_revision_id,
                SceneAssetCandidate.source_prompt_bundle_artifact_id == prompt_revision.source_prompt_bundle_artifact_id,
                SceneAssetCandidate.source_prompt_item_id == prompt_revision.source_prompt_item_id,
                SceneAssetCandidate.source_prompt_hash == prompt_revision.source_prompt_hash,
                Artifact.status == ArtifactStatus.READY,
                Artifact.deleted_at.is_(None),
                Artifact.artifact_type == ArtifactType.IMAGE,
                Artifact.sha256.is_not(None),
                Artifact.sha256 != "",
                Artifact.mime_type.is_not(None),
                Artifact.mime_type != "",
            )
            .limit(1)
        )
        return candidate_id is not None

    def _approved_continuity_snapshot(self, project: Project, requirement: dict[str, Any]) -> dict[str, Any] | None:
        source_beat_id = str(requirement.get("source_beat_id") or "")
        source = self.session.get(SceneAssetBeatState, (project.project_id, source_beat_id))
        if source is None or source.resolution_status != "approved" or source.review_status != "approved" or not source.selected_candidate_id:
            return None
        candidate = self.session.get(SceneAssetCandidate, source.selected_candidate_id)
        if candidate is None or candidate.project_id != project.project_id or candidate.beat_id != source_beat_id:
            return None
        artifact = self.session.get(Artifact, candidate.artifact_id)
        if (
            artifact is None or artifact.status != ArtifactStatus.READY or artifact.deleted_at is not None
            or artifact.artifact_type != ArtifactType.IMAGE or not artifact.sha256 or not artifact.mime_type
        ):
            return None
        return {
            "relation": str(requirement.get("relation")),
            "source_beat_id": source_beat_id,
            "source_asset_artifact_id": artifact.artifact_id,
            "source_asset_sha256": artifact.sha256,
            "mime_type": artifact.mime_type,
            "source_kind": "approved",
        }

    def _ensure_beat_states(self, project: Project, prompt_items: list[dict[str, Any]]) -> None:
        now = utc_now_iso()
        for item in prompt_items:
            beat_id = str(item["beat_id"])
            scene_id = str(item["scene_id"])
            state = self.session.get(SceneAssetBeatState, (project.project_id, beat_id))
            if state is None:
                self.session.add(SceneAssetBeatState(
                    project_id=project.project_id,
                    beat_id=beat_id,
                    scene_id=scene_id,
                    version=1,
                    resolution_status="not_generated",
                    review_status="unreviewed",
                    current_draft_candidate_id=None,
                    selected_candidate_id=None,
                    active_operation_beat_id=None,
                    continuity_source_scene_id=None,
                    continuity_source_beat_id=None,
                    continuity_source_candidate_id=None,
                    last_error_json=None,
                    last_progress_at=None,
                    created_at=now,
                    updated_at=now,
                ))
            elif state.scene_id != scene_id:
                raise AppError(
                    "SCENE_ASSET_V2_STATE_LINEAGE_STALE",
                    "Existing Scene Asset BeatState no longer matches the current Prompt Bundle scene identity.",
                    409,
                    retryable=False,
                    details={"beat_id": beat_id, "stored_scene_id": state.scene_id, "current_scene_id": scene_id},
                )

    def _current_prompt_revision(self, project: Project, item: dict[str, Any]) -> SceneAssetPromptRevision:
        beat_id = str(item["beat_id"])
        revision = self.prompt_revisions.current(project.project_id, beat_id)
        valid = (
            revision.scene_id == str(item["scene_id"])
            and revision.source_prompt_item_id == str(item["prompt_item_id"])
            and revision.source_prompt_hash == str(item["prompt_hash"])
            and revision.image_prompt == str(item["image_prompt"])
            and revision.negative_prompt == str(item["negative_prompt"])
        )
        if not valid:
            raise AppError(
                "SCENE_ASSET_PROMPT_REVISION_STALE",
                "Current beat PromptRevision does not match the current Prompt Bundle item.",
                409,
                retryable=False,
                details={
                    "project_id": project.project_id,
                    "beat_id": beat_id,
                    "prompt_revision_id": revision.prompt_revision_id,
                    "current_prompt_bundle_artifact_id": project.prompt_bundle_artifact_id,
                },
            )
        return revision

    # --------------------------------------------------------------- upstream --

    def _project_required(self, project_id: str) -> Project:
        project = self.projects.get(project_id)
        if project is None:
            raise AppError("PROJECT_NOT_FOUND", f"Project '{project_id}' was not found.", 404, retryable=False)
        return project

    def _load_upstream(self, project: Project) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        if not project.scene_plan_artifact_id or not project.visual_plan_artifact_id or not project.prompt_bundle_artifact_id:
            raise AppError("SCENE_ASSET_NOT_READY", "Approved Scene Plan, Visual Plan, and Prompt Bundle are required.", 409, retryable=False)
        scene_plan = self.loader.load_approved_scene_plan(project)
        visual_plan = self.loader.load_approved_visual_plan(project)
        prompt_bundle = self.loader.load_approved_prompt_bundle(project)
        if prompt_bundle.get("source_visual_reference_approval_set_artifact_id") != project.style_bible_visual_reference_approval_set_artifact_id:
            raise AppError("SCENE_ASSET_UPSTREAM_STALE", "Approved Prompt Bundle reference lineage is stale.", 409, retryable=False)
        return scene_plan, visual_plan, prompt_bundle

    def _provider_snapshot(self) -> dict[str, Any]:
        current = self.media_settings.get_settings()
        readiness = self.media_settings.readiness(current)
        if readiness.get("status") != "ready":
            raise AppError("MEDIA_PROVIDER_CONFIG_INVALID", "Media provider is not ready.", 409, retryable=False, details={"issues": readiness.get("issues", [])})
        return {key: current[key] for key in _PROVIDER_SNAPSHOT_FIELDS if key in current}

    @staticmethod
    def _exact_index(items: list[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for item in items:
            beat_id = str(item.get("beat_id") or "")
            if not beat_id or beat_id in result:
                raise AppError("SCENE_ASSET_PROMPT_BUNDLE_MISMATCH", f"{label} beat IDs must be unique.", 409, retryable=False)
            result[beat_id] = item
        return result

    @staticmethod
    def _assert_project_lineage(project: Project, operation: SceneAssetWorkingOperation) -> None:
        expected = (
            project.script_artifact_id,
            project.script_analysis_artifact_id,
            project.style_bible_artifact_id,
            project.scene_plan_artifact_id,
            project.visual_plan_artifact_id,
            project.prompt_bundle_artifact_id,
            project.style_bible_visual_reference_approval_set_artifact_id,
        )
        frozen = (
            operation.source_script_artifact_id,
            operation.source_script_analysis_artifact_id,
            operation.source_style_bible_artifact_id,
            operation.source_scene_plan_artifact_id,
            operation.source_visual_plan_artifact_id,
            operation.source_prompt_bundle_artifact_id,
            operation.source_reference_approval_set_artifact_id,
        )
        if expected != frozen:
            raise AppError("SCENE_ASSET_UPSTREAM_STALE", "Project production lineage changed during Scene Asset submission.", 409, retryable=True)
