from __future__ import annotations

from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import JSONText


class VisualReferenceOperationAnchor(Base):
    __tablename__ = "visual_reference_operation_anchors"
    __table_args__ = (
        UniqueConstraint("operation_id", "anchor_type", "anchor_id", name="ux_visual_reference_operation_anchors_operation_anchor"),
        UniqueConstraint(
            "operation_anchor_id", "project_id", "anchor_type", "anchor_id",
            name="ux_visual_reference_operation_anchors_identity_owner",
        ),
        UniqueConstraint(
            "operation_anchor_id", "candidate_count",
            name="ux_visual_reference_operation_anchors_candidate_count_owner",
        ),
        ForeignKeyConstraint(
            ["operation_id", "project_id"],
            ["visual_reference_working_operations.operation_id", "visual_reference_working_operations.project_id"],
            name="fk_visual_reference_operation_anchors_operation_project",
        ),
        ForeignKeyConstraint(
            ["prompt_revision_id", "project_id", "anchor_type", "anchor_id"],
            [
                "visual_reference_prompt_revisions.prompt_revision_id",
                "visual_reference_prompt_revisions.project_id",
                "visual_reference_prompt_revisions.anchor_type",
                "visual_reference_prompt_revisions.anchor_id",
            ],
            name="fk_visual_reference_operation_anchors_prompt_revision",
        ),
        CheckConstraint("version >= 1", name="ck_visual_reference_operation_anchors_version_positive"),
        CheckConstraint(
            "anchor_type IN ('character', 'location', 'prop', 'recurring_visual_subject')",
            name="ck_visual_reference_operation_anchors_anchor_type",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'candidate_ready', 'failed', 'cancelled')",
            name="ck_visual_reference_operation_anchors_status",
        ),
        CheckConstraint("candidate_count >= 1 AND candidate_count <= 5", name="ck_visual_reference_operation_anchors_candidate_count"),
        Index("ix_visual_reference_operation_anchors_project_status", "project_id", "status"),
        Index("ix_visual_reference_operation_anchors_project_anchor", "project_id", "anchor_type", "anchor_id"),
    )

    operation_anchor_id: Mapped[str] = mapped_column(String, primary_key=True)
    operation_id: Mapped[str] = mapped_column(String, nullable=False)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id"), nullable=False)
    anchor_type: Mapped[str] = mapped_column(String, nullable=False)
    anchor_id: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    status: Mapped[str] = mapped_column(String, nullable=False)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt_revision_id: Mapped[str] = mapped_column(String, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    negative_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    generation_options_json: Mapped[dict[str, Any]] = mapped_column(JSONText(), nullable=False)
    generation_options_hash: Mapped[str] = mapped_column(String, nullable=False)
    source_definition_hash: Mapped[str] = mapped_column(String, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_details_json: Mapped[dict[str, Any] | None] = mapped_column(JSONText(), nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
    completed_at: Mapped[str | None] = mapped_column(String, nullable=True)
