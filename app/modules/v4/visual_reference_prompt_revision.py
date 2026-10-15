from __future__ import annotations

from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.api.errors import AppError
from app.core.ids import generate_id
from app.core.time import utc_now_iso
from app.db.visual_reference_anchor_state import VisualReferenceAnchorState
from app.db.visual_reference_prompt_revision import VisualReferencePromptRevision
from app.repositories.visual_reference_working import VisualReferenceWorkingRepository


class VisualReferencePromptRevisionService:
    """Immutable per-anchor Visual Reference prompt lineage owner."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = VisualReferenceWorkingRepository(session)

    def current(
        self,
        *,
        project_id: str,
        anchor_type: str,
        anchor_id: str,
    ) -> VisualReferencePromptRevision | None:
        return self.session.scalar(
            select(VisualReferencePromptRevision).where(
                VisualReferencePromptRevision.project_id == project_id,
                VisualReferencePromptRevision.anchor_type == anchor_type,
                VisualReferencePromptRevision.anchor_id == anchor_id,
                VisualReferencePromptRevision.is_current == 1,
            )
        )

    def ensure_compiled(
        self,
        *,
        project_id: str,
        style_bible_artifact_id: str,
        anchor: dict[str, Any],
        now_iso: str | None = None,
    ) -> VisualReferencePromptRevision:
        now = now_iso or utc_now_iso()
        anchor_type = str(anchor["anchor_type"])
        anchor_id = str(anchor["anchor_id"])
        source_definition_hash = str(anchor["source_definition_hash"])
        prompt = str(anchor["prompt"])
        negative_prompt = str(anchor.get("negative_prompt", ""))
        current = self.current(
            project_id=project_id,
            anchor_type=anchor_type,
            anchor_id=anchor_id,
        )
        state = self.repo.get_anchor_state(project_id, anchor_type, anchor_id)

        if current is not None:
            same_definition = (
                current.source_style_bible_artifact_id == style_bible_artifact_id
                and current.source_definition_hash == source_definition_hash
            )
            if same_definition and (
                current.revision_kind == "manual_edit"
                or (current.prompt == prompt and current.negative_prompt == negative_prompt)
            ):
                if state is None:
                    self._create_state(
                        project_id=project_id,
                        anchor_type=anchor_type,
                        anchor_id=anchor_id,
                        prompt_revision_id=current.prompt_revision_id,
                        now_iso=now,
                    )
                elif state.current_prompt_revision_id != current.prompt_revision_id:
                    raise AppError(
                        "VISUAL_REFERENCE_V2_STATE_INTEGRITY_INVALID",
                        "AnchorState current prompt does not match the current PromptRevision.",
                        409,
                        retryable=False,
                        details={"anchor_type": anchor_type, "anchor_id": anchor_id},
                    )
                return current
            if state is not None and state.active_operation_anchor_id is not None:
                raise AppError(
                    "VISUAL_REFERENCE_V2_ANCHOR_BUSY",
                    "Visual Reference prompt lineage cannot advance while the anchor has active generation.",
                    409,
                    retryable=True,
                    details={
                        "anchor_type": anchor_type,
                        "anchor_id": anchor_id,
                        "active_operation_anchor_id": state.active_operation_anchor_id,
                    },
                )

        revision = self._create_revision(
            project_id=project_id,
            anchor_type=anchor_type,
            anchor_id=anchor_id,
            revision_kind="compiled",
            style_bible_artifact_id=style_bible_artifact_id,
            source_definition_hash=source_definition_hash,
            prompt=prompt,
            negative_prompt=negative_prompt,
            now_iso=now,
            current=current,
        )
        if state is None:
            self._create_state(
                project_id=project_id,
                anchor_type=anchor_type,
                anchor_id=anchor_id,
                prompt_revision_id=revision.prompt_revision_id,
                now_iso=now,
            )
        else:
            expected_version = int(state.version)
            if not self.repo.compare_and_set_anchor_state(
                project_id=project_id,
                anchor_type=anchor_type,
                anchor_id=anchor_id,
                expected_version=expected_version,
                values={
                    "current_prompt_revision_id": revision.prompt_revision_id,
                    "resolution_status": "not_generated",
                    "review_status": "unreviewed",
                    "selected_candidate_id": None,
                    "approved_candidate_id": None,
                    "last_error_json": None,
                    "last_progress_at": now,
                },
                updated_at=now,
            ):
                raise AppError(
                    "VISUAL_REFERENCE_V2_ANCHOR_STATE_CONFLICT",
                    "Visual Reference anchor changed while compiled prompt lineage advanced.",
                    409,
                    retryable=True,
                    details={"anchor_type": anchor_type, "anchor_id": anchor_id},
                )
        self.session.flush()
        return revision

    def manual_edit(
        self,
        *,
        project_id: str,
        anchor_type: str,
        anchor_id: str,
        expected_version: int,
        prompt: str,
        negative_prompt: str,
        now_iso: str | None = None,
    ) -> tuple[VisualReferencePromptRevision, int]:
        now = now_iso or utc_now_iso()
        if not prompt.strip():
            raise AppError(
                "VISUAL_REFERENCE_PROMPT_INVALID",
                "Visual Reference prompt cannot be empty.",
                422,
                retryable=False,
            )
        state = self.repo.get_anchor_state(project_id, anchor_type, anchor_id)
        current = self.current(project_id=project_id, anchor_type=anchor_type, anchor_id=anchor_id)
        if state is None or current is None:
            raise AppError(
                "VISUAL_REFERENCE_V2_STATE_NOT_FOUND",
                "Visual Reference prompt state was not found.",
                404,
                retryable=False,
                details={"anchor_type": anchor_type, "anchor_id": anchor_id},
            )
        if state.active_operation_anchor_id is not None:
            raise AppError(
                "VISUAL_REFERENCE_V2_ANCHOR_BUSY",
                "Visual Reference prompt cannot be edited during active generation.",
                409,
                retryable=True,
                details={"anchor_type": anchor_type, "anchor_id": anchor_id},
            )
        if int(state.version) != expected_version:
            raise AppError(
                "VISUAL_REFERENCE_V2_ANCHOR_STATE_CONFLICT",
                "Visual Reference anchor changed before the prompt edit committed.",
                409,
                retryable=True,
                details={"expected_version": expected_version, "actual_version": int(state.version)},
            )
        if current.prompt == prompt and current.negative_prompt == negative_prompt:
            return current, int(state.version)

        revision = self._create_revision(
            project_id=project_id,
            anchor_type=anchor_type,
            anchor_id=anchor_id,
            revision_kind="manual_edit",
            style_bible_artifact_id=current.source_style_bible_artifact_id,
            source_definition_hash=current.source_definition_hash,
            prompt=prompt,
            negative_prompt=negative_prompt,
            now_iso=now,
            current=current,
        )
        if not self.repo.compare_and_set_anchor_state(
            project_id=project_id,
            anchor_type=anchor_type,
            anchor_id=anchor_id,
            expected_version=expected_version,
            values={
                "current_prompt_revision_id": revision.prompt_revision_id,
                "last_error_json": None,
                "last_progress_at": now,
            },
            updated_at=now,
        ):
            raise AppError(
                "VISUAL_REFERENCE_V2_ANCHOR_STATE_CONFLICT",
                "Visual Reference anchor changed before the prompt edit committed.",
                409,
                retryable=True,
                details={"expected_version": expected_version},
            )
        self.session.flush()
        refreshed = self.repo.get_anchor_state(project_id, anchor_type, anchor_id)
        if refreshed is None:
            raise AppError("VISUAL_REFERENCE_V2_STATE_NOT_FOUND", "Visual Reference AnchorState disappeared.", 409, retryable=True)
        return revision, int(refreshed.version)

    def _create_revision(
        self,
        *,
        project_id: str,
        anchor_type: str,
        anchor_id: str,
        revision_kind: str,
        style_bible_artifact_id: str,
        source_definition_hash: str,
        prompt: str,
        negative_prompt: str,
        now_iso: str,
        current: VisualReferencePromptRevision | None,
    ) -> VisualReferencePromptRevision:
        next_revision = int(
            self.session.scalar(
                select(func.max(VisualReferencePromptRevision.revision_no)).where(
                    VisualReferencePromptRevision.project_id == project_id,
                    VisualReferencePromptRevision.anchor_type == anchor_type,
                    VisualReferencePromptRevision.anchor_id == anchor_id,
                )
            )
            or 0
        ) + 1
        if current is not None:
            changed = self.session.execute(
                update(VisualReferencePromptRevision)
                .where(
                    VisualReferencePromptRevision.prompt_revision_id == current.prompt_revision_id,
                    VisualReferencePromptRevision.is_current == 1,
                )
                .values(is_current=0)
            )
            if int(changed.rowcount or 0) != 1:
                raise AppError(
                    "VISUAL_REFERENCE_V2_PROMPT_REVISION_CONFLICT",
                    "Visual Reference current PromptRevision changed concurrently.",
                    409,
                    retryable=True,
                    details={"anchor_type": anchor_type, "anchor_id": anchor_id},
                )
        revision = VisualReferencePromptRevision(
            prompt_revision_id=generate_id("visual_reference_prompt_revision"),
            project_id=project_id,
            anchor_type=anchor_type,
            anchor_id=anchor_id,
            revision_no=next_revision,
            revision_kind=revision_kind,
            is_current=1,
            source_style_bible_artifact_id=style_bible_artifact_id,
            source_definition_hash=source_definition_hash,
            prompt=prompt,
            negative_prompt=negative_prompt,
            created_at=now_iso,
        )
        self.session.add(revision)
        self.session.flush()
        return revision

    def _create_state(
        self,
        *,
        project_id: str,
        anchor_type: str,
        anchor_id: str,
        prompt_revision_id: str,
        now_iso: str,
    ) -> VisualReferenceAnchorState:
        state = VisualReferenceAnchorState(
            project_id=project_id,
            anchor_type=anchor_type,
            anchor_id=anchor_id,
            version=1,
            resolution_status="not_generated",
            review_status="unreviewed",
            current_prompt_revision_id=prompt_revision_id,
            selected_candidate_id=None,
            approved_candidate_id=None,
            active_operation_anchor_id=None,
            last_error_json=None,
            last_progress_at=None,
            created_at=now_iso,
            updated_at=now_iso,
        )
        self.session.add(state)
        self.session.flush()
        return state
