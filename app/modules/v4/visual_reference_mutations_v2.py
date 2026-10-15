from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.api.errors import AppError
from app.core.ids import generate_id
from app.core.time import utc_now_iso
from app.db.enums import ArtifactType
from app.db.models import Project
from app.db.visual_reference_candidate import VisualReferenceCandidate
from app.db.visual_reference_prompt_revision import VisualReferencePromptRevision
from app.modules.media.image_upload_validation import validate_uploaded_image
from app.modules.v4.visual_reference_prompt_revision import VisualReferencePromptRevisionService
from app.repositories import ArtifactRepository, ProjectRepository
from app.repositories.visual_reference_working import VisualReferenceWorkingRepository
from app.services.artifacts import (
    ArtifactService,
    StagedArtifact,
    cleanup_staged_artifact_safely,
    rollback_safely,
)


logger = logging.getLogger(__name__)


class VisualReferenceMutationsV2:
    """Anchor-local Visual Reference V2 working-state mutations.

    These mutations never materialize VisualReferenceSet or ApprovalSet
    publication artifacts. PromptRevision, Candidate, and AnchorState are the
    mutable-authority boundary until the explicit publication phase.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.projects = ProjectRepository(session)
        self.artifacts = ArtifactService(session, ArtifactRepository(session))
        self.repo = VisualReferenceWorkingRepository(session)
        self.prompt_revisions = VisualReferencePromptRevisionService(session)

    def update_prompt(
        self,
        project_id: str,
        anchor_type: str,
        anchor_id: str,
        *,
        expected_version: int,
        prompt: str,
        negative_prompt: str,
    ) -> dict[str, Any]:
        self._project_required(project_id)
        try:
            revision, version = self.prompt_revisions.manual_edit(
                project_id=project_id,
                anchor_type=anchor_type,
                anchor_id=anchor_id,
                expected_version=expected_version,
                prompt=prompt,
                negative_prompt=negative_prompt,
                now_iso=utc_now_iso(),
            )
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        state = self.repo.get_anchor_state(project_id, anchor_type, anchor_id)
        if state is None:
            raise self._not_found(anchor_type, anchor_id)
        return {
            "project_id": project_id,
            "anchor_type": anchor_type,
            "anchor_id": anchor_id,
            "version": version,
            "prompt_revision_id": revision.prompt_revision_id,
            "prompt": revision.prompt,
            "negative_prompt": revision.negative_prompt,
            "resolution_status": state.resolution_status,
            "review_status": state.review_status,
            "selected_candidate_id": state.selected_candidate_id,
            "approved_candidate_id": state.approved_candidate_id,
        }

    def upload_reference(
        self,
        project_id: str,
        anchor_type: str,
        anchor_id: str,
        *,
        expected_version: int,
        content: bytes,
        content_type: str | None,
    ) -> dict[str, Any]:
        project = self._project_required(project_id)
        image = validate_uploaded_image(
            content,
            declared_content_type=content_type,
            error_prefix="VISUAL_REFERENCE_UPLOAD",
            image_label="visual-reference image",
        )
        staged: StagedArtifact | None = None
        try:
            state, revision = self._current_anchor(
                project_id,
                anchor_type,
                anchor_id,
                expected_version=expected_version,
                require_idle=True,
            )
            duplicate = next(
                (
                    candidate
                    for candidate in self.repo.list_candidates_for_anchor(
                        project_id=project_id,
                        anchor_type=anchor_type,
                        anchor_id=anchor_id,
                    )
                    if candidate.media_sha256 == image["sha256"]
                    and self._candidate_matches_definition(candidate, revision)
                ),
                None,
            )
            now = utc_now_iso()
            candidate = duplicate
            if candidate is None:
                media_artifact_id = self.artifacts.preallocate_artifact_id()
                staged = self.artifacts.stage_artifact_revision(
                    logical_id=(
                        f"project/{project_id}/visual_references/"
                        f"{revision.prompt_revision_id}/{anchor_type}/{anchor_id}/uploads/"
                        f"{media_artifact_id}"
                    ),
                    artifact_type=ArtifactType.IMAGE,
                    filename=f"visual_reference_upload.{image['extension']}",
                    artifact_id=media_artifact_id,
                    content=image["content"],
                    provider_source="user_upload",
                    content_hash=image["sha256"],
                )
                staged.artifact.width = int(image["width"])
                staged.artifact.height = int(image["height"])
                staged.artifact.mime_type = str(image["mime_type"])
                candidate = VisualReferenceCandidate(
                    candidate_id=generate_id("visual_reference_candidate"),
                    project_id=project_id,
                    anchor_type=anchor_type,
                    anchor_id=anchor_id,
                    candidate_index=1,
                    source_prompt_revision_id=revision.prompt_revision_id,
                    source_operation_task_id=None,
                    media_artifact_id=media_artifact_id,
                    media_task_id=None,
                    mime_type=str(image["mime_type"]),
                    media_sha256=str(image["sha256"]),
                    provenance_json={
                        "source_kind": "user_upload",
                        "source_definition_hash": revision.source_definition_hash,
                        "source_style_bible_artifact_id": revision.source_style_bible_artifact_id,
                    },
                    created_at=now,
                )
                self.repo.add_candidate(candidate)

            if not self.repo.compare_and_set_anchor_state(
                project_id=project_id,
                anchor_type=anchor_type,
                anchor_id=anchor_id,
                expected_version=expected_version,
                values={
                    "resolution_status": "candidate_ready",
                    "review_status": "draft_selected",
                    "selected_candidate_id": candidate.candidate_id,
                    "approved_candidate_id": None,
                    "last_error_json": None,
                    "last_progress_at": now,
                },
                updated_at=now,
            ):
                raise AppError(
                    "VISUAL_REFERENCE_V2_ANCHOR_STATE_CONFLICT",
                    "Visual Reference anchor changed before upload selection committed.",
                    409,
                    retryable=True,
                    details={
                        "project_id": project_id,
                        "anchor_type": anchor_type,
                        "anchor_id": anchor_id,
                        "expected_version": expected_version,
                    },
                )
            self.session.commit()
        except Exception:
            rollback_safely(self.session, logger=logger)
            cleanup_staged_artifact_safely(staged, logger=logger)
            raise

        refreshed = self.repo.get_anchor_state(project_id, anchor_type, anchor_id)
        if refreshed is None:
            raise self._not_found(anchor_type, anchor_id)
        return {
            "project_id": project_id,
            "anchor_type": anchor_type,
            "anchor_id": anchor_id,
            "version": int(refreshed.version),
            "candidate_id": candidate.candidate_id,
            "media_artifact_id": candidate.media_artifact_id,
            "media_sha256": candidate.media_sha256,
            "mime_type": candidate.mime_type,
            "resolution_status": refreshed.resolution_status,
            "review_status": refreshed.review_status,
            "selected_candidate_id": refreshed.selected_candidate_id,
            "approved_candidate_id": refreshed.approved_candidate_id,
            "published_visual_reference_set_artifact_id": (
                project.style_bible_visual_reference_set_artifact_id
            ),
        }

    def approve(
        self,
        project_id: str,
        anchor_type: str,
        anchor_id: str,
        *,
        expected_version: int,
        candidate_id: str,
    ) -> dict[str, Any]:
        project = self._project_required(project_id)
        state, revision = self._current_anchor(
            project_id,
            anchor_type,
            anchor_id,
            expected_version=expected_version,
            require_idle=True,
        )
        candidate = self.repo.get_candidate(candidate_id)
        if (
            candidate is None
            or candidate.project_id != project_id
            or candidate.anchor_type != anchor_type
            or candidate.anchor_id != anchor_id
            or not self._candidate_matches_definition(candidate, revision)
        ):
            raise AppError(
                "VISUAL_REFERENCE_CANDIDATE_NOT_FOUND",
                "The Visual Reference candidate does not belong to the current anchor definition.",
                404,
                retryable=False,
                details={
                    "anchor_type": anchor_type,
                    "anchor_id": anchor_id,
                    "candidate_id": candidate_id,
                },
            )
        now = utc_now_iso()
        try:
            if not self.repo.compare_and_set_anchor_state(
                project_id=project_id,
                anchor_type=anchor_type,
                anchor_id=anchor_id,
                expected_version=expected_version,
                values={
                    "resolution_status": "approved",
                    "review_status": "approved",
                    "selected_candidate_id": candidate.candidate_id,
                    "approved_candidate_id": candidate.candidate_id,
                    "last_error_json": None,
                    "last_progress_at": now,
                },
                updated_at=now,
            ):
                raise AppError(
                    "VISUAL_REFERENCE_V2_ANCHOR_STATE_CONFLICT",
                    "Visual Reference anchor changed before approval committed.",
                    409,
                    retryable=True,
                    details={"expected_version": expected_version},
                )
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        refreshed = self.repo.get_anchor_state(project_id, anchor_type, anchor_id)
        if refreshed is None:
            raise self._not_found(anchor_type, anchor_id)
        return {
            "project_id": project_id,
            "anchor_type": anchor_type,
            "anchor_id": anchor_id,
            "version": int(refreshed.version),
            "approved_candidate_id": refreshed.approved_candidate_id,
            "selected_candidate_id": refreshed.selected_candidate_id,
            "resolution_status": refreshed.resolution_status,
            "review_status": refreshed.review_status,
            "published_visual_reference_set_artifact_id": (
                project.style_bible_visual_reference_set_artifact_id
            ),
            "published_approval_set_artifact_id": (
                project.style_bible_visual_reference_approval_set_artifact_id
            ),
        }

    def clear_approval(
        self,
        project_id: str,
        anchor_type: str,
        anchor_id: str,
        *,
        expected_version: int,
    ) -> dict[str, Any]:
        project = self._project_required(project_id)
        state, _revision = self._current_anchor(
            project_id,
            anchor_type,
            anchor_id,
            expected_version=expected_version,
            require_idle=True,
        )
        selected = state.selected_candidate_id
        now = utc_now_iso()
        values: dict[str, Any] = {
            "approved_candidate_id": None,
            "last_error_json": None,
            "last_progress_at": now,
        }
        if selected is not None:
            values.update(
                resolution_status="candidate_ready",
                review_status="draft_selected",
            )
        else:
            values.update(
                resolution_status="not_generated",
                review_status="unreviewed",
            )
        try:
            if not self.repo.compare_and_set_anchor_state(
                project_id=project_id,
                anchor_type=anchor_type,
                anchor_id=anchor_id,
                expected_version=expected_version,
                values=values,
                updated_at=now,
            ):
                raise AppError(
                    "VISUAL_REFERENCE_V2_ANCHOR_STATE_CONFLICT",
                    "Visual Reference anchor changed before approval clear committed.",
                    409,
                    retryable=True,
                    details={"expected_version": expected_version},
                )
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        refreshed = self.repo.get_anchor_state(project_id, anchor_type, anchor_id)
        if refreshed is None:
            raise self._not_found(anchor_type, anchor_id)
        return {
            "project_id": project_id,
            "anchor_type": anchor_type,
            "anchor_id": anchor_id,
            "version": int(refreshed.version),
            "approved_candidate_id": None,
            "selected_candidate_id": refreshed.selected_candidate_id,
            "resolution_status": refreshed.resolution_status,
            "review_status": refreshed.review_status,
            "published_visual_reference_set_artifact_id": (
                project.style_bible_visual_reference_set_artifact_id
            ),
            "published_approval_set_artifact_id": (
                project.style_bible_visual_reference_approval_set_artifact_id
            ),
        }

    def _current_anchor(
        self,
        project_id: str,
        anchor_type: str,
        anchor_id: str,
        *,
        expected_version: int,
        require_idle: bool,
    ) -> tuple[Any, VisualReferencePromptRevision]:
        state = self.repo.get_anchor_state(project_id, anchor_type, anchor_id)
        if state is None:
            raise self._not_found(anchor_type, anchor_id)
        if int(state.version) != expected_version:
            raise AppError(
                "VISUAL_REFERENCE_V2_ANCHOR_STATE_CONFLICT",
                "Visual Reference anchor changed before the mutation began.",
                409,
                retryable=True,
                details={
                    "expected_version": expected_version,
                    "actual_version": int(state.version),
                },
            )
        if require_idle and state.active_operation_anchor_id is not None:
            raise AppError(
                "VISUAL_REFERENCE_V2_ANCHOR_BUSY",
                "Visual Reference anchor cannot be mutated during active generation.",
                409,
                retryable=True,
                details={
                    "anchor_type": anchor_type,
                    "anchor_id": anchor_id,
                    "active_operation_anchor_id": state.active_operation_anchor_id,
                },
            )
        revision = self.session.get(
            VisualReferencePromptRevision,
            state.current_prompt_revision_id,
        )
        project = self.projects.get(project_id)
        if (
            revision is None
            or project is None
            or revision.project_id != project_id
            or revision.anchor_type != anchor_type
            or revision.anchor_id != anchor_id
            or revision.source_style_bible_artifact_id != project.style_bible_artifact_id
        ):
            raise AppError(
                "VISUAL_REFERENCE_OPERATION_STALE",
                "Visual Reference anchor prompt lineage is stale for the current Style Bible.",
                409,
                retryable=False,
                details={"anchor_type": anchor_type, "anchor_id": anchor_id},
            )
        return state, revision

    @staticmethod
    def _candidate_matches_definition(
        candidate: VisualReferenceCandidate,
        revision: VisualReferencePromptRevision,
    ) -> bool:
        provenance = dict(candidate.provenance_json or {})
        return (
            provenance.get("source_definition_hash") == revision.source_definition_hash
            and provenance.get("source_style_bible_artifact_id")
            == revision.source_style_bible_artifact_id
        )

    def _project_required(self, project_id: str) -> Project:
        project = self.projects.get(project_id)
        if project is None:
            raise AppError(
                "PROJECT_NOT_FOUND",
                f"Project '{project_id}' was not found.",
                404,
                retryable=False,
            )
        if project.style_bible_artifact_id is None:
            raise AppError(
                "VISUAL_REFERENCE_NOT_READY",
                "A current text-approved Style Bible is required.",
                409,
                retryable=False,
            )
        return project

    @staticmethod
    def _not_found(anchor_type: str, anchor_id: str) -> AppError:
        return AppError(
            "VISUAL_REFERENCE_ANCHOR_NOT_FOUND",
            "The Visual Reference V2 anchor was not found.",
            404,
            retryable=False,
            details={"anchor_type": anchor_type, "anchor_id": anchor_id},
        )
