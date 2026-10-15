from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.voice_pacing_acceptance_schemas_v4 import (
    VoiceSectionPacingAcceptanceRequest,
    VoiceSectionPacingAcceptanceResponse,
)
from app.db.dependencies import get_db_session
from app.modules.v4.voice_pacing_manual_acceptance import VoicePacingManualAcceptanceService


router = APIRouter(prefix="/api/v4", tags=["Production V4"])


@router.post(
    "/projects/{project_id}/voice/sections/{section_id}/accept-pacing-deviation",
    response_model=VoiceSectionPacingAcceptanceResponse,
)
def accept_voice_section_pacing_deviation(
    project_id: str,
    section_id: str,
    request: VoiceSectionPacingAcceptanceRequest,
    session: Session = Depends(get_db_session),
) -> VoiceSectionPacingAcceptanceResponse:
    payload = VoicePacingManualAcceptanceService(session).accept_section(
        project_id=project_id,
        section_id=section_id,
        source_remediation_operation_id=request.source_remediation_operation_id,
        expected_remediation_report_sha256=request.expected_remediation_report_sha256,
        expected_remediation_identity=request.expected_remediation_identity,
    )
    return VoiceSectionPacingAcceptanceResponse.model_validate(payload)
