from __future__ import annotations

from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import JSONText


class VisualReferenceAnchorState(Base):
    __tablename__ = "visual_reference_anchor_states"
    __table_args__ = (
        UniqueConstraint("project_id", "anchor_type", "anchor_id", name="ux_visual_reference_anchor_states_owner"),
        ForeignKeyConstraint(
            ["current_prompt_revision_id", "project_id", "anchor_type", "anchor_id"],
            ["visual_reference_prompt_revisions.prompt_revision_id", "visual_reference_prompt_revisions.project_id", "visual_reference_prompt_revisions.anchor_type", "visual_reference_prompt_revisions.anchor_id"],
            name="fk_visual_reference_anchor_states_current_prompt",
        ),
        ForeignKeyConstraint(
            ["selected_candidate_id", "project_id", "anchor_type", "anchor_id"],
            ["visual_reference_candidates.candidate_id", "visual_reference_candidates.project_id", "visual_reference_candidates.anchor_type", "visual_reference_candidates.anchor_id"],
            name="fk_visual_reference_anchor_states_selected_candidate",
        ),
        ForeignKeyConstraint(
            ["approved_candidate_id", "project_id", "anchor_type", "anchor_id"],
            ["visual_reference_candidates.candidate_id", "visual_reference_candidates.project_id", "visual_reference_candidates.anchor_type", "visual_reference_candidates.anchor_id"],
            name="fk_visual_reference_anchor_states_approved_candidate",
        ),
        ForeignKeyConstraint(
            ["active_operation_anchor_id", "project_id", "anchor_type", "anchor_id"],
            ["visual_reference_operation_anchors.operation_anchor_id", "visual_reference_operation_anchors.project_id", "visual_reference_operation_anchors.anchor_type", "visual_reference_operation_anchors.anchor_id"],
            name="fk_visual_reference_anchor_states_active_operation_anchor",
        ),
        CheckConstraint("version >= 1", name="ck_visual_reference_anchor_states_version_positive"),
        CheckConstraint("anchor_type IN ('character', 'location', 'prop', 'recurring_visual_subject')", name="ck_visual_reference_anchor_states_anchor_type"),
        CheckConstraint("resolution_status IN ('not_generated', 'candidate_ready', 'approved', 'failed')", name="ck_visual_reference_anchor_states_resolution_status"),
        CheckConstraint("review_status IN ('unreviewed', 'draft_selected', 'approved', 'rejected')", name="ck_visual_reference_anchor_states_review_status"),
        CheckConstraint("review_status != 'draft_selected' OR selected_candidate_id IS NOT NULL", name="ck_visual_reference_anchor_states_selected_review"),
        CheckConstraint(
            "(resolution_status != 'approved' OR (review_status = 'approved' AND approved_candidate_id IS NOT NULL)) AND "
            "(review_status != 'approved' OR (resolution_status = 'approved' AND approved_candidate_id IS NOT NULL))",
            name="ck_visual_reference_anchor_states_approved_consistency",
        ),
        Index("ix_visual_reference_anchor_states_project_resolution", "project_id", "resolution_status"),
        Index("ix_visual_reference_anchor_states_active_operation_anchor", "active_operation_anchor_id"),
    )

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id"), primary_key=True)
    anchor_type: Mapped[str] = mapped_column(String, primary_key=True)
    anchor_id: Mapped[str] = mapped_column(String, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    resolution_status: Mapped[str] = mapped_column(String, nullable=False)
    review_status: Mapped[str] = mapped_column(String, nullable=False)
    current_prompt_revision_id: Mapped[str] = mapped_column(String, nullable=False)
    selected_candidate_id: Mapped[str | None] = mapped_column(String, nullable=True)
    approved_candidate_id: Mapped[str | None] = mapped_column(String, nullable=True)
    active_operation_anchor_id: Mapped[str | None] = mapped_column(String, nullable=True)
    last_error_json: Mapped[dict[str, Any] | None] = mapped_column(JSONText(), nullable=True)
    last_progress_at: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
