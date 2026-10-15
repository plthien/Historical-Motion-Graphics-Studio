from __future__ import annotations

from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import JSONText


class VisualReferenceCandidate(Base):
    __tablename__ = "visual_reference_candidates"
    __table_args__ = (
        UniqueConstraint("candidate_id", "project_id", "anchor_type", "anchor_id", name="ux_visual_reference_candidates_identity_owner"),
        ForeignKeyConstraint(
            ["source_prompt_revision_id", "project_id", "anchor_type", "anchor_id"],
            ["visual_reference_prompt_revisions.prompt_revision_id", "visual_reference_prompt_revisions.project_id", "visual_reference_prompt_revisions.anchor_type", "visual_reference_prompt_revisions.anchor_id"],
            name="fk_visual_reference_candidates_prompt_revision",
        ),
        CheckConstraint("anchor_type IN ('character', 'location', 'prop', 'recurring_visual_subject')", name="ck_visual_reference_candidates_anchor_type"),
        CheckConstraint("candidate_index >= 1 AND candidate_index <= 5", name="ck_visual_reference_candidates_candidate_index"),
        Index("ux_visual_reference_candidates_operation_task", "source_operation_task_id", unique=True),
        Index("ix_visual_reference_candidates_anchor", "project_id", "anchor_type", "anchor_id", "created_at"),
    )

    candidate_id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id"), nullable=False)
    anchor_type: Mapped[str] = mapped_column(String, nullable=False)
    anchor_id: Mapped[str] = mapped_column(String, nullable=False)
    candidate_index: Mapped[int] = mapped_column(Integer, nullable=False)
    source_prompt_revision_id: Mapped[str] = mapped_column(String, nullable=False)
    source_operation_task_id: Mapped[str | None] = mapped_column(ForeignKey("visual_reference_operation_tasks.operation_task_id"), nullable=True)
    media_artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.artifact_id"), nullable=False)
    media_task_id: Mapped[str | None] = mapped_column(ForeignKey("media_tasks.media_task_id"), nullable=True)
    mime_type: Mapped[str] = mapped_column(String, nullable=False)
    media_sha256: Mapped[str] = mapped_column(String, nullable=False)
    provenance_json: Mapped[dict[str, Any]] = mapped_column(JSONText(), nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
