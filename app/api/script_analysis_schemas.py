from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.ids import ARTIFACT_ID_PATTERN


class GenerateScriptAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    script_revision_id: str = Field(pattern=ARTIFACT_ID_PATTERN)


class ScriptAnalysisArtifactResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_id: str = Field(pattern=ARTIFACT_ID_PATTERN)
    artifact_type: Literal["script_analysis"]
    project_id: str = Field(pattern=r"^project_[0-9a-f]{12}$")
    source_script_revision_id: str = Field(pattern=ARTIFACT_ID_PATTERN)
    quote_resolution_status: Literal["passed", "warning", "failed"]
    content: dict[str, Any]


class ScriptAnalysisReadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str = Field(pattern=r"^project_[0-9a-f]{12}$")
    generated: bool
    artifact_id: str | None = Field(default=None, pattern=ARTIFACT_ID_PATTERN)
    artifact: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_generated_state(self) -> "ScriptAnalysisReadResponse":
        has_artifact = self.artifact_id is not None and self.artifact is not None
        if self.generated != has_artifact:
            raise ValueError(
                "generated must be true exactly when artifact_id and artifact are both present"
            )
        return self

class ScriptAnalysisContinuityReviewMutationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_script_analysis_artifact_id: str = Field(pattern=ARTIFACT_ID_PATTERN)
    expected_review_artifact_id: str | None = Field(default=None, pattern=ARTIFACT_ID_PATTERN)
    continuity_ref: str = Field(pattern=r"^(char|loc|prop|subject)_[0-9]{3}$")
    removed: bool


class ScriptAnalysisContinuityReviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str = Field(pattern=r"^project_[0-9a-f]{12}$")
    script_analysis_artifact_id: str = Field(pattern=ARTIFACT_ID_PATTERN)
    review_artifact_id: str | None = Field(default=None, pattern=ARTIFACT_ID_PATTERN)
    removed_continuity_refs: list[str]
    available_continuity_refs: list[str]
