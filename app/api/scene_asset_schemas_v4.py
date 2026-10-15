from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.api.visual_plan_schemas_v4 import ArtifactId, BeatId, ProjectId, SceneId, SectionId
from app.api.prompt_composer_schemas_v4 import PromptHash, PromptReferenceBinding
from app.api.media_progress_schemas_v4 import MediaGenerationProgressV4

NonEmptyText = Annotated[str, Field(min_length=1)]
PositiveInt = Annotated[int, Field(ge=1)]
NonNegativeInt = Annotated[int, Field(ge=0)]
PositiveFloat = Annotated[float, Field(gt=0)]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Sha256Identity = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
OperationId = Annotated[str, Field(pattern=r"^scene_asset_operation_[0-9a-f]{12}$")]
JobId = Annotated[str, Field(pattern=r"^job_[0-9a-f]{12}$")]
MediaBatchId = Annotated[str, Field(pattern=r"^media_batch_[0-9a-f]{12}$")]
MediaTaskId = Annotated[str, Field(pattern=r"^media_task_[0-9a-f]{12}$")]
PromptItemId = Annotated[str, Field(pattern=r"^prompt_[0-9]{3,}$")]
SceneAssetManifestRevisionId = ArtifactId
ManifestStatus = Literal["generating", "partial", "draft_ready", "approved", "failed", "superseded"]
ResolutionStatus = Literal["pending", "generating", "candidate_ready", "approved", "failed"]
WorkspaceResolutionStatus = Literal["not_generated", "queued", "generating", "candidate_ready", "approved", "failed"]
ReviewStatus = Literal["unreviewed", "current_draft", "approved", "rejected"]
OperationType = Literal["generate_all", "generate_section", "generate_beat", "generate_missing", "retry_failed", "retry_beat", "regenerate_beat", "regenerate_section"]
OperationStatus = Literal[
    "queued",
    "running",
    "waiting_for_media",
    "finalizing",
    "completed",
    "partial",
    "failed",
    "superseded",
    "cancelled",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SceneAssetError(StrictModel):
    code: NonEmptyText
    message: NonEmptyText
    details: dict[str, Any] = Field(default_factory=dict)


class SceneAssetGenerationLineage(StrictModel):
    scene_asset_operation_id: OperationId
    scene_plan_artifact_id: ArtifactId
    visual_plan_artifact_id: ArtifactId
    prompt_bundle_artifact_id: ArtifactId
    prompt_item_id: PromptItemId
    prompt_hash: PromptHash
    visual_reference_approval_set_artifact_id: ArtifactId | None
    reference_binding_snapshot_hash: Sha256Identity
    generation_settings_snapshot_hash: Sha256Identity


class SceneAssetContinuitySource(StrictModel):
    relation: NonEmptyText
    source_beat_id: BeatId
    source_asset_artifact_id: ArtifactId
    source_asset_sha256: Sha256
    mime_type: NonEmptyText
    source_kind: Literal["approved"]


class SceneAssetTreatment(StrictModel):
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


class SceneAssetManifestBeatEntry(StrictModel):
    scene_id: SceneId
    beat_id: BeatId
    beat_order: PositiveInt
    local_beat_order: PositiveInt
    continuity_group_id: Annotated[str, Field(pattern=r"^continuity_[0-9]{3,}$")]
    computed_duration_seconds: PositiveFloat

    resolution_status: ResolutionStatus
    review_status: ReviewStatus

    selected_asset_artifact_id: ArtifactId | None
    current_draft_asset_artifact_id: ArtifactId | None
    candidate_asset_artifact_ids: list[ArtifactId]

    source_prompt_item_id: PromptItemId
    source_prompt_hash: PromptHash
    media_task_ids: list[MediaTaskId]

    width: NonNegativeInt | None
    height: NonNegativeInt | None
    mime_type: NonEmptyText | None
    sha256: Sha256 | None

    generation_lineage: SceneAssetGenerationLineage
    continuity_source: SceneAssetContinuitySource | None
    treatment: SceneAssetTreatment
    reference_bindings: list[PromptReferenceBinding]
    error: SceneAssetError | None

    @model_validator(mode="after")
    def validate_entry(self) -> "SceneAssetManifestBeatEntry":
        if len(self.candidate_asset_artifact_ids) != len(set(self.candidate_asset_artifact_ids)):
            raise ValueError("candidate assets must be unique")
        if len(self.media_task_ids) != len(set(self.media_task_ids)):
            raise ValueError("media task IDs must be unique")
        candidates = set(self.candidate_asset_artifact_ids)
        if self.current_draft_asset_artifact_id is not None and self.current_draft_asset_artifact_id not in candidates:
            raise ValueError("current draft must belong to the exact beat candidate set")
        if self.selected_asset_artifact_id is not None and self.selected_asset_artifact_id not in candidates:
            raise ValueError("selected asset must belong to the exact beat candidate set")
        if self.resolution_status == "approved":
            if self.review_status != "approved" or self.selected_asset_artifact_id is None:
                raise ValueError("approved beat requires an approved selected candidate")
        if self.review_status == "approved" and self.resolution_status != "approved":
            raise ValueError("approved review state requires approved resolution")
        if self.review_status == "current_draft" and self.current_draft_asset_artifact_id is None:
            raise ValueError("current_draft review state requires a current draft candidate")
        if self.resolution_status == "candidate_ready" and not candidates:
            raise ValueError("candidate_ready requires at least one candidate")
        if self.resolution_status in {"pending", "generating"} and self.selected_asset_artifact_id is not None:
            raise ValueError("unresolved beat must not have a selected asset")
        if self.selected_asset_artifact_id is None:
            if any(value is not None for value in (self.width, self.height, self.mime_type, self.sha256)):
                raise ValueError("asset metadata requires selected_asset_artifact_id")
        elif any(value is None for value in (self.mime_type, self.sha256)):
            raise ValueError("selected asset requires mime type and sha256")
        return self


class SceneAssetManifestScene(StrictModel):
    scene_id: SceneId
    scene_order: PositiveInt
    entries: Annotated[list[SceneAssetManifestBeatEntry], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_scene(self) -> "SceneAssetManifestScene":
        if any(entry.scene_id != self.scene_id for entry in self.entries):
            raise ValueError("manifest entry scene identity mismatch")
        if [entry.local_beat_order for entry in self.entries] != list(range(1, len(self.entries) + 1)):
            raise ValueError("local beat order must be contiguous")
        return self


class SceneAssetValidationIssue(StrictModel):
    path: NonEmptyText
    code: NonEmptyText
    message: NonEmptyText


class SceneAssetValidationReport(StrictModel):
    coverage_status: Literal["passed", "failed"]
    ordering_status: Literal["passed", "failed"]
    prompt_bundle_parity_status: Literal["passed", "failed"]
    asset_integrity_status: Literal["passed", "failed"]
    lineage_status: Literal["passed", "failed"]
    approval_eligibility_status: Literal["passed", "failed"]
    issues: list[SceneAssetValidationIssue]
    warnings: list[NonEmptyText]


class SceneAssetManifestModel(StrictModel):
    artifact_type: Literal["scene_asset_manifest"]
    schema_version: Literal["3.0"]
    project_id: ProjectId

    scene_asset_manifest_revision_id: SceneAssetManifestRevisionId
    parent_scene_asset_manifest_revision_id: SceneAssetManifestRevisionId | None
    revision_operation: Literal[
        "generation",
        "media_progress",
        "candidate_decision",
        "candidate_upload",
        "beat_regeneration",
        "approval",
        "failure",
        "supersession",
    ]

    source_script_revision_id: ArtifactId
    source_script_analysis_revision_id: ArtifactId
    source_style_bible_revision_id: ArtifactId
    source_scene_plan_revision_id: ArtifactId
    source_visual_plan_revision_id: ArtifactId
    source_prompt_bundle_revision_id: ArtifactId
    source_visual_reference_approval_set_artifact_id: ArtifactId | None

    source_operation_id: OperationId
    status: ManifestStatus

    scene_count: NonNegativeInt
    beat_count: NonNegativeInt
    approved_asset_count: NonNegativeInt
    failed_beat_count: NonNegativeInt

    scenes: list[SceneAssetManifestScene]
    backend_qc: SceneAssetValidationReport

    @model_validator(mode="after")
    def validate_manifest(self) -> "SceneAssetManifestModel":
        if self.scene_count != len(self.scenes):
            raise ValueError("scene_count must equal scenes length")
        if [scene.scene_order for scene in self.scenes] != list(range(1, len(self.scenes) + 1)):
            raise ValueError("scene order must be contiguous")
        entries = [entry for scene in self.scenes for entry in scene.entries]
        if self.beat_count != len(entries):
            raise ValueError("beat_count must equal manifest entries")
        beat_ids = [entry.beat_id for entry in entries]
        if len(beat_ids) != len(set(beat_ids)):
            raise ValueError("manifest must contain exactly one entry per beat")
        if [entry.beat_order for entry in entries] != list(range(1, len(entries) + 1)):
            raise ValueError("manifest beat order must be exact and contiguous")
        if self.approved_asset_count != sum(entry.resolution_status == "approved" for entry in entries):
            raise ValueError("approved_asset_count does not match entries")
        if self.failed_beat_count != sum(entry.resolution_status == "failed" for entry in entries):
            raise ValueError("failed_beat_count does not match entries")
        if self.revision_operation == "approval" and self.status != "approved":
            raise ValueError("approval revision must be approved")
        if self.status == "approved":
            if self.approved_asset_count != self.beat_count:
                raise ValueError("approved manifest requires one approved generated image per beat")
            if self.backend_qc.approval_eligibility_status != "passed":
                raise ValueError("approved manifest must pass approval eligibility")
        return self


class SceneAssetOperationIntegrity(StrictModel):
    schema_version: Literal["3.0"]
    operation_id: OperationId
    project_id: ProjectId
    operation_type: OperationType
    status: OperationStatus
    script_artifact_id: ArtifactId
    script_analysis_artifact_id: ArtifactId
    style_bible_artifact_id: ArtifactId
    scene_plan_artifact_id: ArtifactId
    visual_plan_artifact_id: ArtifactId
    prompt_bundle_artifact_id: ArtifactId
    visual_reference_approval_set_artifact_id: ArtifactId | None
    expected_scene_asset_manifest_artifact_id: ArtifactId | None
    media_batch_id: MediaBatchId
    frozen_reference_bindings_hash: Sha256Identity
    provider_settings_snapshot_hash: Sha256Identity


class SceneAssetReadiness(StrictModel):
    ready: bool
    blockers: list[NonEmptyText]


class SceneAssetActionEligibility(StrictModel):
    can_generate_all: bool
    can_generate_section: bool
    can_generate_missing: bool
    can_retry_failed: bool
    can_approve_manifest: bool
    eligible_generate_count: NonNegativeInt
    eligible_missing_count: NonNegativeInt
    blocked_generate_count: NonNegativeInt


class SceneAssetCandidateModel(StrictModel):
    artifact_id: ArtifactId
    media_task_id: MediaTaskId | None
    mime_type: NonEmptyText
    sha256: Sha256
    width: NonNegativeInt | None
    height: NonNegativeInt | None
    is_current_draft: bool
    is_approved: bool
    is_rejected: bool
    generated_from_current_prompt: bool


class SceneAssetContinuityReadiness(StrictModel):
    continuity_required: bool
    continuity_source_beat_id: BeatId | None
    continuity_source_ready: bool
    continuity_source_kind: Literal["approved"] | None
    continuity_source_stale: bool
    continuity_block_reason: Literal["source_not_generated", "source_not_approved", "source_artifact_missing", "source_integrity_failed", "source_lineage_stale"] | None
    dependency_error: SceneAssetError | None


class SceneAssetWorkspaceBeatEntry(StrictModel):
    scene_id: SceneId
    beat_id: BeatId
    beat_order: PositiveInt
    local_beat_order: PositiveInt
    continuity_group_id: Annotated[str, Field(pattern=r"^continuity_[0-9]{3,}$")]
    computed_duration_seconds: PositiveFloat

    resolution_status: WorkspaceResolutionStatus
    review_status: ReviewStatus

    selected_asset_artifact_id: ArtifactId | None
    current_draft_asset_artifact_id: ArtifactId | None
    candidate_asset_artifact_ids: list[ArtifactId]

    source_prompt_item_id: PromptItemId
    source_prompt_hash: PromptHash
    media_task_ids: list[MediaTaskId]

    width: NonNegativeInt | None
    height: NonNegativeInt | None
    mime_type: NonEmptyText | None
    sha256: Sha256 | None

    generation_lineage: SceneAssetGenerationLineage | None
    continuity_source: SceneAssetContinuitySource | None
    treatment: SceneAssetTreatment
    reference_bindings: list[PromptReferenceBinding]
    error: SceneAssetError | None


class SceneAssetBeatPrompt(StrictModel):
    image_prompt: NonEmptyText
    negative_prompt: NonEmptyText
    prompt_hash: PromptHash
    prompt_origin: Literal["compiled", "manual_edit"]


class SceneAssetPromptEditRequest(StrictModel):
    expected_prompt_bundle_artifact_id: ArtifactId
    image_prompt: NonEmptyText
    negative_prompt: NonEmptyText


class SceneAssetPromptRestoreRequest(StrictModel):
    expected_prompt_bundle_artifact_id: ArtifactId


class SceneAssetBeatWorkspace(StrictModel):
    entry: SceneAssetWorkspaceBeatEntry
    script_quote: NonEmptyText | None
    candidates: list[SceneAssetCandidateModel]
    continuity: SceneAssetContinuityReadiness
    prompt: SceneAssetBeatPrompt
    approval_change_impact_count: NonNegativeInt
    action_eligibility: dict[str, bool]


class SceneAssetSceneWorkspace(StrictModel):
    scene_id: SceneId
    scene_order: PositiveInt
    section_id: SectionId | None
    section_order: PositiveInt | None
    section_title: NonEmptyText | None
    beats: list[SceneAssetBeatWorkspace]


class SceneAssetOperationSummary(StrictModel):
    operation_id: OperationId
    job_id: JobId
    operation_type: OperationType
    status: OperationStatus
    progress: NonNegativeInt
    media_progress: MediaGenerationProgressV4 | None
    error_code: str | None
    error_message: str | None


class SceneAssetWorkspaceResponse(StrictModel):
    project_id: ProjectId
    readiness: SceneAssetReadiness
    current_prompt_bundle_artifact_id: ArtifactId | None
    current_manifest_artifact_id: ArtifactId | None
    manifest_status: ManifestStatus | None
    operation: SceneAssetOperationSummary | None
    scenes: list[SceneAssetSceneWorkspace]
    action_eligibility: SceneAssetActionEligibility


class SceneAssetBeatWorkspaceResponse(StrictModel):
    project_id: ProjectId
    base_manifest_artifact_id: ArtifactId | None
    published_manifest_artifact_id: ArtifactId | None
    current_manifest_artifact_id: ArtifactId | None
    manifest_status: ManifestStatus | None
    operation: SceneAssetOperationSummary | None
    beat: SceneAssetBeatWorkspace
    action_eligibility: SceneAssetActionEligibility


class SceneAssetBeatMutationResponse(StrictModel):
    project_id: ProjectId
    artifact_id: ArtifactId
    base_manifest_artifact_id: ArtifactId | None
    published_manifest_artifact_id: ArtifactId
    current_manifest_artifact_id: ArtifactId
    manifest_status: ManifestStatus
    operation: SceneAssetOperationSummary | None
    beats: list[SceneAssetBeatWorkspace]
    action_eligibility: SceneAssetActionEligibility


class SceneAssetGenerateRequest(StrictModel):
    operation_type: Literal["generate_all", "generate_section", "generate_missing", "retry_failed", "regenerate_section"] = "generate_all"
    expected_scene_asset_manifest_artifact_id: ArtifactId | None
    section_id: SectionId | None = None
    candidate_count: Annotated[int, Field(ge=1, le=5)] = 1

    @model_validator(mode="after")
    def validate_scope(self) -> "SceneAssetGenerateRequest":
        if self.operation_type in {"generate_section", "regenerate_section"} and self.section_id is None:
            raise ValueError(f"{self.operation_type} requires section_id")
        if self.operation_type not in {"generate_section", "regenerate_section", "retry_failed"} and self.section_id is not None:
            raise ValueError("section_id is only valid for generate_section, regenerate_section, or retry_failed")
        return self


class SceneAssetBeatGenerateRequest(StrictModel):
    expected_scene_asset_manifest_artifact_id: ArtifactId | None
    candidate_count: Annotated[int, Field(ge=1, le=5)] = 1


class SceneAssetBeatRegenerateRequest(StrictModel):
    expected_scene_asset_manifest_artifact_id: ArtifactId
    candidate_count: Annotated[int, Field(ge=1, le=5)] = 1


class SceneAssetCandidateDecisionRequest(StrictModel):
    expected_scene_asset_manifest_artifact_id: ArtifactId


class SceneAssetManifestApproveRequest(StrictModel):
    expected_scene_asset_manifest_artifact_id: ArtifactId


class SceneAssetJobSubmissionResponse(StrictModel):
    project_id: ProjectId
    job_id: JobId
    operation_id: OperationId
    status: Literal["queued"]
    manifest_artifact_id: ArtifactId


class SceneAssetJobStatusResponse(StrictModel):
    project_id: ProjectId
    job_id: JobId
    operation_id: OperationId
    operation_type: OperationType
    status: OperationStatus
    progress: Annotated[int, Field(ge=0, le=100)]
    manifest_artifact_id: ArtifactId | None
    media_batch_id: MediaBatchId
    media_progress: MediaGenerationProgressV4
    result: dict[str, Any] = Field(json_schema_extra={"additionalProperties": True})
    error_code: str | None
    error_message: str | None


class SceneAssetExecutionBeatStatus(StrictModel):
    state: WorkspaceResolutionStatus
    operation_id: OperationId
    job_id: JobId
    media_task_ids: list[MediaTaskId] = Field(default_factory=list)


class SceneAssetExecutionStatusResponse(StrictModel):
    project_id: ProjectId
    beats: dict[BeatId, SceneAssetExecutionBeatStatus] = Field(default_factory=dict)
    active_count: NonNegativeInt
    terminal_count: NonNegativeInt
    effective_poll_interval_seconds: PositiveInt
    updated_at: str


class SceneAssetRevisionResponse(StrictModel):
    artifact_id: ArtifactId
    artifact: SceneAssetManifestModel

    @model_validator(mode="after")
    def identity(self) -> "SceneAssetRevisionResponse":
        if self.artifact_id != self.artifact.scene_asset_manifest_revision_id:
            raise ValueError("artifact identity mismatch")
        return self
