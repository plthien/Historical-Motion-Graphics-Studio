from __future__ import annotations

from typing import Any, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.api.media_progress_schemas_v4 import MediaGenerationProgressV4
from app.api.prompt_composer_schemas_v4 import AspectRatio
from app.core.ids import ARTIFACT_ID_PATTERN

ArtifactId = Annotated[str, Field(pattern=ARTIFACT_ID_PATTERN)]
ProjectId = Annotated[str, Field(pattern=r"^project_[0-9a-f]{12}$")]
JobId = Annotated[str, Field(pattern=r"^job_[0-9a-f]{12}$")]
OperationId = Annotated[str, Field(pattern=r"^operation_[0-9a-f]{12}$")]
AnchorType = Literal["character", "location", "prop", "recurring_visual_subject"]
ANCHOR_TYPE_VALUES = ["character", "location", "prop", "recurring_visual_subject"]


def _object_branch(schema: dict[str, Any]) -> dict[str, Any]:
    if schema.get("type") == "object":
        return schema
    for branch in schema.get("anyOf", []):
        if isinstance(branch, dict) and branch.get("type") == "object":
            return branch
    return schema


def _visual_reference_generate_schema_extra(schema: dict[str, Any]) -> None:
    field_schema = schema.get("properties", {}).get("aspect_ratios_by_anchor_type")
    if isinstance(field_schema, dict):
        _object_branch(field_schema)["propertyNames"] = {"enum": ANCHOR_TYPE_VALUES}


def _visual_reference_job_status_schema_extra(schema: dict[str, Any]) -> None:
    job_type = schema.get("properties", {}).get("job_type")
    if isinstance(job_type, dict):
        job_type.pop("enum", None)


def _visual_reference_workspace_schema_extra(schema: dict[str, Any]) -> None:
    properties = schema.get("properties", {})
    for field_name in ("visual_reference_set", "approval_set", "readiness"):
        field_schema = properties.get(field_name)
        if isinstance(field_schema, dict):
            _object_branch(field_schema)["additionalProperties"] = True


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VisualReferenceGenerateRequestV4(StrictModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra=_visual_reference_generate_schema_extra)

    style_bible_artifact_id: ArtifactId
    expected_visual_reference_set_artifact_id: ArtifactId | None = None
    candidate_count: Annotated[int, Field(ge=1, le=5)] = 1
    aspect_ratio: AspectRatio | None = None
    aspect_ratios_by_anchor_type: dict[AnchorType, AspectRatio] | None = None


class VisualReferenceJobSubmissionResponseV4(StrictModel):
    job_id: JobId
    job_type: Literal["visual_reference_v4_generate"]
    project_id: ProjectId
    operation_id: OperationId
    status: Literal["queued"]
    progress: Literal[0]


class VisualReferenceJobStatusResponseV4(StrictModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra=_visual_reference_job_status_schema_extra)

    job_id: JobId
    job_type: Literal["visual_reference_v4_generate"]
    project_id: ProjectId
    operation_id: OperationId
    status: Literal["queued", "running", "completed", "failed", "cancelled", "stale_failed"]
    progress: Annotated[int, Field(ge=0, le=100)]
    retryable: bool
    error_code: str | None
    error_message: str | None
    visual_reference_set_artifact_id: ArtifactId | None
    media_batch_id: Annotated[str, Field(pattern=r"^media_batch_[0-9a-f]{12}$")]
    media_progress: MediaGenerationProgressV4
    created_at: str
    updated_at: str
    started_at: str | None
    finished_at: str | None


class VisualReferenceOperationSummaryV4(StrictModel):
    operation_id: OperationId
    job_id: JobId
    status: Literal["queued", "generating", "completed", "failed"]
    progress: Annotated[int, Field(ge=0, le=100)]
    media_progress: MediaGenerationProgressV4
    error_code: str | None
    error_message: str | None


class VisualReferenceWorkspaceResponseV4(StrictModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra=_visual_reference_workspace_schema_extra)

    project_id: ProjectId
    generated: bool
    visual_reference_set_artifact_id: ArtifactId | None
    visual_reference_set: dict[str, Any] | None
    approval_set_artifact_id: ArtifactId | None
    approval_set: dict[str, Any] | None
    operation: VisualReferenceOperationSummaryV4 | None
    readiness: dict[str, Any]

    @model_validator(mode="after")
    def validate_state(self) -> "VisualReferenceWorkspaceResponseV4":
        if self.generated != (self.visual_reference_set_artifact_id is not None and self.visual_reference_set is not None):
            raise ValueError("generated must match visual-reference-set presence")
        return self


class VisualReferenceApproveRequestV4(StrictModel):
    visual_reference_set_artifact_id: ArtifactId
    expected_approval_set_artifact_id: ArtifactId | None = None
    reference_artifact_id: ArtifactId


class VisualReferenceClearRequestV4(StrictModel):
    visual_reference_set_artifact_id: ArtifactId
    expected_approval_set_artifact_id: ArtifactId | None = None


class VisualReferencePromptUpdateRequestV4(StrictModel):
    expected_visual_reference_set_artifact_id: ArtifactId
    prompt: Annotated[str, Field(min_length=1, max_length=50000)]
    negative_prompt: Annotated[str, Field(max_length=50000)] = ""


class VisualReferenceApprovalRevisionResponseV4(StrictModel):
    artifact_id: ArtifactId
    artifact: dict[str, Any]

    @model_validator(mode="after")
    def validate_identity(self) -> "VisualReferenceApprovalRevisionResponseV4":
        if self.artifact.get("approval_set_artifact_id") != self.artifact_id:
            raise ValueError("artifact identity mismatch")
        return self
