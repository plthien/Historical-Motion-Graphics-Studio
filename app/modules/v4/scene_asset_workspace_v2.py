from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api.errors import AppError
from app.api.scene_asset_manifest_v2_schemas import SceneAssetPublishedManifestV2
from app.api.scene_asset_workspace_v2_schemas import (
    SceneAssetV2BeatResponse,
    SceneAssetV2MutationResponse,
    SceneAssetV2PublishResponse,
    SceneAssetV2WorkspaceResponse,
)
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
from app.modules.v4.invalidation import ProjectPipelineInvalidationService
from app.modules.v4.production_artifact_loader import ProductionArtifactLoaderV4
from app.modules.v4.scene_asset_continuity import SceneAssetContinuityInvalidationService
from app.modules.v4.scene_asset_published_manifest_v2 import SceneAssetPublishedManifestV2Validator
from app.modules.v4.scene_asset_prompt_revision import SceneAssetPromptRevisionService
from app.repositories import ArtifactRepository, ProjectRepository
from app.services.artifacts import (
    ArtifactService,
    StagedArtifact,
    cleanup_staged_artifact_safely,
    rollback_safely,
)


_ACTIVE_OPERATION_STATUSES = {"queued", "running", "finalizing"}
_ACTIVE_BEAT_STATUSES = {"queued", "running"}


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")

def _manifest_content_identity(value: dict[str, Any]) -> dict[str, Any]:
    identity = deepcopy(value)
    identity.pop("scene_asset_manifest_revision_id", None)
    identity.pop("parent_scene_asset_manifest_revision_id", None)
    return identity


def _manifest_content_fingerprint(value: dict[str, Any]) -> str:
    canonical = json.dumps(_manifest_content_identity(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()

class SceneAssetWorkspaceV2Service:
    """Shared Scene Assets V2 workspace projection/publication base.

    Live state comes only from the V2 working graph. The Project Manifest pointer
    is a last-published immutable snapshot and is never used as a mutation/CAS
    token. Route-facing upload/review mutations live only in the PromptRevision-
    aware runtime subclass.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.projects = ProjectRepository(session)
        self.artifact_rows = ArtifactRepository(session)
        self.artifacts = ArtifactService(session, self.artifact_rows)
        self.loader = ProductionArtifactLoaderV4(self.artifacts)
        self.continuity = SceneAssetContinuityInvalidationService(session)
        self.prompt_revisions = SceneAssetPromptRevisionService(session)

    def workspace(self, project_id: str) -> dict[str, Any]:
        project = self._project_required(project_id)
        scene_plan, visual_plan, prompt_bundle = self._load_upstream(project)
        payload = self._workspace_payload(project, scene_plan, visual_plan, prompt_bundle)
        return SceneAssetV2WorkspaceResponse.model_validate(payload).model_dump(mode="json")

    def beat(self, project_id: str, beat_id: str) -> dict[str, Any]:
        project = self._project_required(project_id)
        scene_plan, visual_plan, prompt_bundle = self._load_upstream(project)
        workspace = self._workspace_payload(project, scene_plan, visual_plan, prompt_bundle)
        matches = [beat for scene in workspace["scenes"] for beat in scene["beats"] if beat["beat_id"] == beat_id]
        if len(matches) != 1:
            raise AppError("SCENE_ASSET_BEAT_NOT_FOUND", "Scene Asset beat was not found.", 404, retryable=False, details={"beat_id": beat_id})
        return SceneAssetV2BeatResponse.model_validate({"project_id": project_id, "published_manifest_artifact_id": workspace["published_manifest_artifact_id"], "published_manifest_status": workspace["published_manifest_status"], "manifest_state": workspace["manifest_state"], "beat": matches[0], "actions": workspace["actions"]}).model_dump(mode="json")

    def publish_approved_manifest(self, project_id: str) -> dict[str, Any]:
        staged: StagedArtifact | None = None
        try:
            self.session.execute(text("BEGIN IMMEDIATE"))
            project = self._project_required(project_id)
            scene_plan, visual_plan, prompt_bundle = self._load_upstream(project)
            if self._active_operation_beat_ids(project_id) or self._owned_beat_state_ids(project_id):
                raise AppError("SCENE_ASSET_GENERATION_ACTIVE", "Scene Asset Manifest cannot be published while any beat is owned by generation.", 409, retryable=True)

            previous_id = project.scene_asset_manifest_artifact_id
            comparison_payload = self._build_published_manifest(
                project,
                scene_plan,
                visual_plan,
                prompt_bundle,
                manifest_id="artifact_000000000000",
                parent_manifest_id=previous_id,
            )
            comparison_payload = SceneAssetPublishedManifestV2Validator(self.session).validate(
                project=project,
                manifest=comparison_payload,
                scene_plan=scene_plan,
                visual_plan=visual_plan,
                prompt_bundle=prompt_bundle,
                require_approved=True,
                validate_output_integrity=True,
            )
            current_fingerprint = _manifest_content_fingerprint(comparison_payload)
            previous = self.artifact_rows.get(previous_id) if previous_id else None
            previous_payload = self._load_published_manifest_payload(project)
            if previous_id and previous is not None and previous_payload is not None and _manifest_content_fingerprint(previous_payload) == current_fingerprint:
                self.session.commit()
                return SceneAssetV2PublishResponse.model_validate({
                    "project_id": project_id,
                    "artifact_id": previous_id,
                    "status": str(previous_payload.get("status") or "approved"),
                    "beat_count": int(previous_payload.get("beat_count") or comparison_payload["beat_count"]),
                    "already_current": True,
                }).model_dump(mode="json")

            manifest_id = generate_id("artifact")
            payload = deepcopy(comparison_payload)
            payload["scene_asset_manifest_revision_id"] = manifest_id
            payload["parent_scene_asset_manifest_revision_id"] = previous_id
            payload = SceneAssetPublishedManifestV2Validator(self.session).validate(
                project=project,
                manifest=payload,
                scene_plan=scene_plan,
                visual_plan=visual_plan,
                prompt_bundle=prompt_bundle,
                require_approved=True,
                validate_output_integrity=True,
            )
            staged = self.artifacts.stage_artifact_revision(logical_id=f"project/{project_id}/scene_asset_manifest", artifact_type=ArtifactType.JSON, filename="scene_asset_manifest.json", content=_json_bytes(payload), artifact_id=manifest_id)
            if previous is not None:
                previous.is_current = False
                previous.superseded_by_artifact_id = manifest_id
            staged.artifact.is_current = True
            project.scene_asset_manifest_artifact_id = manifest_id
            ProjectPipelineInvalidationService(self.session).invalidate_from_scene_asset_manifest(project)
            project.updated_at = utc_now_iso()
            self.session.flush()
            self.session.commit()
            staged.owns_file = False
        except Exception:
            rollback_safely(self.session, logger=__import__("logging").getLogger(__name__))
            cleanup_staged_artifact_safely(staged, logger=__import__("logging").getLogger(__name__))
            raise
        return SceneAssetV2PublishResponse.model_validate({"project_id": project_id, "artifact_id": staged.artifact_id, "status": "approved", "beat_count": payload["beat_count"], "already_current": False}).model_dump(mode="json")

    def _workspace_payload(self, project: Project, scene_plan: dict[str, Any], visual_plan: dict[str, Any], prompt_bundle: dict[str, Any]) -> dict[str, Any]:
        prompt_items = list(prompt_bundle.get("prompt_items", []))
        prompt_by_beat = {str(item["beat_id"]): item for item in prompt_items}
        if len(prompt_by_beat) != len(prompt_items):
            raise AppError("SCENE_ASSET_PROMPT_BUNDLE_MISMATCH", "Prompt Bundle beat IDs must be unique.", 409, retryable=False)
        scene_plan_beats = {str(beat.get("beat_id")): beat for scene in scene_plan.get("scenes", []) for beat in scene.get("beats", [])}
        active_operation_beat_ids = self._active_operation_beat_ids(project.project_id)
        scenes: list[dict[str, Any]] = []
        global_beats: list[dict[str, Any]] = []
        for visual_scene in visual_plan.get("scenes", []):
            scene_beats: list[dict[str, Any]] = []
            for visual_beat in visual_scene.get("beat_plans", []):
                beat_id = str(visual_beat["beat_id"])
                item = prompt_by_beat.get(beat_id)
                if item is None:
                    raise AppError("SCENE_ASSET_PROMPT_BUNDLE_MISMATCH", "Prompt Bundle is missing an exact beat item.", 409, retryable=False, details={"beat_id": beat_id})
                state = self.session.get(SceneAssetBeatState, (project.project_id, beat_id))
                active = bool(state and state.active_operation_beat_id)
                if active and state.active_operation_beat_id not in active_operation_beat_ids:
                    active = True
                projected = self._project_beat(project, item, visual_beat, scene_plan_beats.get(beat_id), state, active)
                scene_beats.append(projected)
                global_beats.append(projected)
            scenes.append({"scene_id": str(visual_scene["scene_id"]), "scene_order": int(visual_scene["scene_order"]), "section_id": visual_scene.get("section_id"), "section_order": visual_scene.get("section_order"), "section_title": visual_scene.get("section_title"), "beats": scene_beats})
        latest_operation = self._latest_operation(project.project_id)
        generate_eligible = sum(1 for beat in global_beats if beat["can_generate"])
        retry_eligible = sum(1 for beat in global_beats if beat["can_retry"])
        failed = sum(1 for beat in global_beats if beat["resolution_status"] == "failed")
        approved = sum(1 for beat in global_beats if beat["resolution_status"] == "approved")
        all_approved = bool(global_beats) and approved == len(global_beats)
        owned_beat_ids = self._owned_beat_state_ids(project.project_id)
        manifest_state, published_status = self._manifest_publication_state(project, scene_plan, visual_plan, prompt_bundle, all_approved=all_approved)
        approvable = all_approved and not owned_beat_ids and manifest_state != "current"
        return {
            "project_id": project.project_id,
            "readiness": {"ready": True, "blockers": []},
            "current_prompt_bundle_artifact_id": project.prompt_bundle_artifact_id,
            "published_manifest_artifact_id": getattr(project, "scene_asset_manifest_artifact_id", None),
            "published_manifest_status": published_status,
            "manifest_state": manifest_state,
            "latest_operation": latest_operation,
            "scenes": scenes,
            "actions": {"can_generate_all": generate_eligible > 0, "can_generate_missing": (generate_eligible + retry_eligible) > 0, "can_retry_failed": retry_eligible > 0, "can_approve_manifest": approvable, "can_publish_manifest": approvable, "eligible_generate_count": generate_eligible + retry_eligible, "failed_count": failed, "approved_count": approved, "total_count": len(global_beats)},
        }

    def _project_beat(self, project: Project, prompt_item: dict[str, Any], visual_beat: dict[str, Any], scene_plan_beat: dict[str, Any] | None, state: SceneAssetBeatState | None, active: bool) -> dict[str, Any]:
        beat_id = str(prompt_item["beat_id"])
        candidates = self._current_candidates_for_prompt(project, prompt_item)
        candidate_ids = {candidate.candidate_id for candidate in candidates}
        current_id = state.current_draft_candidate_id if state and state.current_draft_candidate_id in candidate_ids else None
        selected_id = state.selected_candidate_id if state and state.selected_candidate_id in candidate_ids else None
        candidate_payloads: list[dict[str, Any]] = []
        for candidate in candidates:
            artifact = self._workspace_candidate_artifact(candidate)
            if artifact is None:
                continue
            candidate_payloads.append({"candidate_id": candidate.candidate_id, "artifact_id": candidate.artifact_id, "source_kind": candidate.source_kind, "mime_type": artifact.mime_type, "sha256": artifact.sha256, "width": artifact.width, "height": artifact.height, "is_current_draft": candidate.candidate_id == current_id, "is_approved": candidate.candidate_id == selected_id, "is_rejected": bool(state and state.review_status == "rejected" and candidate.candidate_id == current_id), "generated_from_current_prompt": True})
        stable_resolution = str(state.resolution_status) if state else "not_generated"
        resolution = self._transient_resolution(state, stable_resolution) if active else stable_resolution
        review_status = str(state.review_status) if state else "unreviewed"
        continuity = self._continuity_readiness(project, prompt_item)
        has_reviewable = bool(candidate_payloads)
        locked = bool(state and self._is_locked(state))
        current_candidate_row = next((candidate for candidate in candidates if candidate.candidate_id == current_id), None)
        current_candidate = next((item for item in candidate_payloads if item["candidate_id"] == current_id), None)
        current_continuity_ok = bool(current_candidate_row is not None and self._candidate_continuity_is_current(project, prompt_item, current_candidate_row))
        error_payload = None
        if state and state.last_error_json:
            raw = dict(state.last_error_json)
            error_payload = {"code": str(raw.get("error_code") or raw.get("code") or "SCENE_ASSET_GENERATION_FAILED"), "message": str(raw.get("error_message") or raw.get("message") or "Scene Asset generation failed."), "details": dict(raw.get("error_details") or raw.get("details") or {})}
        script_quote = None
        if scene_plan_beat:
            for key in ("script_quote", "narration_quote", "narration_excerpt", "text"):
                value = scene_plan_beat.get(key)
                if isinstance(value, str) and value.strip():
                    script_quote = value
                    break
        return {
            "scene_id": str(prompt_item["scene_id"]), "beat_id": beat_id, "beat_order": int(prompt_item["beat_order"]), "local_beat_order": int(prompt_item["local_beat_order"]),
            "computed_duration_seconds": float(visual_beat["computed_duration_seconds"]), "resolution_status": resolution, "review_status": review_status,
            "beat_state_version": int(state.version) if state else 1, "active_operation_beat_id": state.active_operation_beat_id if state else None,
            "current_draft_candidate_id": current_id, "selected_candidate_id": selected_id, "prompt_item_id": str(prompt_item["prompt_item_id"]),
            "prompt_hash": str(prompt_item["prompt_hash"]), "prompt_origin": str(prompt_item.get("prompt_origin") or "compiled"), "image_prompt": str(prompt_item["image_prompt"]),
            "negative_prompt": str(prompt_item["negative_prompt"]), "reference_bindings": list(prompt_item.get("reference_bindings", [])), "treatment": self._treatment(visual_beat),
            "script_quote": script_quote, "candidates": candidate_payloads, "continuity": continuity, "approval_change_impact_count": self.continuity.impact_count(project.project_id, beat_id),
            "can_generate": not active and not locked and stable_resolution == "not_generated" and not has_reviewable and continuity["source_ready"],
            "can_retry": not active and not locked and stable_resolution == "failed" and not has_reviewable and continuity["source_ready"],
            "can_regenerate": not active and not locked and has_reviewable and continuity["source_ready"], "can_upload": not active and not locked and continuity["source_ready"],
            "can_review": not active and not locked and bool(candidate_payloads),
            "can_approve": not active and not locked and current_candidate is not None and bool(current_candidate["generated_from_current_prompt"]) and current_continuity_ok,
            "error": error_payload,
        }

    def _build_published_manifest(self, project: Project, scene_plan: dict[str, Any], visual_plan: dict[str, Any], prompt_bundle: dict[str, Any], *, manifest_id: str | None = None, parent_manifest_id: str | None = None) -> dict[str, Any]:
        prompt_by_beat = {str(item["beat_id"]): item for item in prompt_bundle.get("prompt_items", [])}
        if manifest_id is None:
            manifest_id = generate_id("artifact")
        scenes: list[dict[str, Any]] = []
        all_entries: list[dict[str, Any]] = []
        for visual_scene in visual_plan.get("scenes", []):
            entries: list[dict[str, Any]] = []
            for visual_beat in visual_scene.get("beat_plans", []):
                beat_id = str(visual_beat["beat_id"])
                prompt_item = prompt_by_beat.get(beat_id)
                state = self.session.get(SceneAssetBeatState, (project.project_id, beat_id))
                if prompt_item is None or state is None or state.resolution_status != "approved" or state.review_status != "approved" or not state.selected_candidate_id:
                    raise AppError("SCENE_ASSET_MANIFEST_NOT_APPROVABLE", "Every beat must have one approved current candidate before Manifest publication.", 409, retryable=False, details={"beat_id": beat_id})
                candidate = self.session.get(SceneAssetCandidate, state.selected_candidate_id)
                if candidate is None or candidate.project_id != project.project_id or candidate.beat_id != beat_id:
                    raise AppError("SCENE_ASSET_CANDIDATE_OWNERSHIP_INVALID", "Approved candidate ownership is invalid.", 409, retryable=False, details={"beat_id": beat_id})
                self._assert_candidate_current_prompt(project, prompt_item, candidate)
                self._assert_candidate_current_continuity(project, prompt_item, candidate)
                artifact = self.artifact_rows.get(candidate.artifact_id)
                self._assert_image_artifact(project, beat_id, artifact)
                candidate_rows = list(
                    self.session.scalars(
                        select(SceneAssetCandidate)
                        .where(
                            SceneAssetCandidate.project_id == project.project_id,
                            SceneAssetCandidate.beat_id == beat_id,
                        )
                        .order_by(
                            SceneAssetCandidate.created_at,
                            SceneAssetCandidate.candidate_id,
                        )
                    )
                )
                generation_lineage = self._candidate_generation_lineage(project, candidate)
                task_ids: list[str] = []
                if candidate.operation_task_id:
                    task = self.session.get(SceneAssetOperationTask, candidate.operation_task_id)
                    if task and task.media_task_id:
                        task_ids.append(task.media_task_id)
                entry = {
                    "scene_id": str(visual_beat["scene_id"]), "beat_id": beat_id, "beat_order": int(visual_beat["beat_order"]), "local_beat_order": int(visual_beat["local_beat_order"]),
                    "continuity_group_id": str(visual_beat["continuity_group_id"]), "computed_duration_seconds": float(visual_beat["computed_duration_seconds"]), "resolution_status": "approved", "review_status": "approved",
                    "selected_candidate_id": candidate.candidate_id, "selected_asset_artifact_id": candidate.artifact_id, "candidate_asset_artifact_ids": [item.artifact_id for item in candidate_rows],
                    "source_kind": candidate.source_kind, "source_prompt_bundle_artifact_id": candidate.source_prompt_bundle_artifact_id, "source_prompt_item_id": candidate.source_prompt_item_id,
                    "source_prompt_hash": candidate.source_prompt_hash, "media_task_ids": task_ids, "width": artifact.width, "height": artifact.height, "mime_type": artifact.mime_type, "sha256": artifact.sha256,
                    "generation_lineage": generation_lineage, "continuity_source": deepcopy(candidate.continuity_snapshot_json), "treatment": self._treatment(visual_beat), "reference_bindings": list(prompt_item.get("reference_bindings", [])), "error": None,
                }
                entries.append(entry)
                all_entries.append(entry)
            scenes.append({"scene_id": str(visual_scene["scene_id"]), "scene_order": int(visual_scene["scene_order"]), "entries": entries})
        payload = {
            "artifact_type": "scene_asset_manifest", "schema_version": "4.0", "project_id": project.project_id, "scene_asset_manifest_revision_id": manifest_id,
            "parent_scene_asset_manifest_revision_id": parent_manifest_id, "revision_operation": "approval", "source_script_revision_id": str(project.script_artifact_id),
            "source_script_analysis_revision_id": str(project.script_analysis_artifact_id), "source_style_bible_revision_id": str(project.style_bible_artifact_id),
            "source_scene_plan_revision_id": str(project.scene_plan_artifact_id), "source_visual_plan_revision_id": str(project.visual_plan_artifact_id),
            "source_prompt_bundle_revision_id": str(project.prompt_bundle_artifact_id), "source_visual_reference_approval_set_artifact_id": project.style_bible_visual_reference_approval_set_artifact_id,
            "source_operation_id": None, "status": "approved", "scene_count": len(scenes), "beat_count": len(all_entries), "approved_asset_count": len(all_entries), "failed_beat_count": 0,
            "scenes": scenes,
            "backend_qc": {"coverage_status": "passed", "ordering_status": "passed", "prompt_bundle_parity_status": "passed", "asset_integrity_status": "passed", "lineage_status": "passed", "approval_eligibility_status": "passed", "issues": [], "warnings": []},
        }
        return SceneAssetPublishedManifestV2.model_validate(payload).model_dump(mode="json")

    def _load_published_manifest_payload(self, project: Project) -> dict[str, Any] | None:
        if not project.scene_asset_manifest_artifact_id:
            return None
        row = self.artifact_rows.get(project.scene_asset_manifest_artifact_id)
        if row is None or row.status != ArtifactStatus.READY:
            return None
        try:
            value = json.loads(self.artifacts.resolve_file_path(row).read_bytes().decode("utf-8"))
        except Exception:
            return None
        return value if isinstance(value, dict) else None

    def _manifest_publication_state(
        self,
        project: Project,
        scene_plan: dict[str, Any],
        visual_plan: dict[str, Any],
        prompt_bundle: dict[str, Any],
        *,
        all_approved: bool,
    ) -> tuple[str, str | None]:
        published_manifest_artifact_id = getattr(project, "scene_asset_manifest_artifact_id", None)
        if not published_manifest_artifact_id:
            return "unpublished", None
        published_payload = self._load_published_manifest_payload(project)
        published_status = str(published_payload.get("status")) if published_payload and published_payload.get("status") is not None else None
        if not all_approved or published_payload is None:
            return "stale", published_status
        try:
            current_payload = self._build_published_manifest(
                project,
                scene_plan,
                visual_plan,
                prompt_bundle,
                manifest_id="artifact_000000000000",
                parent_manifest_id=published_manifest_artifact_id,
            )
            current_payload = SceneAssetPublishedManifestV2Validator(self.session).validate(
                project=project,
                manifest=current_payload,
                scene_plan=scene_plan,
                visual_plan=visual_plan,
                prompt_bundle=prompt_bundle,
                require_approved=True,
                validate_output_integrity=True,
            )
        except Exception:
            return "stale", published_status
        if _manifest_content_fingerprint(published_payload) == _manifest_content_fingerprint(current_payload):
            return "current", published_status
        return "stale", published_status

    def _publication_projection_payload(
        self,
        project: Project,
        scene_plan: dict[str, Any],
        visual_plan: dict[str, Any],
        prompt_bundle: dict[str, Any],
        *,
        actions: dict[str, Any],
    ) -> dict[str, Any]:
        all_approved = bool(actions.get("total_count")) and int(actions.get("approved_count") or 0) == int(actions.get("total_count") or 0)
        manifest_state, published_status = self._manifest_publication_state(
            project,
            scene_plan,
            visual_plan,
            prompt_bundle,
            all_approved=all_approved,
        )
        return {
            "published_manifest_artifact_id": getattr(project, "scene_asset_manifest_artifact_id", None),
            "published_manifest_status": published_status,
            "manifest_state": manifest_state,
        }

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

    @staticmethod
    def _prompt_item(prompt_bundle: dict[str, Any], beat_id: str) -> dict[str, Any]:
        matches = [item for item in prompt_bundle.get("prompt_items", []) if item.get("beat_id") == beat_id]
        if len(matches) != 1:
            raise AppError("SCENE_ASSET_BEAT_NOT_FOUND", "Prompt Bundle does not contain the exact Scene Asset beat.", 404, retryable=False, details={"beat_id": beat_id})
        return matches[0]

    def _ensure_state(self, project: Project, prompt_item: dict[str, Any]) -> SceneAssetBeatState:
        beat_id = str(prompt_item["beat_id"])
        state = self.session.get(SceneAssetBeatState, (project.project_id, beat_id))
        if state is not None:
            if state.scene_id != str(prompt_item["scene_id"]):
                raise AppError("SCENE_ASSET_V2_STATE_LINEAGE_STALE", "Scene Asset BeatState scene identity is stale.", 409, retryable=False, details={"beat_id": beat_id})
            return state
        now = utc_now_iso()
        state = SceneAssetBeatState(project_id=project.project_id, beat_id=beat_id, scene_id=str(prompt_item["scene_id"]), version=1, resolution_status="not_generated", review_status="unreviewed", current_draft_candidate_id=None, selected_candidate_id=None, active_operation_beat_id=None, continuity_source_scene_id=None, continuity_source_beat_id=None, continuity_source_candidate_id=None, last_error_json=None, last_progress_at=None, created_at=now, updated_at=now)
        self.session.add(state)
        self.session.flush()
        return state

    @staticmethod
    def _is_locked(state: SceneAssetBeatState) -> bool:
        return state.resolution_status == "approved" or state.review_status == "approved"

    @staticmethod
    def _assert_mutable_beat(state: SceneAssetBeatState, *, action: str) -> None:
        if state.active_operation_beat_id is not None:
            raise AppError("SCENE_ASSET_V2_BEAT_BUSY", f"Scene Asset beat has active generation and cannot {action} yet.", 409, retryable=True, details={"beat_id": state.beat_id, "operation_beat_id": state.active_operation_beat_id})

    def _current_prompt_revision(self, project: Project, prompt_item: dict[str, Any]) -> SceneAssetPromptRevision:
        beat_id = str(prompt_item["beat_id"])
        revision = self.prompt_revisions.current(project.project_id, beat_id)
        valid = (
            revision.scene_id == str(prompt_item["scene_id"])
            and revision.source_prompt_item_id == str(prompt_item["prompt_item_id"])
            and revision.source_prompt_hash == str(prompt_item["prompt_hash"])
            and revision.image_prompt == str(prompt_item["image_prompt"])
            and revision.negative_prompt == str(prompt_item["negative_prompt"])
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

    def _candidate_matches_prompt(self, project: Project, prompt_item: dict[str, Any], candidate: SceneAssetCandidate) -> bool:
        revision = self._current_prompt_revision(project, prompt_item)
        return bool(
            candidate.source_prompt_revision_id == revision.prompt_revision_id
            and candidate.source_prompt_bundle_artifact_id == revision.source_prompt_bundle_artifact_id
            and candidate.source_prompt_item_id == revision.source_prompt_item_id
            and candidate.source_prompt_hash == revision.source_prompt_hash
        )

    def _current_candidates_for_prompt(self, project: Project, prompt_item: dict[str, Any]) -> list[SceneAssetCandidate]:
        candidates = list(
            self.session.scalars(
                select(SceneAssetCandidate)
                .where(
                    SceneAssetCandidate.project_id == project.project_id,
                    SceneAssetCandidate.beat_id == str(prompt_item["beat_id"]),
                )
                .order_by(SceneAssetCandidate.created_at, SceneAssetCandidate.candidate_id)
            )
        )
        return [
            candidate
            for candidate in candidates
            if self._candidate_matches_prompt(project, prompt_item, candidate)
        ]

    def _workspace_candidate_artifact(self, candidate: SceneAssetCandidate) -> Artifact | None:
        artifact = self.artifact_rows.get(candidate.artifact_id)
        if artifact is None or artifact.status != ArtifactStatus.READY or artifact.deleted_at is not None or artifact.artifact_type != ArtifactType.IMAGE or not artifact.sha256 or not artifact.mime_type:
            return None
        return artifact

    def _assert_candidate_current_prompt(self, project: Project, prompt_item: dict[str, Any], candidate: SceneAssetCandidate) -> None:
        if not self._candidate_matches_prompt(project, prompt_item, candidate):
            raise AppError("SCENE_ASSET_CANDIDATE_PROMPT_STALE", "Scene Asset candidate was created from a previous Prompt Bundle revision.", 409, retryable=False, details={"beat_id": candidate.beat_id, "candidate_id": candidate.candidate_id})

    def _candidate_continuity_is_current(self, project: Project, prompt_item: dict[str, Any], candidate: SceneAssetCandidate) -> bool:
        requirement = prompt_item.get("continuity_reference_requirement")
        stored = candidate.continuity_snapshot_json
        if not isinstance(requirement, dict):
            return stored is None
        current = self._continuity_snapshot(project, prompt_item, require_ready=False)
        return isinstance(stored, dict) and current is not None and stored == current

    def _assert_candidate_current_continuity(self, project: Project, prompt_item: dict[str, Any], candidate: SceneAssetCandidate) -> None:
        if not self._candidate_continuity_is_current(project, prompt_item, candidate):
            raise AppError("SCENE_ASSET_CANDIDATE_CONTINUITY_STALE", "Scene Asset candidate continuity source is no longer the current approved source.", 409, retryable=False, details={"beat_id": candidate.beat_id, "candidate_id": candidate.candidate_id})

    def _continuity_snapshot(self, project: Project, prompt_item: dict[str, Any], *, require_ready: bool) -> dict[str, Any] | None:
        requirement = prompt_item.get("continuity_reference_requirement")
        if not isinstance(requirement, dict):
            return None
        source_beat_id = str(requirement.get("source_beat_id") or "")
        source = self.session.get(SceneAssetBeatState, (project.project_id, source_beat_id))
        if source is None or source.resolution_status != "approved" or source.review_status != "approved" or not source.selected_candidate_id:
            if require_ready:
                raise AppError("SCENE_ASSET_CONTINUITY_NOT_READY", "Previous-beat approved continuity source is required.", 409, retryable=False, details={"beat_id": prompt_item["beat_id"], "source_beat_id": source_beat_id})
            return None
        candidate = self.session.get(SceneAssetCandidate, source.selected_candidate_id)
        artifact = self.artifact_rows.get(candidate.artifact_id) if candidate else None
        try:
            self._assert_image_artifact(project, source_beat_id, artifact)
        except AppError:
            if require_ready:
                raise
            return None
        return {"relation": str(requirement.get("relation")), "source_beat_id": source_beat_id, "source_asset_artifact_id": artifact.artifact_id, "source_asset_sha256": artifact.sha256, "mime_type": artifact.mime_type, "source_kind": "approved"}

    def _continuity_readiness(self, project: Project, prompt_item: dict[str, Any]) -> dict[str, Any]:
        requirement = prompt_item.get("continuity_reference_requirement")
        if not isinstance(requirement, dict):
            return {"required": False, "source_beat_id": None, "source_ready": True, "source_candidate_id": None, "block_reason": None}
        source_beat_id = str(requirement.get("source_beat_id") or "")
        source = self.session.get(SceneAssetBeatState, (project.project_id, source_beat_id))
        if source is None or source.resolution_status != "approved" or source.review_status != "approved" or not source.selected_candidate_id:
            return {"required": True, "source_beat_id": source_beat_id, "source_ready": False, "source_candidate_id": None, "block_reason": "source_not_approved"}
        candidate = self.session.get(SceneAssetCandidate, source.selected_candidate_id)
        artifact = self.artifact_rows.get(candidate.artifact_id) if candidate else None
        try:
            self._assert_image_artifact(project, source_beat_id, artifact)
        except AppError:
            return {"required": True, "source_beat_id": source_beat_id, "source_ready": False, "source_candidate_id": source.selected_candidate_id, "block_reason": "source_integrity_failed"}
        return {"required": True, "source_beat_id": source_beat_id, "source_ready": True, "source_candidate_id": source.selected_candidate_id, "block_reason": None}

    def _active_operation_beat_ids(self, project_id: str) -> set[str]:
        stmt = select(SceneAssetOperationBeat.operation_beat_id).join(SceneAssetWorkingOperation, SceneAssetWorkingOperation.operation_id == SceneAssetOperationBeat.operation_id).where(SceneAssetOperationBeat.project_id == project_id, SceneAssetOperationBeat.status.in_(_ACTIVE_BEAT_STATUSES), SceneAssetWorkingOperation.status.in_(_ACTIVE_OPERATION_STATUSES))
        return {str(value) for value in self.session.scalars(stmt)}

    def _owned_beat_state_ids(self, project_id: str) -> set[str]:
        stmt = select(SceneAssetBeatState.beat_id).where(SceneAssetBeatState.project_id == project_id, SceneAssetBeatState.active_operation_beat_id.is_not(None))
        return {str(value) for value in self.session.scalars(stmt)}

    def _transient_resolution(self, state: SceneAssetBeatState | None, stable: str) -> str:
        if state is None or not state.active_operation_beat_id:
            return stable
        operation_beat = self.session.get(SceneAssetOperationBeat, state.active_operation_beat_id)
        if operation_beat is None or operation_beat.status not in _ACTIVE_BEAT_STATUSES:
            return stable
        tasks = list(self.session.scalars(select(SceneAssetOperationTask).where(SceneAssetOperationTask.operation_beat_id == operation_beat.operation_beat_id)))
        if tasks and all(task.status in {"queued", "submitting"} for task in tasks):
            return "queued"
        return "generating"

    def _latest_operation(self, project_id: str) -> dict[str, Any] | None:
        operation = self.session.scalar(select(SceneAssetWorkingOperation).where(SceneAssetWorkingOperation.project_id == project_id).order_by(SceneAssetWorkingOperation.created_at.desc(), SceneAssetWorkingOperation.operation_id.desc()).limit(1))
        if operation is None:
            return None
        terminal = int(operation.completed_count) + int(operation.failed_count) + int(operation.cancelled_count)
        progress = int(round(100 * terminal / max(1, int(operation.total_beat_count))))
        return {"operation_id": operation.operation_id, "operation_type": operation.operation_type, "status": operation.status, "progress": min(100, max(0, progress)), "error_code": operation.error_code, "error_message": operation.error_message}

    def _candidate_generation_lineage(self, project: Project, candidate: SceneAssetCandidate) -> dict[str, Any] | None:
        if candidate.source_kind == "uploaded":
            return None
        if not candidate.operation_beat_id:
            raise AppError("SCENE_ASSET_CANDIDATE_OWNERSHIP_INVALID", "Generated candidate is missing operation-beat ownership.", 409, retryable=False)
        operation_beat = self.session.get(SceneAssetOperationBeat, candidate.operation_beat_id)
        operation = self.session.get(SceneAssetWorkingOperation, operation_beat.operation_id) if operation_beat else None
        if operation_beat is None or operation is None:
            raise AppError("SCENE_ASSET_CANDIDATE_OWNERSHIP_INVALID", "Generated candidate operation lineage is missing.", 409, retryable=False)
        return {"scene_asset_operation_id": operation.operation_id, "scene_plan_artifact_id": operation.source_scene_plan_artifact_id, "visual_plan_artifact_id": operation.source_visual_plan_artifact_id, "prompt_bundle_artifact_id": candidate.source_prompt_bundle_artifact_id, "prompt_item_id": candidate.source_prompt_item_id, "prompt_hash": candidate.source_prompt_hash, "visual_reference_approval_set_artifact_id": operation.source_reference_approval_set_artifact_id, "reference_binding_snapshot_hash": operation_beat.reference_bindings_hash, "generation_settings_snapshot_hash": operation_beat.generation_options_hash}

    def _assert_image_artifact(self, project: Project, beat_id: str, artifact: Artifact | None) -> None:
        if artifact is None or artifact.status != ArtifactStatus.READY or artifact.deleted_at is not None or artifact.artifact_type != ArtifactType.IMAGE or not artifact.sha256 or not artifact.mime_type or not artifact.mime_type.startswith("image/") or not artifact.logical_id.startswith(f"project/{project.project_id}/"):
            raise AppError("SCENE_ASSET_ARTIFACT_INVALID", "Scene Asset candidate image Artifact is invalid.", 409, retryable=False, details={"beat_id": beat_id})
        self.artifacts.resolve_file_path(artifact)

    @staticmethod
    def _treatment(beat: dict[str, Any]) -> dict[str, Any]:
        return {
            "visual_concept": str(beat["visual_concept"]), "subject_action": str(beat["subject_action"]), "environment_emphasis": str(beat["environment_emphasis"]),
            "framing": str(beat["framing"]), "camera_angle": str(beat["camera_angle"]), "camera_movement": str(beat["camera_movement"]), "composition": str(beat["composition"]),
            "lighting_direction": str(beat["lighting_direction"]), "color_emphasis": str(beat["color_emphasis"]), "motion_guidance": str(beat["motion_guidance"]),
            "continuity_with_previous": str(beat["continuity_with_previous"]), "continuity_notes": str(beat["continuity_notes"]), "production_notes": str(beat["production_notes"]),
            "negative_requirements": list(beat.get("negative_requirements", [])), "visible_text": list(beat.get("visible_text", [])),
        }

    def _workspace_actions_payload(self, project: Project, scene_plan: dict[str, Any] | None, visual_plan: dict[str, Any] | None = None, prompt_bundle: dict[str, Any] | None = None) -> dict[str, Any]:
        if prompt_bundle is None:
            prompt_bundle = visual_plan
            visual_plan = scene_plan
            scene_plan = None
        assert visual_plan is not None and prompt_bundle is not None
        prompt_by_beat = {str(item["beat_id"]): item for item in prompt_bundle.get("prompt_items", [])}
        beat_ids = [str(beat["beat_id"]) for scene in visual_plan.get("scenes", []) for beat in scene.get("beat_plans", [])]
        states = {
            str(state.beat_id): state
            for state in self.session.scalars(
                select(SceneAssetBeatState).where(
                    SceneAssetBeatState.project_id == project.project_id,
                    SceneAssetBeatState.beat_id.in_(beat_ids),
                )
            )
        } if beat_ids else {}
        candidate_counts: dict[str, int] = {}
        for beat_id in beat_ids:
            prompt_item = prompt_by_beat.get(beat_id)
            if prompt_item is None:
                continue
            candidate_counts[beat_id] = sum(
                1
                for candidate in self._current_candidates_for_prompt(project, prompt_item)
                if self._workspace_candidate_artifact(candidate) is not None
            )
        generate_eligible = 0
        retry_eligible = 0
        failed = 0
        approved = 0
        for beat_id in beat_ids:
            prompt_item = prompt_by_beat.get(beat_id)
            if prompt_item is None:
                raise AppError("SCENE_ASSET_PROMPT_BUNDLE_MISMATCH", "Prompt Bundle is missing an exact beat item.", 409, retryable=False, details={"beat_id": beat_id})
            state = states.get(beat_id)
            stable_resolution = str(state.resolution_status) if state else "not_generated"
            active = bool(state and state.active_operation_beat_id)
            locked = bool(state and self._is_locked(state))
            has_reviewable = candidate_counts.get(beat_id, 0) > 0
            continuity = self._continuity_readiness(project, prompt_item)
            if not active and not locked and stable_resolution == "not_generated" and not has_reviewable and continuity["source_ready"]:
                generate_eligible += 1
            if not active and not locked and stable_resolution == "failed" and not has_reviewable and continuity["source_ready"]:
                retry_eligible += 1
            if stable_resolution == "failed":
                failed += 1
            if stable_resolution == "approved":
                approved += 1
        all_approved = bool(beat_ids) and approved == len(beat_ids)
        manifest_state = "stale"
        if scene_plan is not None:
            manifest_state, _published_status = self._manifest_publication_state(project, scene_plan, visual_plan, prompt_bundle, all_approved=all_approved)
        approvable = all_approved and not self._owned_beat_state_ids(project.project_id) and manifest_state != "current"
        return {
            "can_generate_all": generate_eligible > 0,
            "can_generate_missing": (generate_eligible + retry_eligible) > 0,
            "can_retry_failed": retry_eligible > 0,
            "can_approve_manifest": approvable,
            "can_publish_manifest": approvable,
            "eligible_generate_count": generate_eligible + retry_eligible,
            "failed_count": failed,
            "approved_count": approved,
            "total_count": len(beat_ids),
        }

    def _mutation_response_from_loaded_workspace(
        self,
        project: Project,
        scene_plan: dict[str, Any],
        visual_plan: dict[str, Any],
        prompt_bundle: dict[str, Any],
        beat_id: str,
        *,
        invalidated_beat_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        prompt_item = self._prompt_item(prompt_bundle, beat_id)
        visual_beat = next(
            (
                beat
                for scene in visual_plan.get("scenes", [])
                for beat in scene.get("beat_plans", [])
                if str(beat.get("beat_id")) == beat_id
            ),
            None,
        )
        if visual_beat is None:
            raise AppError("SCENE_ASSET_BEAT_NOT_FOUND", "Visual Plan does not contain the exact Scene Asset beat.", 404, retryable=False, details={"beat_id": beat_id})
        scene_plan_beat = next(
            (
                beat
                for scene in scene_plan.get("scenes", [])
                for beat in scene.get("beats", [])
                if str(beat.get("beat_id")) == beat_id
            ),
            None,
        )
        state = self.session.get(SceneAssetBeatState, (project.project_id, beat_id))
        active_operation_beat_ids = self._active_operation_beat_ids(project.project_id)
        active = bool(state and state.active_operation_beat_id)
        if active and state.active_operation_beat_id not in active_operation_beat_ids:
            active = True
        actions = self._workspace_actions_payload(project, scene_plan, visual_plan, prompt_bundle)
        payload = {
            "project_id": project.project_id,
            **self._publication_projection_payload(project, scene_plan, visual_plan, prompt_bundle, actions=actions),
            "beat": self._project_beat(project, prompt_item, visual_beat, scene_plan_beat, state, active),
            "invalidated_beat_ids": invalidated_beat_ids or [],
            "actions": actions,
        }
        return SceneAssetV2MutationResponse.model_validate(payload).model_dump(mode="json")

    def _mutation_response(self, project_id: str, beat_id: str, *, artifact_id: str | None, invalidated_beat_ids: list[str] | None = None) -> dict[str, Any]:
        response = self.beat(project_id, beat_id)
        payload = {"project_id": project_id, "published_manifest_artifact_id": response["published_manifest_artifact_id"], "published_manifest_status": response["published_manifest_status"], "manifest_state": response["manifest_state"], "beat": response["beat"], "invalidated_beat_ids": invalidated_beat_ids or [], "actions": response["actions"]}
        return SceneAssetV2MutationResponse.model_validate(payload).model_dump(mode="json")
