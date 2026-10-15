from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


AnchorTypeV2 = Literal["character", "location", "prop", "recurring_visual_subject"]
AspectRatioV2 = Literal["1:1", "3:4", "4:3", "9:16", "16:9"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VisualReferenceGenerateRequestV2(StrictModel):
    style_bible_artifact_id: str
    expected_visual_reference_set_artifact_id: str | None = None
    candidate_count: int = Field(default=1, ge=1, le=5)
    aspect_ratio: AspectRatioV2 | None = None
    aspect_ratios_by_anchor_type: dict[AnchorTypeV2, AspectRatioV2] | None = None


class VisualReferencePromptEditRequestV2(StrictModel):
    expected_version: int = Field(ge=1)
    prompt: str = Field(min_length=1, max_length=50000)
    negative_prompt: str = Field(default="", max_length=50000)


class VisualReferenceApproveRequestV2(StrictModel):
    expected_version: int = Field(ge=1)
    candidate_id: str


class VisualReferenceClearRequestV2(StrictModel):
    expected_version: int = Field(ge=1)


class VisualReferencePublicationRequestV2(StrictModel):
    expected_visual_reference_set_artifact_id: str | None = None
    expected_approval_set_artifact_id: str | None = None


class VisualReferencePublicationResultV2(StrictModel):
    project_id: str
    visual_reference_set_artifact_id: str
    approval_set_artifact_id: str
    published_anchor_count: int = Field(ge=1)
    ready: Literal[True]


class VisualReferenceOperationSubmissionV2(StrictModel):
    project_id: str
    operation_id: str
    operation_type: str
    status: Literal["queued"]
    total_anchor_count: int = Field(ge=1)
    scheduled_anchors: list[dict[str, str]]
    blocked_anchors: list[dict[str, str]]
    next_reconcile_at: str


class VisualReferenceMutationResultV2(StrictModel):
    project_id: str
    anchor_type: AnchorTypeV2
    anchor_id: str
    version: int = Field(ge=1)
    prompt_revision_id: str | None = None
    prompt: str | None = None
    negative_prompt: str | None = None
    candidate_id: str | None = None
    media_artifact_id: str | None = None
    media_sha256: str | None = None
    mime_type: str | None = None
    resolution_status: str | None = None
    review_status: str | None = None
    selected_candidate_id: str | None = None
    approved_candidate_id: str | None = None
    published_visual_reference_set_artifact_id: str | None = None
    published_approval_set_artifact_id: str | None = None


class VisualReferenceCandidateV2(StrictModel):
    candidate_id: str
    source_prompt_revision_id: str
    candidate_index: int = Field(ge=1, le=5)
    media_artifact_id: str
    media_task_id: str | None
    mime_type: str
    media_sha256: str
    file_url: str
    source_kind: str
    compatible_with_current_definition: bool
    created_at: str


class VisualReferenceAnchorV2(StrictModel):
    anchor_type: AnchorTypeV2
    anchor_id: str
    label: str
    source_definition_hash: str
    version: int = Field(ge=0)
    initialized: bool
    upstream_stale: bool
    resolution_status: Literal["not_generated", "candidate_ready", "approved", "failed"]
    review_status: Literal["unreviewed", "draft_selected", "approved", "rejected"]
    prompt_revision_id: str | None
    prompt: str
    negative_prompt: str
    candidates: list[VisualReferenceCandidateV2]
    selected_candidate_id: str | None
    approved_candidate_id: str | None
    active_operation_anchor_id: str | None
    last_error: dict[str, Any] | None
    last_progress_at: str | None


class VisualReferenceReadinessV2(StrictModel):
    required_count: int = Field(ge=0)
    approved_count: int = Field(ge=0)
    ready: bool
    missing_anchor_keys: list[str]
    stale_anchor_keys: list[str]


class VisualReferenceWorkspaceV2(StrictModel):
    project_id: str
    style_bible_artifact_id: str | None
    published_visual_reference_set_artifact_id: str | None
    published_approval_set_artifact_id: str | None
    generated: bool
    anchors: list[VisualReferenceAnchorV2]
    readiness: VisualReferenceReadinessV2
    active_operation_count: int = Field(ge=0)


class VisualReferenceExecutionTaskV2(StrictModel):
    operation_task_id: str
    candidate_index: int = Field(ge=1, le=5)
    status: str
    media_task_id: str | None
    next_reconcile_at: str | None
    error_code: str | None
    error_message: str | None


class VisualReferenceExecutionAnchorV2(StrictModel):
    operation_anchor_id: str
    anchor_type: AnchorTypeV2
    anchor_id: str
    status: str
    candidate_count: int = Field(ge=1, le=5)
    tasks: list[VisualReferenceExecutionTaskV2]


class VisualReferenceExecutionV2(StrictModel):
    operation_id: str
    operation_type: str
    status: str
    total_anchor_count: int = Field(ge=1)
    completed_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    cancelled_count: int = Field(ge=0)
    next_reconcile_at: str | None
    created_at: str
    updated_at: str
    error_code: str | None
    error_message: str | None
    anchors: list[VisualReferenceExecutionAnchorV2]


class VisualReferenceExecutionsV2(StrictModel):
    project_id: str
    executions: list[VisualReferenceExecutionV2]
