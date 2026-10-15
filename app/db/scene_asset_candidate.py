from __future__ import annotations

from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import JSONText


class SceneAssetCandidate(Base):
    __tablename__ = "scene_asset_candidates"
    __table_args__ = (
        UniqueConstraint("artifact_id", name="ux_scene_asset_candidates_artifact_id"),
        UniqueConstraint("operation_task_id", name="ux_scene_asset_candidates_operation_task_id"),
        UniqueConstraint(
            "candidate_id", "project_id", "scene_id", "beat_id",
            name="ux_scene_asset_candidates_identity_owner",
        ),
        ForeignKeyConstraint(
            ["operation_beat_id", "project_id", "scene_id", "beat_id"],
            [
                "scene_asset_operation_beats.operation_beat_id",
                "scene_asset_operation_beats.project_id",
                "scene_asset_operation_beats.scene_id",
                "scene_asset_operation_beats.beat_id",
            ],
            name="fk_scene_asset_candidates_operation_beat_owner",
        ),
        ForeignKeyConstraint(
            ["operation_task_id", "operation_beat_id"],
            [
                "scene_asset_operation_tasks.operation_task_id",
                "scene_asset_operation_tasks.operation_beat_id",
            ],
            name="fk_scene_asset_candidates_operation_task_owner",
        ),
        ForeignKeyConstraint(
            [
                "source_prompt_revision_id", "project_id", "scene_id", "beat_id",
                "source_prompt_bundle_artifact_id", "source_prompt_item_id", "source_prompt_hash",
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
            name="fk_scene_asset_candidates_prompt_revision",
        ),
        ForeignKeyConstraint(
            [
                "operation_beat_id", "project_id", "scene_id", "beat_id",
                "source_prompt_revision_id", "source_prompt_bundle_artifact_id", "source_prompt_item_id", "source_prompt_hash",
            ],
            [
                "scene_asset_operation_beats.operation_beat_id",
                "scene_asset_operation_beats.project_id",
                "scene_asset_operation_beats.scene_id",
                "scene_asset_operation_beats.beat_id",
                "scene_asset_operation_beats.prompt_revision_id",
                "scene_asset_operation_beats.prompt_bundle_artifact_id",
                "scene_asset_operation_beats.prompt_item_id",
                "scene_asset_operation_beats.prompt_hash",
            ],
            name="fk_scene_asset_candidates_generated_prompt_owner",
        ),
        CheckConstraint(
            "source_kind IN ('generated', 'uploaded')",
            name="ck_scene_asset_candidates_source_kind",
        ),
        CheckConstraint(
            "(source_kind = 'generated' AND operation_beat_id IS NOT NULL AND operation_task_id IS NOT NULL) OR "
            "(source_kind = 'uploaded' AND operation_beat_id IS NULL AND operation_task_id IS NULL)",
            name="ck_scene_asset_candidates_generated_owner",
        ),
        Index(
            "ix_scene_asset_candidates_project_beat_created",
            "project_id", "beat_id", "created_at",
        ),
    )

    candidate_id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id"), nullable=False)
    scene_id: Mapped[str] = mapped_column(String, nullable=False)
    beat_id: Mapped[str] = mapped_column(String, nullable=False)
    artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.artifact_id"), nullable=False)
    source_kind: Mapped[str] = mapped_column(String, nullable=False)
    operation_beat_id: Mapped[str | None] = mapped_column(String, nullable=True)
    operation_task_id: Mapped[str | None] = mapped_column(String, nullable=True)

    # Candidate lineage records the exact beat-local PromptRevision as well as
    # the aggregate Prompt Bundle/item/hash from which it was created.
    source_prompt_revision_id: Mapped[str] = mapped_column(String, nullable=False)
    source_prompt_bundle_artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.artifact_id"), nullable=False)
    source_prompt_item_id: Mapped[str] = mapped_column(String, nullable=False)
    source_prompt_hash: Mapped[str] = mapped_column(String, nullable=False)
    continuity_snapshot_json: Mapped[dict[str, Any] | None] = mapped_column(JSONText(), nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
