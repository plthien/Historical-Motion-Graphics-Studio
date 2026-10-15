from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class VoiceSectionPacingAcceptanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_remediation_operation_id: str = Field(min_length=1)
    expected_remediation_report_sha256: str = Field(min_length=64, max_length=64)
    expected_remediation_identity: str = Field(min_length=64, max_length=64)


class VoiceSectionPacingAcceptanceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(min_length=1)
    operation_id: str = Field(min_length=1)
    job_id: str = Field(min_length=1)
    section_id: str = Field(min_length=1)
    status: Literal["accepted", "resuming"]
    accepted_section_ids: list[str]
    remaining_regeneration_required_section_ids: list[str]
