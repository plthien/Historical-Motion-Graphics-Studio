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


class SceneAssetBeatState(Base):
    __tablename__ = "scene_asset_beat_states"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "scene_id", "beat_id",
            name="ux_scene_asset_beat_states_scene_owner",
        ),
        ForeignKeyConstraint(
            ["current_draft_candidate_id", "project_id", "scene_id", "beat_id"],
            [
                "scene_asset_candidates.candidate_id",
                "scene_asset_candidates.project_id",
                "scene_asset_candidates.scene_id",
                "scene_asset_candidates.beat_id",
            ],
            name="fk_scene_asset_beat_states_current_draft_owner",
        ),
        ForeignKeyConstraint(
            ["selected_candidate_id", "project_id", "scene_id", "beat_id"],
            [
                "scene_asset_candidates.candidate_id",
                "scene_asset_candidates.project_id",
                "scene_asset_candidates.scene_id",
                "scene_asset_candidates.beat_id",
            ],
            name="fk_scene_asset_beat_states_selected_owner",
        ),
        ForeignKeyConstraint(
            ["active_operation_beat_id", "project_id", "scene_id", "beat_id"],
            [
                "scene_asset_operation_beats.operation_beat_id",
                "scene_asset_operation_beats.project_id",
                "scene_asset_operation_beats.scene_id",
                "scene_asset_operation_beats.beat_id",
            ],
            name="fk_scene_asset_beat_states_active_operation_beat_owner",
        ),
        ForeignKeyConstraint(
            ["project_id", "continuity_source_scene_id", "continuity_source_beat_id"],
            [
                "scene_asset_beat_states.project_id",
                "scene_asset_beat_states.scene_id",
                "scene_asset_beat_states.beat_id",
            ],
            name="fk_scene_asset_beat_states_continuity_source",
        ),
        ForeignKeyConstraint(
            ["continuity_source_candidate_id", "project_id", "continuity_source_scene_id", "continuity_source_beat_id"],
            [
                "scene_asset_candidates.candidate_id",
                "scene_asset_candidates.project_id",
                "scene_asset_candidates.scene_id",
                "scene_asset_candidates.beat_id",
            ],
            name="fk_scene_asset_beat_states_continuity_candidate_owner",
        ),
        CheckConstraint("version >= 1", name="ck_scene_asset_beat_states_version_positive"),
        CheckConstraint(
            "resolution_status IN ('not_generated', 'candidate_ready', 'approved', 'failed')",
            name="ck_scene_asset_beat_states_resolution_status",
        ),
        CheckConstraint(
            "review_status IN ('unreviewed', 'current_draft', 'approved', 'rejected')",
            name="ck_scene_asset_beat_states_review_status",
        ),
        CheckConstraint(
            "(continuity_source_scene_id IS NULL AND continuity_source_beat_id IS NULL AND continuity_source_candidate_id IS NULL) OR "
            "(continuity_source_scene_id IS NOT NULL AND continuity_source_beat_id IS NOT NULL AND continuity_source_candidate_id IS NOT NULL)",
            name="ck_scene_asset_beat_states_continuity_pair",
        ),
        CheckConstraint(
            "review_status != 'current_draft' OR current_draft_candidate_id IS NOT NULL",
            name="ck_scene_asset_beat_states_current_draft_review",
        ),
        CheckConstraint(
            "(resolution_status != 'approved' OR (review_status = 'approved' AND selected_candidate_id IS NOT NULL)) AND "
            "(review_status != 'approved' OR (resolution_status = 'approved' AND selected_candidate_id IS NOT NULL))",
            name="ck_scene_asset_beat_states_approved_consistency",
        ),
        Index(
            "ix_scene_asset_beat_states_active_operation_beat",
            "active_operation_beat_id",
        ),
        Index(
            "ix_scene_asset_beat_states_project_resolution",
            "project_id", "resolution_status",
        ),
    )

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id"), primary_key=True)
    beat_id: Mapped[str] = mapped_column(String, primary_key=True)
    scene_id: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    resolution_status: Mapped[str] = mapped_column(String, nullable=False)
    review_status: Mapped[str] = mapped_column(String, nullable=False)

    current_draft_candidate_id: Mapped[str | None] = mapped_column(String, nullable=True)
    selected_candidate_id: Mapped[str | None] = mapped_column(String, nullable=True)
    active_operation_beat_id: Mapped[str | None] = mapped_column(String, nullable=True)

    continuity_source_scene_id: Mapped[str | None] = mapped_column(String, nullable=True)
    continuity_source_beat_id: Mapped[str | None] = mapped_column(String, nullable=True)
    continuity_source_candidate_id: Mapped[str | None] = mapped_column(String, nullable=True)

    last_error_json: Mapped[dict[str, Any] | None] = mapped_column(JSONText(), nullable=True)
    last_progress_at: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
