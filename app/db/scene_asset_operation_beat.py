from __future__ import annotations

from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import JSONText


class SceneAssetOperationBeat(Base):
    __tablename__ = "scene_asset_operation_beats"
    __table_args__ = (
        UniqueConstraint(
            "operation_id", "beat_id",
            name="ux_scene_asset_operation_beats_operation_beat",
        ),
        UniqueConstraint(
            "operation_beat_id", "project_id", "scene_id", "beat_id",
            name="ux_scene_asset_operation_beats_identity_owner",
        ),
        UniqueConstraint(
            "operation_beat_id", "candidate_count",
            name="ux_scene_asset_operation_beats_candidate_count_owner",
        ),
        UniqueConstraint(
            "operation_beat_id", "project_id", "scene_id", "beat_id",
            "prompt_revision_id", "prompt_bundle_artifact_id", "prompt_item_id", "prompt_hash",
            name="ux_scene_asset_operation_beats_prompt_identity",
        ),
        ForeignKeyConstraint(
            ["operation_id", "project_id"],
            ["scene_asset_working_operations.operation_id", "scene_asset_working_operations.project_id"],
            name="fk_scene_asset_operation_beats_operation_project",
        ),
        ForeignKeyConstraint(
            [
                "prompt_revision_id", "project_id", "scene_id", "beat_id",
                "prompt_bundle_artifact_id", "prompt_item_id", "prompt_hash",
            ],
            [
                "scene_asset_prompt_revisions.prompt_revision_id",
                "scene_asset_prompt_revisions.project_id",
                "scene_asset_prompt_revisions.scene_id",
                "scene_asset_prompt_revisions.beat_id",
                "scene_asset_prompt_revisions.source_prompt_bundle_artifact_id",
                "scene_asset_prompt_revisions.source_prompt_item_id",
                "scene_asset_prompt_revisions.source_prompt_hash",
            ],
            name="fk_scene_asset_operation_beats_prompt_revision",
        ),
        CheckConstraint("version >= 1", name="ck_scene_asset_operation_beats_version_positive"),
        CheckConstraint(
            "status IN ('queued', 'running', 'candidate_ready', 'failed', 'cancelled')",
            name="ck_scene_asset_operation_beats_status",
        ),
        CheckConstraint(
            "candidate_count >= 1 AND candidate_count <= 5",
            name="ck_scene_asset_operation_beats_candidate_count",
        ),
        Index(
            "ix_scene_asset_operation_beats_project_status",
            "project_id", "status",
        ),
        Index(
            "ix_scene_asset_operation_beats_project_beat",
            "project_id", "beat_id",
        ),
    )

    operation_beat_id: Mapped[str] = mapped_column(String, primary_key=True)
    operation_id: Mapped[str] = mapped_column(String, nullable=False)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id"), nullable=False)
    scene_id: Mapped[str] = mapped_column(String, nullable=False)
    beat_id: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    status: Mapped[str] = mapped_column(String, nullable=False)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False)

    # PromptRevision is the beat-local prompt identity. The operation freezes
    # the aggregate current Prompt Bundle separately; a beat may legitimately
    # retain an older source bundle when unrelated beats were edited later.
    prompt_revision_id: Mapped[str] = mapped_column(String, nullable=False)
    prompt_bundle_artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.artifact_id"), nullable=False)
    prompt_item_id: Mapped[str] = mapped_column(String, nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String, nullable=False)
    image_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    negative_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    generation_options_json: Mapped[dict[str, Any]] = mapped_column(JSONText(), nullable=False)
    generation_options_hash: Mapped[str] = mapped_column(String, nullable=False)
    reference_bindings_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONText(), nullable=False)
    reference_bindings_hash: Mapped[str] = mapped_column(String, nullable=False)
    continuity_snapshot_json: Mapped[dict[str, Any] | None] = mapped_column(JSONText(), nullable=True)

    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_details_json: Mapped[dict[str, Any] | None] = mapped_column(JSONText(), nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
    completed_at: Mapped[str | None] = mapped_column(String, nullable=True)
