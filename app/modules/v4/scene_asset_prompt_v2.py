from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.errors import AppError
from app.core.time import utc_now_iso
from app.db.models import Project
from app.db.scene_asset_beat_state import SceneAssetBeatState
from app.modules.v4.prompt_composer_service import PromptComposerService
from app.modules.v4.scene_asset_continuity import (
    ContinuityInvalidationPlan,
    SceneAssetContinuityInvalidationService,
)
from app.modules.v4.scene_asset_prompt_revision import SceneAssetPromptRevisionService
from app.repositories import ProjectRepository


class SceneAssetPromptV2Service:
    """Prompt mutation boundary coordinated with beat state and PromptRevision.

    Prompt Bundle remains aggregate immutable authoring authority. PromptRevision
    is the beat-local optimistic concurrency identity. The aggregate bundle ID is
    derived from the expected PromptRevision rather than supplied by the client.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.projects = ProjectRepository(session)
        self.continuity = SceneAssetContinuityInvalidationService(session)
        self.prompt_revisions = SceneAssetPromptRevisionService(session)

    def edit(
        self,
        project_id: str,
        beat_id: str,
        *,
        expected_beat_version: int,
        expected_prompt_revision_id: str,
        image_prompt: str,
        negative_prompt: str,
    ) -> dict:
        return self._mutate(
            project_id,
            beat_id,
            expected_beat_version=expected_beat_version,
            expected_prompt_revision_id=expected_prompt_revision_id,
            image_prompt=image_prompt,
            negative_prompt=negative_prompt,
            restore=False,
        )

    def restore(
        self,
        project_id: str,
        beat_id: str,
        *,
        expected_beat_version: int,
        expected_prompt_revision_id: str,
    ) -> dict:
        return self._mutate(
            project_id,
            beat_id,
            expected_beat_version=expected_beat_version,
            expected_prompt_revision_id=expected_prompt_revision_id,
            image_prompt=None,
            negative_prompt=None,
            restore=True,
        )

    def _mutate(
        self,
        project_id: str,
        beat_id: str,
        *,
        expected_beat_version: int,
        expected_prompt_revision_id: str,
        image_prompt: str | None,
        negative_prompt: str | None,
        restore: bool,
    ) -> dict:
        try:
            self.session.execute(text("BEGIN IMMEDIATE"))
            project = self._project(project_id)
            expected_revision = self.prompt_revisions.assert_current(
                project_id,
                beat_id,
                expected_prompt_revision_id,
            )
            state = self._state(project_id, beat_id)
            self._assert_expected_version(state, beat_id, expected_beat_version)
            if state is not None and state.active_operation_beat_id is not None:
                raise AppError(
                    "SCENE_ASSET_V2_BEAT_BUSY",
                    "Scene Asset beat has active generation and its prompt cannot be changed yet.",
                    409,
                    retryable=True,
                    details={
                        "beat_id": beat_id,
                        "operation_beat_id": state.active_operation_beat_id,
                    },
                )

            request = {
                "style_bible_artifact_id": str(project.style_bible_artifact_id),
                "visual_plan_artifact_id": str(project.visual_plan_artifact_id),
                "expected_prompt_bundle_artifact_id": expected_revision.source_prompt_bundle_artifact_id,
            }
            if not restore:
                request.update(
                    image_prompt=str(image_prompt or ""),
                    negative_prompt=str(negative_prompt or ""),
                )

            def before_commit() -> None:
                self.prompt_revisions.assert_current(
                    project_id,
                    beat_id,
                    expected_prompt_revision_id,
                )
                current_state = self._state(project_id, beat_id)
                self._assert_expected_version(current_state, beat_id, expected_beat_version)
                if current_state is not None and current_state.active_operation_beat_id is not None:
                    raise AppError(
                        "SCENE_ASSET_V2_BEAT_BUSY",
                        "Scene Asset beat became busy before the prompt mutation could commit.",
                        409,
                        retryable=True,
                        details={
                            "beat_id": beat_id,
                            "operation_beat_id": current_state.active_operation_beat_id,
                        },
                    )
                if current_state is not None:
                    self._invalidate_target_and_dependents(current_state)

            composer = PromptComposerService(self.session)
            if restore:
                return composer.recompile_beat(
                    project_id,
                    beat_id,
                    request,
                    before_commit=before_commit,
                )
            return composer.edit_beat(
                project_id,
                beat_id,
                request,
                before_commit=before_commit,
            )
        except Exception:
            self.session.rollback()
            raise

    @staticmethod
    def _assert_expected_version(
        state: SceneAssetBeatState | None,
        beat_id: str,
        expected_beat_version: int,
    ) -> None:
        current_version = int(state.version) if state is not None else 1
        if current_version != int(expected_beat_version):
            raise AppError(
                "SCENE_ASSET_V2_BEAT_STATE_CONFLICT",
                "Scene Asset beat changed since the client loaded it.",
                409,
                retryable=True,
                details={
                    "beat_id": beat_id,
                    "expected_beat_version": int(expected_beat_version),
                    "current_beat_version": current_version,
                },
            )

    def _invalidate_target_and_dependents(self, state: SceneAssetBeatState) -> None:
        now = utc_now_iso()
        plan: ContinuityInvalidationPlan | None = None
        if state.selected_candidate_id is not None:
            plan = self.continuity.plan(state.project_id, state.beat_id)

        state.selected_candidate_id = None
        state.current_draft_candidate_id = None
        state.resolution_status = "not_generated"
        state.review_status = "unreviewed"
        state.last_error_json = None
        state.last_progress_at = now
        state.version += 1
        state.updated_at = now

        if plan is not None:
            self.continuity.apply(
                plan,
                now_iso=now,
                error_message="Approved continuity source prompt changed; regenerate this beat.",
            )

    def _project(self, project_id: str) -> Project:
        project = self.projects.get(project_id)
        if project is None:
            raise AppError("PROJECT_NOT_FOUND", f"Project '{project_id}' was not found.", 404, retryable=False)
        if not project.style_bible_artifact_id or not project.visual_plan_artifact_id or not project.prompt_bundle_artifact_id:
            raise AppError(
                "PROMPT_COMPOSER_PREREQUISITES_REQUIRED",
                "Current Style Bible, Visual Plan, and Prompt Bundle are required.",
                409,
                retryable=False,
            )
        return project

    def _state(self, project_id: str, beat_id: str) -> SceneAssetBeatState | None:
        return self.session.get(SceneAssetBeatState, (project_id, beat_id))
