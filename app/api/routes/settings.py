from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.schemas import (
    MediaProviderSettingsResponse,
    SettingsResponse,
    TestMediaProviderConnectionResponse,
    UpdateMediaProviderSettingsRequest,
    UpdateSettingsRequest,
)
from app.core.config import get_settings as get_runtime_settings
from app.db.dependencies import get_db_session
from app.repositories import SettingsRepository
from app.services.media_settings import MediaProviderSettingsService
from app.services.settings import SettingsService

router = APIRouter(prefix="/api/settings", tags=["Settings"])


def build_settings_service(session: Session) -> SettingsService:
    return SettingsService(
        session=session,
        settings_repository=SettingsRepository(session),
    )


@router.get("", response_model=SettingsResponse)
def get_settings(
    session: Session = Depends(get_db_session),
) -> SettingsResponse:
    settings = build_settings_service(session).get_settings()
    return SettingsResponse(settings=settings)


@router.put("", response_model=SettingsResponse)
def update_settings(
    request: UpdateSettingsRequest,
    session: Session = Depends(get_db_session),
) -> SettingsResponse:
    settings = build_settings_service(session).update_settings(request.settings)
    return SettingsResponse(settings=settings)


def build_media_settings_service(
    session: Session,
) -> MediaProviderSettingsService:
    return MediaProviderSettingsService(
        session=session,
        repository=SettingsRepository(session),
    )


@router.get(
    "/media",
    response_model=MediaProviderSettingsResponse,
    response_model_exclude_none=True,
)
def get_media_settings(
    session: Session = Depends(get_db_session),
) -> MediaProviderSettingsResponse:
    service = build_media_settings_service(session)
    settings = service.get_settings()
    return MediaProviderSettingsResponse(
        settings=settings,
        readiness=service.readiness(settings),
    )


@router.put(
    "/media",
    response_model=MediaProviderSettingsResponse,
    response_model_exclude_none=True,
)
def update_media_settings(
    request: UpdateMediaProviderSettingsRequest,
    session: Session = Depends(get_db_session),
) -> MediaProviderSettingsResponse:
    service = build_media_settings_service(session)
    settings = service.put_settings(
        request.settings.model_dump(mode="json", exclude_none=True)
    )
    return MediaProviderSettingsResponse(
        settings=settings,
        readiness=service.readiness(settings),
    )


@router.post(
    "/media/test-connection",
    response_model=TestMediaProviderConnectionResponse,
)
def test_media_connection(
    session: Session = Depends(get_db_session),
) -> TestMediaProviderConnectionResponse:
    result = build_media_settings_service(session).test_connection()
    return TestMediaProviderConnectionResponse(**result)


from app.api.voice_credential_schemas_v4 import (
    ElevenLabsConnectionTestResponse,
    ElevenLabsCredentialStatusResponse,
    ElevenLabsProfessionalVoiceCatalogResponse,
    ElevenLabsProfessionalVoiceModel,
    UpdateElevenLabsCredentialRequest,
)
from app.api.voice_schemas_v4 import (
    TTSReadinessModel,
    TTSSettingsResponse,
    UpdateTTSSettingsRequest,
)
from app.modules.v4.elevenlabs_credentials import (
    ELEVENLABS_PROVIDER_KEY,
    VoiceProviderCredentialStore,
    credential_status,
)
from app.modules.v4.elevenlabs_provider_adapter import ElevenLabsProviderAdapter
from app.modules.v4.voice_first_elevenlabs_adapter import VoiceFirstElevenLabsProviderAdapter
from app.modules.v4.voice_provider_registry import build_voice_provider_registry
from app.services.tts_settings import TTSSettingsService


def build_tts_settings_service(session: Session) -> TTSSettingsService:
    return TTSSettingsService(
        session,
        SettingsRepository(session),
        runtime_settings=get_runtime_settings(),
    )


def build_voice_provider_credential_store(
    session: Session,
) -> VoiceProviderCredentialStore:
    return VoiceProviderCredentialStore(
        session,
        SettingsRepository(session),
    )


def _elevenlabs_credential_response(
    session: Session,
) -> ElevenLabsCredentialStatusResponse:
    effective = credential_status(
        settings=get_runtime_settings(),
        store=build_voice_provider_credential_store(session),
    )
    return ElevenLabsCredentialStatusResponse(
        api_key_configured=effective.api_key is not None,
        source=effective.source,
    )


@router.get(
    "/tts/providers/elevenlabs/credential",
    response_model=ElevenLabsCredentialStatusResponse,
)
def get_elevenlabs_credential(
    session: Session = Depends(get_db_session),
) -> ElevenLabsCredentialStatusResponse:
    return _elevenlabs_credential_response(session)


@router.put(
    "/tts/providers/elevenlabs/credential",
    response_model=ElevenLabsCredentialStatusResponse,
)
def update_elevenlabs_credential(
    request: UpdateElevenLabsCredentialRequest,
    session: Session = Depends(get_db_session),
) -> ElevenLabsCredentialStatusResponse:
    build_voice_provider_credential_store(session).save_api_key(
        ELEVENLABS_PROVIDER_KEY,
        request.api_key,
    )
    return _elevenlabs_credential_response(session)


@router.delete(
    "/tts/providers/elevenlabs/credential",
    response_model=ElevenLabsCredentialStatusResponse,
)
def clear_elevenlabs_credential(
    session: Session = Depends(get_db_session),
) -> ElevenLabsCredentialStatusResponse:
    build_voice_provider_credential_store(session).clear_api_key(
        ELEVENLABS_PROVIDER_KEY
    )
    return _elevenlabs_credential_response(session)


@router.get(
    "/tts/providers/elevenlabs/professional-voices",
    response_model=ElevenLabsProfessionalVoiceCatalogResponse,
)
def list_elevenlabs_professional_voices() -> ElevenLabsProfessionalVoiceCatalogResponse:
    provider = build_voice_provider_registry(
        get_runtime_settings()
    ).resolve(ELEVENLABS_PROVIDER_KEY)
    if not isinstance(provider, VoiceFirstElevenLabsProviderAdapter):
        raise RuntimeError("Voice-first ElevenLabs provider registry composition is invalid.")
    return ElevenLabsProfessionalVoiceCatalogResponse(
        voices=[
            ElevenLabsProfessionalVoiceModel.model_validate(item)
            for item in provider.professional_voice_catalog()
        ]
    )


@router.post(
    "/tts/providers/elevenlabs/test-connection",
    response_model=ElevenLabsConnectionTestResponse,
)
def test_elevenlabs_connection(
    session: Session = Depends(get_db_session),
) -> ElevenLabsConnectionTestResponse:
    _ = session
    provider = build_voice_provider_registry(
        get_runtime_settings()
    ).resolve(ELEVENLABS_PROVIDER_KEY)
    if not isinstance(provider, ElevenLabsProviderAdapter):
        raise RuntimeError("ElevenLabs provider registry composition is invalid.")
    return ElevenLabsConnectionTestResponse.model_validate(
        provider.test_connection()
    )


@router.get("/tts", response_model=TTSSettingsResponse)
def get_tts_settings(
    session: Session = Depends(get_db_session),
) -> TTSSettingsResponse:
    service = build_tts_settings_service(session)
    settings = service.get_settings()
    profile = settings.get("active_profile_id")
    default_voice = next(
        (
            item.get("default_voice_id")
            for item in settings.get("profiles", [])
            if item.get("profile_id") == profile
        ),
        None,
    )
    return TTSSettingsResponse(
        settings=settings,
        readiness=service.readiness(profile, default_voice),
    )


@router.put("/tts", response_model=TTSSettingsResponse)
def update_tts_settings(
    request: UpdateTTSSettingsRequest,
    session: Session = Depends(get_db_session),
) -> TTSSettingsResponse:
    service = build_tts_settings_service(session)
    settings = service.put_settings(
        request.settings.model_dump(mode="json")
    )
    profile = settings.get("active_profile_id")
    default_voice = next(
        (
            item.get("default_voice_id")
            for item in settings.get("profiles", [])
            if item.get("profile_id") == profile
        ),
        None,
    )
    return TTSSettingsResponse(
        settings=settings,
        readiness=service.readiness(profile, default_voice),
    )


@router.get("/tts/readiness", response_model=TTSReadinessModel)
def get_tts_readiness(
    profile_id: str | None = None,
    voice_id: str | None = None,
    session: Session = Depends(get_db_session),
) -> TTSReadinessModel:
    return TTSReadinessModel.model_validate(
        build_tts_settings_service(session).readiness(
            profile_id,
            voice_id,
        )
    )


# Phase 14 — Local FFmpeg Render settings
from app.api.render_schemas_v4 import (
    RenderReadinessModel,
    RenderSettingsResponse,
    UpdateRenderSettingsRequest,
)
from app.services.render_settings import RenderSettingsService


def build_render_settings_service(
    session: Session,
) -> RenderSettingsService:
    return RenderSettingsService(
        session,
        SettingsRepository(session),
        runtime_settings=get_runtime_settings(),
    )


@router.get("/render", response_model=RenderSettingsResponse)
def get_render_settings(
    session: Session = Depends(get_db_session),
) -> RenderSettingsResponse:
    service = build_render_settings_service(session)
    settings = service.get_settings()
    return RenderSettingsResponse(
        settings=settings,
        readiness=service.readiness(settings["active_profile_id"]),
    )


@router.put("/render", response_model=RenderSettingsResponse)
def update_render_settings(
    request: UpdateRenderSettingsRequest,
    session: Session = Depends(get_db_session),
) -> RenderSettingsResponse:
    service = build_render_settings_service(session)
    settings = service.put_settings(
        request.settings.model_dump(mode="json")
    )
    return RenderSettingsResponse(
        settings=settings,
        readiness=service.readiness(settings["active_profile_id"]),
    )


@router.get("/render/readiness", response_model=RenderReadinessModel)
def get_render_readiness(
    profile_id: str | None = None,
    session: Session = Depends(get_db_session),
) -> RenderReadinessModel:
    return RenderReadinessModel.model_validate(
        build_render_settings_service(session).readiness(profile_id)
    )
