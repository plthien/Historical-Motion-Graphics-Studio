from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.api.scene_asset_workspace_v2_schemas import (
    CandidateId,
    PositiveInt,
    SceneAssetV2WorkspaceActions,
)
from app.api.visual_plan_schemas_v4 import ArtifactId, BeatId, ProjectId, SectionId


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SceneAssetV2SectionApprovalItem(StrictModel):
    beat_id: BeatId
    candidate_id: CandidateId
    expected_beat_version: PositiveInt


class SceneAssetV2SectionApprovalRequest(StrictModel):
    beats: list[SceneAssetV2SectionApprovalItem] = Field(min_length=1)


class SceneAssetV2SectionApprovalResponse(StrictModel):
    project_id: ProjectId
    section_id: SectionId
    published_manifest_artifact_id: ArtifactId | None
    published_manifest_status: str | None
    manifest_state: Literal["unpublished", "current", "stale"]
    approved_beat_ids: list[BeatId] = Field(default_factory=list)
    skipped_beat_ids: list[BeatId] = Field(default_factory=list)
    invalidated_beat_ids: list[BeatId] = Field(default_factory=list)
    actions: SceneAssetV2WorkspaceActions
