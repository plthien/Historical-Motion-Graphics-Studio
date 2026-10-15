from __future__ import annotations

import logging

from sqlalchemy import select, text

from app.api.errors import AppError
from app.api.scene_asset_workspace_v2_schemas import SceneAssetV2BeatResponse, SceneAssetV2WorkspaceResponse
from app.core.ids import generate_id
from app.core.time import utc_now_iso
from app.db.enums import ArtifactStatus, ArtifactType
from app.db.models import Artifact, Project
from app.db.scene_asset_beat_state import SceneAssetBeatState
from app.db.scene_asset_candidate import SceneAssetCandidate
from app.modules.media.image_upload_validation import validate_uploaded_image
from app.modules.v4.scene_asset_continuity import ContinuityInvalidationPlan
from app.modules.v4.scene_asset_workspace_v2 import SceneAssetWorkspaceV2Service as _BaseSceneAssetWorkspaceV2Service
from app.services.artifacts import StagedArtifact, cleanup_staged_artifact_safely, rollback_safely


logger = logging.getLogger(__name__)


class SceneAssetWorkspaceV2Service(_BaseSceneAssetWorkspaceV2Service):
    """Route-facing V2 workspace with explicit per-beat optimistic writes."""

    def __init__(self, session) -> None:
        super().__init__(session)
        self._projection_candidates_by_beat: dict[str, list[SceneAssetCandidate]] | None = None
        self._projection_artifacts_by_id: dict[str, Artifact] | None = None

    def workspace(self, project_id: str) -> dict:
        project = self._project_required(project_id)
        scene_plan, visual_plan, prompt_bundle = self._load_upstream(project)
        beat_ids = [
            str(beat["beat_id"])
            for scene in visual_plan.get("scenes", [])
            for beat in scene.get("beat_plans", [])
        ]
        if beat_ids:
            states = list(
                self.session.scalars(
                    select(SceneAssetBeatState).where(
                        SceneAssetBeatState.project_id == project_id,
                        SceneAssetBeatState.beat_id.in_(beat_ids),
                    )
                )
            )
            candidates = list(
                self.session.scalars(
                    select(SceneAssetCandidate)
                    .where(
                        SceneAssetCandidate.project_id == project_id,
                        SceneAssetCandidate.beat_id.in_(beat_ids),
                    )
                    .order_by(
                        SceneAssetCandidate.beat_id,
                        SceneAssetCandidate.created_at,
                        SceneAssetCandidate.candidate_id,
                    )
                )
            )
            candidates_by_beat: dict[str, list[SceneAssetCandidate]] = {}
            for candidate in candidates:
                candidates_by_beat.setdefault(str(candidate.beat_id), []).append(candidate)
            artifact_ids = sorted({str(candidate.artifact_id) for candidate in candidates})
            artifacts = list(
                self.session.scalars(
                    select(Artifact).where(Artifact.artifact_id.in_(artifact_ids))
                )
            ) if artifact_ids else []
            self._projection_candidates_by_beat = candidates_by_beat
            self._projection_artifacts_by_id = {
                str(artifact.artifact_id): artifact for artifact in artifacts
            }
            self.prompt_revisions.prime_current(project_id, beat_ids)
            # Retain the loaded states through projection so Session.get() resolves
            # them from the identity map instead of issuing one SELECT per beat.
            _loaded_states = states
        else:
            self._projection_candidates_by_beat = {}
            self._projection_artifacts_by_id = {}
            _loaded_states = []
        try:
            payload = self._workspace_payload(project, scene_plan, visual_plan, prompt_bundle)
            return SceneAssetV2WorkspaceResponse.model_validate(payload).model_dump(mode="json")
        finally:
            self._projection_candidates_by_beat = None
            self._projection_artifacts_by_id = None
            _loaded_states.clear()

    def beat(self, project_id: str, beat_id: str) -> dict:
        project = self._project_required(project_id)
        scene_plan, visual_plan, prompt_bundle = self._load_upstream(project)
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
            raise AppError(
                "SCENE_ASSET_BEAT_NOT_FOUND",
                "Visual Plan does not contain the exact Scene Asset beat.",
                404,
                retryable=False,
                details={"beat_id": beat_id},
            )
        scene_plan_beat = next(
            (
                beat
                for scene in scene_plan.get("scenes", [])
                for beat in scene.get("beats", [])
                if str(beat.get("beat_id")) == beat_id
            ),
            None,
        )
        state = self.session.get(SceneAssetBeatState, (project_id, beat_id))
        candidates = list(
            self.session.scalars(
                select(SceneAssetCandidate)
                .where(
                    SceneAssetCandidate.project_id == project_id,
                    SceneAssetCandidate.beat_id == beat_id,
                )
                .order_by(SceneAssetCandidate.created_at, SceneAssetCandidate.candidate_id)
            )
        )
        artifact_ids = sorted({str(candidate.artifact_id) for candidate in candidates})
        artifacts = list(
            self.session.scalars(select(Artifact).where(Artifact.artifact_id.in_(artifact_ids)))
        ) if artifact_ids else []
        self._projection_candidates_by_beat = {beat_id: candidates}
        self._projection_artifacts_by_id = {
            str(artifact.artifact_id): artifact for artifact in artifacts
        }
        self.prompt_revisions.prime_current(project_id, [beat_id])
        active_operation_beat_ids = self._active_operation_beat_ids(project_id)
        active = bool(state and state.active_operation_beat_id)
        if active and state.active_operation_beat_id not in active_operation_beat_ids:
            active = True
        try:
            projected_beat = self._project_beat(
                project,
                prompt_item,
                visual_beat,
                scene_plan_beat,
                state,
                active,
            )
            actions = self._workspace_actions_payload(project, scene_plan, visual_plan, prompt_bundle)
            payload = {
                "project_id": project_id,
                **self._publication_projection_payload(project, scene_plan, visual_plan, prompt_bundle, actions=actions),
                "beat": projected_beat,
                "actions": actions,
            }
            return SceneAssetV2BeatResponse.model_validate(payload).model_dump(mode="json")
        finally:
            self._projection_candidates_by_beat = None
            self._projection_artifacts_by_id = None

    @staticmethod
    def _assert_expected_version(state, expected_beat_version: int) -> None:
        if int(state.version) != int(expected_beat_version):
            raise AppError(
                "SCENE_ASSET_V2_BEAT_STATE_CONFLICT",
                "Scene Asset beat changed since the client loaded it.",
                409,
                retryable=True,
                details={
                    "beat_id": state.beat_id,
                    "expected_beat_version": int(expected_beat_version),
                    "current_beat_version": int(state.version),
                },
            )

    def _project_beat(self, project, prompt_item, visual_beat, scene_plan_beat, state, active):
        if self._projection_candidates_by_beat is None:
            payload = super()._project_beat(project, prompt_item, visual_beat, scene_plan_beat, state, active)
            payload["prompt_revision_id"] = self._current_prompt_revision(project, prompt_item).prompt_revision_id
            return payload

        beat_id = str(prompt_item["beat_id"])
        candidates = [
            candidate
            for candidate in self._projection_candidates_by_beat.get(beat_id, [])
            if self._candidate_matches_prompt(project, prompt_item, candidate)
        ]
        candidate_ids = {candidate.candidate_id for candidate in candidates}
        current_id = state.current_draft_candidate_id if state and state.current_draft_candidate_id in candidate_ids else None
        selected_id = state.selected_candidate_id if state and state.selected_candidate_id in candidate_ids else None
        candidate_payloads: list[dict] = []
        for candidate in candidates:
            artifact = (
                self._projection_artifacts_by_id.get(str(candidate.artifact_id))
                if self._projection_artifacts_by_id is not None
                else self.artifact_rows.get(candidate.artifact_id)
            )
            if (
                artifact is None
                or artifact.status != ArtifactStatus.READY
                or artifact.deleted_at is not None
                or artifact.artifact_type != ArtifactType.IMAGE
                or not artifact.sha256
                or not artifact.mime_type
            ):
                continue
            candidate_payloads.append({
                "candidate_id": candidate.candidate_id,
                "artifact_id": candidate.artifact_id,
                "source_kind": candidate.source_kind,
                "mime_type": artifact.mime_type,
                "sha256": artifact.sha256,
                "width": artifact.width,
                "height": artifact.height,
                "is_current_draft": candidate.candidate_id == current_id,
                "is_approved": candidate.candidate_id == selected_id,
                "is_rejected": bool(
                    state
                    and state.review_status == "rejected"
                    and candidate.candidate_id == current_id
                ),
                "generated_from_current_prompt": True,
            })
        stable_resolution = str(state.resolution_status) if state else "not_generated"
        resolution = self._transient_resolution(state, stable_resolution) if active else stable_resolution
        review_status = str(state.review_status) if state else "unreviewed"
        continuity = self._continuity_readiness(project, prompt_item)
        has_reviewable = bool(candidate_payloads)
        locked = bool(state and self._is_locked(state))
        current_candidate_row = next(
            (candidate for candidate in candidates if candidate.candidate_id == current_id),
            None,
        )
        current_candidate = next(
            (item for item in candidate_payloads if item["candidate_id"] == current_id),
            None,
        )
        current_continuity_ok = bool(
            current_candidate_row is not None
            and self._candidate_continuity_is_current(project, prompt_item, current_candidate_row)
        )
        error_payload = None
        if state and state.last_error_json:
            raw = dict(state.last_error_json)
            error_payload = {
                "code": str(raw.get("error_code") or raw.get("code") or "SCENE_ASSET_GENERATION_FAILED"),
                "message": str(raw.get("error_message") or raw.get("message") or "Scene Asset generation failed."),
                "details": dict(raw.get("error_details") or raw.get("details") or {}),
            }
        script_quote = None
        if scene_plan_beat:
            for key in ("script_quote", "narration_quote", "narration_excerpt", "text"):
                value = scene_plan_beat.get(key)
                if isinstance(value, str) and value.strip():
                    script_quote = value
                    break
        payload = {
            "scene_id": str(prompt_item["scene_id"]),
            "beat_id": beat_id,
            "beat_order": int(prompt_item["beat_order"]),
            "local_beat_order": int(prompt_item["local_beat_order"]),
            "computed_duration_seconds": float(visual_beat["computed_duration_seconds"]),
            "resolution_status": resolution,
            "review_status": review_status,
            "beat_state_version": int(state.version) if state else 1,
            "active_operation_beat_id": state.active_operation_beat_id if state else None,
            "current_draft_candidate_id": current_id,
            "selected_candidate_id": selected_id,
            "prompt_item_id": str(prompt_item["prompt_item_id"]),
            "prompt_hash": str(prompt_item["prompt_hash"]),
            "prompt_origin": str(prompt_item.get("prompt_origin") or "compiled"),
            "image_prompt": str(prompt_item["image_prompt"]),
            "negative_prompt": str(prompt_item["negative_prompt"]),
            "reference_bindings": list(prompt_item.get("reference_bindings", [])),
            "treatment": self._treatment(visual_beat),
            "script_quote": script_quote,
            "candidates": candidate_payloads,
            "continuity": continuity,
            "approval_change_impact_count": self.continuity.impact_count(project.project_id, beat_id),
            "can_generate": not active and not locked and stable_resolution == "not_generated" and not has_reviewable and continuity["source_ready"],
            "can_retry": not active and not locked and stable_resolution == "failed" and not has_reviewable and continuity["source_ready"],
            "can_regenerate": not active and not locked and has_reviewable and continuity["source_ready"],
            "can_upload": not active and not locked and continuity["source_ready"],
            "can_review": not active and not locked and bool(candidate_payloads),
            "can_approve": not active and not locked and current_candidate is not None and bool(current_candidate["generated_from_current_prompt"]) and current_continuity_ok,
            "error": error_payload,
        }
        payload["prompt_revision_id"] = self._current_prompt_revision(project, prompt_item).prompt_revision_id
        return payload

    def upload_candidate(
        self,
        project_id: str,
        beat_id: str,
        content: bytes,
        *,
        declared_content_type: str | None,
        expected_beat_version: int,
    ) -> dict:
        image = validate_uploaded_image(
            content,
            declared_content_type=declared_content_type,
            error_prefix="SCENE_ASSET_UPLOAD",
            image_label="Scene Asset image",
        )
        staged: StagedArtifact | None = None
        candidate_id = generate_id("scene_asset_candidate")
        try:
            self.session.execute(text("BEGIN IMMEDIATE"))
            project = self._project_required(project_id)
            scene_plan, visual_plan, prompt_bundle = self._load_upstream(project)
            prompt_item = self._prompt_item(prompt_bundle, beat_id)
            prompt_revision = self._current_prompt_revision(project, prompt_item)
            state = self._ensure_state(project, prompt_item)
            self._assert_expected_version(state, expected_beat_version)
            self._assert_mutable_beat(state, action="upload")
            if self._is_locked(state):
                raise AppError(
                    "SCENE_ASSET_APPROVED_IMAGE_LOCKED",
                    "Approved Scene Asset beats are locked. Clear approval before uploading a replacement.",
                    409,
                    retryable=False,
                    details={"beat_id": beat_id},
                )
            continuity_snapshot = self._continuity_snapshot(project, prompt_item, require_ready=True)
            artifact_id = generate_id("artifact")
            staged = self.artifacts.stage_artifact_revision(
                logical_id=f"project/{project_id}/scene_assets/{beat_id}/uploads/{candidate_id}",
                artifact_type=ArtifactType.IMAGE,
                filename=f"scene_asset_upload.{image['extension']}",
                content=image["content"],
                artifact_id=artifact_id,
                provider_source="user_upload",
                content_hash=image["sha256"],
            )
            staged.artifact.width = int(image["width"])
            staged.artifact.height = int(image["height"])
            self.session.add(SceneAssetCandidate(
                candidate_id=candidate_id,
                project_id=project_id,
                scene_id=str(prompt_item["scene_id"]),
                beat_id=beat_id,
                artifact_id=artifact_id,
                source_kind="uploaded",
                operation_beat_id=None,
                operation_task_id=None,
                source_prompt_revision_id=prompt_revision.prompt_revision_id,
                source_prompt_bundle_artifact_id=prompt_revision.source_prompt_bundle_artifact_id,
                source_prompt_item_id=prompt_revision.source_prompt_item_id,
                source_prompt_hash=prompt_revision.source_prompt_hash,
                continuity_snapshot_json=continuity_snapshot,
                created_at=utc_now_iso(),
            ))
            now = utc_now_iso()
            state.current_draft_candidate_id = candidate_id
            state.selected_candidate_id = None
            state.resolution_status = "candidate_ready"
            state.review_status = "current_draft"
            state.last_error_json = None
            state.last_progress_at = now
            state.version += 1
            state.updated_at = now
            self.session.flush()
            self.session.commit()
            staged.owns_file = False
        except Exception:
            rollback_safely(self.session, logger=logger)
            cleanup_staged_artifact_safely(staged, logger=logger)
            raise
        return self._mutation_response(project_id, beat_id, artifact_id=staged.artifact_id if staged else None)

    def decide_candidate(
        self,
        project_id: str,
        beat_id: str,
        *,
        candidate_id: str | None,
        decision: str,
        expected_beat_version: int,
    ) -> dict:
        if decision not in {"set-current-draft", "approve", "reject", "clear-approval"}:
            raise AppError("SCENE_ASSET_DECISION_INVALID", "Scene Asset candidate decision is invalid.", 422, retryable=False)
        self.session.execute(text("BEGIN IMMEDIATE"))
        try:
            project = self._project_required(project_id)
            scene_plan, visual_plan, prompt_bundle = self._load_upstream(project)
            prompt_item = self._prompt_item(prompt_bundle, beat_id)
            self._current_prompt_revision(project, prompt_item)
            state = self._ensure_state(project, prompt_item)
            self._assert_expected_version(state, expected_beat_version)
            self._assert_mutable_beat(state, action="review")
            if self._is_locked(state) and decision != "clear-approval":
                raise AppError(
                    "SCENE_ASSET_APPROVED_IMAGE_LOCKED",
                    "Approved Scene Asset beats are locked. Clear approval before changing review state.",
                    409,
                    retryable=False,
                    details={"beat_id": beat_id},
                )
            candidate = None
            if candidate_id is not None:
                candidate = self.session.get(SceneAssetCandidate, candidate_id)
                if candidate is None or candidate.project_id != project_id or candidate.beat_id != beat_id:
                    raise AppError(
                        "SCENE_ASSET_CANDIDATE_NOT_FOUND",
                        "Scene Asset candidate does not belong to the exact beat.",
                        404,
                        retryable=False,
                        details={"beat_id": beat_id, "candidate_id": candidate_id},
                    )
            now = utc_now_iso()
            old_selected = state.selected_candidate_id
            invalidation_plan: ContinuityInvalidationPlan | None = None
            invalidated: list[str] = []
            if decision == "set-current-draft":
                if candidate is None:
                    raise AppError("SCENE_ASSET_CANDIDATE_NOT_FOUND", "Candidate is required.", 404, retryable=False)
                state.current_draft_candidate_id = candidate.candidate_id
                state.selected_candidate_id = None
                state.resolution_status = "candidate_ready"
                state.review_status = "current_draft"
            elif decision == "reject":
                if candidate is None:
                    raise AppError("SCENE_ASSET_CANDIDATE_NOT_FOUND", "Candidate is required.", 404, retryable=False)
                state.current_draft_candidate_id = candidate.candidate_id
                state.selected_candidate_id = None
                state.resolution_status = "candidate_ready"
                state.review_status = "rejected"
            elif decision == "approve":
                if candidate is None:
                    raise AppError("SCENE_ASSET_CANDIDATE_NOT_FOUND", "Candidate is required.", 404, retryable=False)
                self._assert_candidate_current_prompt(project, prompt_item, candidate)
                self._assert_candidate_current_continuity(project, prompt_item, candidate)
                self._assert_image_artifact(project, beat_id, self.artifact_rows.get(candidate.artifact_id))
                if old_selected != candidate.candidate_id:
                    invalidation_plan = self.continuity.plan(
                        project_id,
                        beat_id,
                        new_source_candidate_id=candidate.candidate_id,
                    )
                state.current_draft_candidate_id = candidate.candidate_id
                state.selected_candidate_id = candidate.candidate_id
                state.resolution_status = "approved"
                state.review_status = "approved"
                state.continuity_source_scene_id = None
                state.continuity_source_beat_id = None
                state.continuity_source_candidate_id = None
            else:
                if state.selected_candidate_id is not None:
                    invalidation_plan = self.continuity.plan(project_id, beat_id)
                state.selected_candidate_id = None
                if state.current_draft_candidate_id is not None:
                    state.resolution_status = "candidate_ready"
                    state.review_status = "current_draft"
                else:
                    state.resolution_status = "not_generated"
                    state.review_status = "unreviewed"
            if invalidation_plan is not None:
                invalidated = self.continuity.apply(invalidation_plan, now_iso=now)
            state.last_error_json = None
            state.last_progress_at = now
            state.version += 1
            state.updated_at = now
            self.session.flush()
            response = self._mutation_response_from_loaded_workspace(
                project,
                scene_plan,
                visual_plan,
                prompt_bundle,
                beat_id,
                invalidated_beat_ids=invalidated,
            )
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return response


    def approve_section_ready(
        self,
        project_id: str,
        section_id: str,
        *,
        beats: list[dict],
    ) -> dict:
        """Approve frozen current-draft candidates in Visual Plan order as one transaction."""

        self.session.execute(text("BEGIN IMMEDIATE"))
        try:
            project = self._project_required(project_id)
            scene_plan, visual_plan, prompt_bundle = self._load_upstream(project)
            section_beat_ids = [
                str(beat["beat_id"])
                for scene in visual_plan.get("scenes", [])
                if str(scene.get("section_id") or "") == section_id
                for beat in scene.get("beat_plans", [])
            ]
            if not section_beat_ids:
                raise AppError(
                    "SCENE_ASSET_SECTION_NOT_FOUND",
                    "Scene Asset section was not found in the current Visual Plan.",
                    404,
                    retryable=False,
                    details={"section_id": section_id},
                )

            requested: dict[str, dict] = {}
            for item in beats:
                beat_id = str(item["beat_id"])
                if beat_id in requested:
                    raise AppError(
                        "SCENE_ASSET_SECTION_APPROVAL_REQUEST_INVALID",
                        "Section approval request contains the same beat more than once.",
                        422,
                        retryable=False,
                        details={"section_id": section_id, "beat_id": beat_id},
                    )
                requested[beat_id] = item

            outside_scope = sorted(set(requested) - set(section_beat_ids))
            if outside_scope:
                raise AppError(
                    "SCENE_ASSET_SECTION_APPROVAL_SCOPE_INVALID",
                    "Section approval request contains beats outside the selected section.",
                    409,
                    retryable=False,
                    details={"section_id": section_id, "beat_ids": outside_scope},
                )

            approved: list[str] = []
            skipped: list[str] = []
            invalidated: list[str] = []
            invalidated_set: set[str] = set()
            now = utc_now_iso()

            for beat_id in section_beat_ids:
                item = requested.get(beat_id)
                if item is None:
                    continue
                if beat_id in invalidated_set:
                    skipped.append(beat_id)
                    continue

                prompt_item = self._prompt_item(prompt_bundle, beat_id)
                self._current_prompt_revision(project, prompt_item)
                state = self._ensure_state(project, prompt_item)
                self._assert_expected_version(state, int(item["expected_beat_version"]))
                self._assert_mutable_beat(state, action="review")
                if self._is_locked(state):
                    raise AppError(
                        "SCENE_ASSET_APPROVED_IMAGE_LOCKED",
                        "Approved Scene Asset beats are locked.",
                        409,
                        retryable=False,
                        details={"beat_id": beat_id},
                    )

                candidate_id = str(item["candidate_id"])
                candidate = self.session.get(SceneAssetCandidate, candidate_id)
                if (
                    candidate is None
                    or candidate.project_id != project_id
                    or candidate.beat_id != beat_id
                ):
                    raise AppError(
                        "SCENE_ASSET_CANDIDATE_NOT_FOUND",
                        "Scene Asset candidate does not belong to the exact beat.",
                        404,
                        retryable=False,
                        details={"beat_id": beat_id, "candidate_id": candidate_id},
                    )
                if state.current_draft_candidate_id != candidate.candidate_id:
                    raise AppError(
                        "SCENE_ASSET_SECTION_APPROVAL_STALE",
                        "Section approval candidate is no longer the current draft.",
                        409,
                        retryable=True,
                        details={
                            "beat_id": beat_id,
                            "candidate_id": candidate_id,
                            "current_draft_candidate_id": state.current_draft_candidate_id,
                        },
                    )

                self._assert_candidate_current_prompt(project, prompt_item, candidate)
                self._assert_candidate_current_continuity(project, prompt_item, candidate)
                self._assert_image_artifact(
                    project,
                    beat_id,
                    self.artifact_rows.get(candidate.artifact_id),
                )

                invalidation_plan: ContinuityInvalidationPlan | None = None
                if state.selected_candidate_id != candidate.candidate_id:
                    invalidation_plan = self.continuity.plan(
                        project_id,
                        beat_id,
                        new_source_candidate_id=candidate.candidate_id,
                    )

                state.current_draft_candidate_id = candidate.candidate_id
                state.selected_candidate_id = candidate.candidate_id
                state.resolution_status = "approved"
                state.review_status = "approved"
                state.continuity_source_scene_id = None
                state.continuity_source_beat_id = None
                state.continuity_source_candidate_id = None

                newly_invalidated: list[str] = []
                if invalidation_plan is not None:
                    newly_invalidated = self.continuity.apply(
                        invalidation_plan,
                        now_iso=now,
                    )
                for dependent_id in newly_invalidated:
                    if dependent_id not in invalidated_set:
                        invalidated_set.add(dependent_id)
                        invalidated.append(dependent_id)
                    if dependent_id in approved:
                        approved.remove(dependent_id)
                        if dependent_id in requested and dependent_id not in skipped:
                            skipped.append(dependent_id)

                state.last_error_json = None
                state.last_progress_at = now
                state.version += 1
                state.updated_at = now
                self.session.flush()
                approved.append(beat_id)

            actions = self._workspace_actions_payload(
                project,
                scene_plan,
                visual_plan,
                prompt_bundle,
            )
            response = {
                "project_id": project_id,
                "section_id": section_id,
                **self._publication_projection_payload(project, scene_plan, visual_plan, prompt_bundle, actions=actions),
                "approved_beat_ids": approved,
                "skipped_beat_ids": skipped,
                "invalidated_beat_ids": invalidated,
                "actions": actions,
            }
            self.session.commit()
            return response
        except Exception:
            self.session.rollback()
            raise


__all__ = ["SceneAssetWorkspaceV2Service"]
