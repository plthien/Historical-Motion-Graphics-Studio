from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class VisualReferencePromptRevision(Base):
    __tablename__ = "visual_reference_prompt_revisions"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "anchor_type", "anchor_id", "revision_no",
            name="ux_visual_reference_prompt_revisions_anchor_revision",
        ),
        UniqueConstraint(
            "prompt_revision_id", "project_id", "anchor_type", "anchor_id",
            name="ux_visual_reference_prompt_revisions_identity_owner",
        ),
        CheckConstraint("revision_no >= 1", name="ck_visual_reference_prompt_revisions_revision_positive"),
        CheckConstraint(
            "anchor_type IN ('character', 'location', 'prop', 'recurring_visual_subject')",
            name="ck_visual_reference_prompt_revisions_anchor_type",
        ),
        CheckConstraint(
            "revision_kind IN ('compiled', 'manual_edit', 'restore_compiled')",
            name="ck_visual_reference_prompt_revisions_kind",
        ),
        Index(
            "ux_visual_reference_prompt_revisions_current",
            "project_id", "anchor_type", "anchor_id",
            unique=True,
            sqlite_where=text("is_current = 1"),
        ),
        Index("ix_visual_reference_prompt_revisions_style_bible", "source_style_bible_artifact_id"),
    )

    prompt_revision_id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id"), nullable=False)
    anchor_type: Mapped[str] = mapped_column(String, nullable=False)
    anchor_id: Mapped[str] = mapped_column(String, nullable=False)
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    revision_kind: Mapped[str] = mapped_column(String, nullable=False)
    is_current: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    source_style_bible_artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.artifact_id"), nullable=False)
    source_definition_hash: Mapped[str] = mapped_column(String, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    negative_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
