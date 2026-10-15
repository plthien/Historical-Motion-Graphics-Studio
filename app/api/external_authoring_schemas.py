from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExternalPromptPackageResponse(_StrictModel):
    prompt_text: str = Field(min_length=1)
    artifact_ids: dict[str, str]
    output_schema_name: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)


class ExternalPromptDraftReadResponse(_StrictModel):
    generated: bool
    rebuild_required: bool = False
    prompt_package: ExternalPromptPackageResponse | None
    request_input: dict[str, Any] | None


class ExternalImportRequest(_StrictModel):
    result_json: str = Field(min_length=1)


class ExternalValidationIssue(_StrictModel):
    code: str = Field(min_length=1)
    path: str = Field(min_length=1)
    message: str = Field(min_length=1)


class ExternalImportValidationResponse(_StrictModel):
    valid: bool
    errors: list[ExternalValidationIssue]
    warnings: list[ExternalValidationIssue]
    summary: dict[str, Any]


class ScriptAnalysisCombinedRevisionResponse(_StrictModel):
    script_artifact_id: str
    script: dict[str, Any]
    script_analysis_artifact_id: str
    script_analysis: dict[str, Any]
