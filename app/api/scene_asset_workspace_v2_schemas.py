from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.api.visual_plan_schemas_v4 import ArtifactId, BeatId, ProjectId, SceneId, SectionId
from app.api.prompt_composer_schemas_v4 import PromptHash, PromptReferenceBinding

NonEmptyText = Annotated[str, Field(min_length=1)]
NonNegativeInt = Annotated[int, Field(ge=0)]
PositiveInt = Annotated[int, Field(ge=1)]
PositiveFloat = Annotated[float, Field(gt=0)]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
OperationId = Annotated[str, Field(pattern=r"^scene_asset_operation_[0-9a-f]{12}$")]
CandidateId = Annotated[str, Field(pattern=r"^scene_asset_candidate_[0-9a-f]{12}$")]
PromptRevisionId = Annotated[str, Field(pattern=r"^scene_asset_prompt_revision_[0-9a-f]{12}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SceneAssetV2Error(StrictModel):
    code: NonEmptyText
    message: NonEmptyText
    details: dict[str, Any] = Field(default_factory=dict)


class SceneAssetV2Readiness(StrictModel):
    ready: bool
    blockers: list[NonEmptyText] = Field(default_factory=list)


class SceneAssetV2OperationSummary(StrictModel):
    operation_id: OperationId
    operation_type: NonEmptyText
    status: Literal["queued", "running", "finalizing", "completed", "partial", "failed", "cancelled", "superseded"]
    progress: Annotated[int, Field(ge=0, le=100)]
    error_code: str | None
    error_message: str | None


class SceneAssetV2Candidate(StrictModel):
    candidate_id: CandidateId
    artifact_id: ArtifactId
    source_kind: Literal["generated", "uploaded"]
    mime_type: NonEmptyText
    sha256: Sha256
    width: NonNegativeInt | None
    height: NonNegativeInt | None
    is_current_draft: bool
    is_approved: bool
    is_rejected: bool
    generated_from_current_prompt: bool


class SceneAssetV2Continuity(StrictModel):
    required: bool
    source_beat_id: BeatId | None
    source_ready: bool
    source_candidate_id: CandidateId | None
    block_reason: str | None


class SceneAssetV2Treatment(StrictModel):
    visual_concept: NonEmptyText
    subject_action: NonEmptyText
    environment_emphasis: NonEmptyText
    framing: NonEmptyText
    camera_angle: NonEmptyText
    camera_movement: NonEmptyText
    composition: NonEmptyText
    lighting_direction: NonEmptyText
    color_emphasis: NonEmptyText
    motion_guidance: NonEmptyText
    continuity_with_previous: NonEmptyText
    continuity_notes: NonEmptyText
    production_notes: NonEmptyText
    negative_requirements: list[NonEmptyText]
    visible_text: list[NonEmptyText]


class SceneAssetV2Beat(StrictModel):
    scene_id: SceneId
    beat_id: BeatId
    beat_order: PositiveInt
    local_beat_order: PositiveInt
    computed_duration_seconds: PositiveFloat
    resolution_status: Literal["not_generated", "queued", "generating", "candidate_ready", "approved", "failed"]
    review_status: Literal["unreviewed", "current_draft", "approved", "rejected"]
    beat_state_version: PositiveInt
    active_operation_beat_id: str | None
    current_draft_candidate_id: CandidateId | None
    selected_candidate_id: CandidateId | None
    prompt_revision_id: PromptRevisionId
    prompt_item_id: NonEmptyText
    prompt_hash: PromptHash
    prompt_origin: Literal["compiled", "manual_edit"]
    image_prompt: NonEmptyText
    negative_prompt: NonEmptyText
    reference_bindings: list[PromptReferenceBinding]
    treatment: SceneAssetV2Treatment
    script_quote: str | None
    candidates: list[SceneAssetV2Candidate]
    continuity: SceneAssetV2Continuity
    approval_change_impact_count: NonNegativeInt
    can_generate: bool
    can_retry: bool
    can_regenerate: bool
    can_upload: bool
    can_review: bool
    can_approve: bool
    error: SceneAssetV2Error | None


class SceneAssetV2Scene(StrictModel):
    scene_id: SceneId
    scene_order: PositiveInt
    section_id: SectionId | None
    section_order: PositiveInt | None
    section_title: str | None
    beats: list[SceneAssetV2Beat]


class SceneAssetV2WorkspaceActions(StrictModel):
    can_generate_all: bool
    can_generate_missing: bool
    can_retry_failed: bool
    can_approve_manifest: bool
    can_publish_manifest: bool
    eligible_generate_count: NonNegativeInt
    failed_count: NonNegativeInt
    approved_count: NonNegativeInt
    total_count: NonNegativeInt


class SceneAssetV2WorkspaceResponse(StrictModel):
    project_id: ProjectId
    readiness: SceneAssetV2Readiness
    current_prompt_bundle_artifact_id: ArtifactId | None
    published_manifest_artifact_id: ArtifactId | None
    published_manifest_status: str | None
    manifest_state: Literal["unpublished", "current", "stale"]
    latest_operation: SceneAssetV2OperationSummary | None
    scenes: list[SceneAssetV2Scene]
    actions: SceneAssetV2WorkspaceActions


class SceneAssetV2BeatResponse(StrictModel):
    project_id: ProjectId
    published_manifest_artifact_id: ArtifactId | None
    published_manifest_status: str | None
    manifest_state: Literal["unpublished", "current", "stale"]
    beat: SceneAssetV2Beat
    actions: SceneAssetV2WorkspaceActions


class SceneAssetV2MutationResponse(StrictModel):
    project_id: ProjectId
    published_manifest_artifact_id: ArtifactId | None
    published_manifest_status: str | None
    manifest_state: Literal["unpublished", "current", "stale"]
    beat: SceneAssetV2Beat
    invalidated_beat_ids: list[BeatId] = Field(default_factory=list)
    actions: SceneAssetV2WorkspaceActions


class SceneAssetV2DecisionRequest(StrictModel):
    expected_beat_version: PositiveInt
    candidate_id: CandidateId | None = None


class SceneAssetV2PromptEditRequest(StrictModel):
    expected_beat_version: PositiveInt
    expected_prompt_revision_id: PromptRevisionId
    image_prompt: NonEmptyText
    negative_prompt: NonEmptyText


class SceneAssetV2PromptRestoreRequest(StrictModel):
    expected_beat_version: PositiveInt
    expected_prompt_revision_id: PromptRevisionId


class SceneAssetV2PublishResponse(StrictModel):
    project_id: ProjectId
    artifact_id: ArtifactId
    status: Literal["approved"]
    beat_count: PositiveInt
    already_current: bool
