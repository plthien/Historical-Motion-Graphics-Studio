from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.voice_credential_schemas_v4 import (
    ElevenLabsVoicePacingCalibrationResponse,
    UpdateElevenLabsVoicePacingCalibrationRequest,
)
from app.db.dependencies import get_db_session
from app.modules.v4.voice_pacing_calibration_settings import VoicePacingCalibrationSettingsService
from app.repositories import SettingsRepository


router = APIRouter(
    prefix="/api/settings/tts/providers/elevenlabs",
    tags=["Settings"],
)


def build_voice_pacing_calibration_service(
    session: Session,
) -> VoicePacingCalibrationSettingsService:
    return VoicePacingCalibrationSettingsService(
        session,
        SettingsRepository(session),
    )


@router.get(
    "/voices/{voice_id}/pacing-calibration",
    response_model=ElevenLabsVoicePacingCalibrationResponse,
)
def get_voice_pacing_calibration(
    voice_id: str,
    session: Session = Depends(get_db_session),
) -> ElevenLabsVoicePacingCalibrationResponse:
    payload = build_voice_pacing_calibration_service(session).get_voice_snapshot(
        provider_key="elevenlabs",
        voice_id=voice_id,
    )
    return ElevenLabsVoicePacingCalibrationResponse.model_validate(payload)


@router.put(
    "/voices/{voice_id}/pacing-calibration",
    response_model=ElevenLabsVoicePacingCalibrationResponse,
)
def update_voice_pacing_calibration(
    voice_id: str,
    request: UpdateElevenLabsVoicePacingCalibrationRequest,
    session: Session = Depends(get_db_session),
) -> ElevenLabsVoicePacingCalibrationResponse:
    payload = build_voice_pacing_calibration_service(session).put_voice_preset_override(
        provider_key="elevenlabs",
        voice_id=voice_id,
        voice_preset=request.voice_preset,
        native_wpm_at_rate_1=request.native_wpm_at_rate_1,
    )
    return ElevenLabsVoicePacingCalibrationResponse.model_validate(payload)
