from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SceneAssetPromptRevision(Base):
    __tablename__ = "scene_asset_prompt_revisions"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "beat_id", "revision_no",
            name="ux_scene_asset_prompt_revisions_beat_revision",
        ),
        UniqueConstraint(
            "prompt_revision_id", "project_id", "scene_id", "beat_id",
            name="ux_scene_asset_prompt_revisions_identity_owner",
        ),
        UniqueConstraint(
            "prompt_revision_id", "project_id", "scene_id", "beat_id",
            "source_prompt_bundle_artifact_id", "source_prompt_item_id", "source_prompt_hash",
            name="ux_scene_asset_prompt_revisions_source_identity",
        ),
        CheckConstraint("revision_no >= 1", name="ck_scene_asset_prompt_revisions_revision_positive"),
        CheckConstraint(
            "revision_kind IN ('compiled', 'manual_edit', 'restore_compiled')",
            name="ck_scene_asset_prompt_revisions_kind",
        ),
        Index(
            "ux_scene_asset_prompt_revisions_current",
            "project_id", "beat_id",
            unique=True,
            sqlite_where=text("is_current = 1"),
        ),
        Index(
            "ix_scene_asset_prompt_revisions_bundle",
            "source_prompt_bundle_artifact_id",
        ),
    )

    prompt_revision_id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id"), nullable=False)
    scene_id: Mapped[str] = mapped_column(String, nullable=False)
    beat_id: Mapped[str] = mapped_column(String, nullable=False)
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    revision_kind: Mapped[str] = mapped_column(String, nullable=False)
    is_current: Mapped[bool] = mapped_column(Integer, nullable=False, default=True, server_default=text("1"))
    source_prompt_bundle_artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.artifact_id"), nullable=False)
    source_prompt_item_id: Mapped[str] = mapped_column(String, nullable=False)
    source_prompt_hash: Mapped[str] = mapped_column(String, nullable=False)
    image_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    negative_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
