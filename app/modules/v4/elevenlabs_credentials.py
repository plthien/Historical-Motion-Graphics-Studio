from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.orm import Session

from app.api.errors import AppError
from app.core.config import Settings, get_settings
from app.core.time import utc_now_iso
from app.db.models import Setting
from app.db.session import get_session_factory
from app.repositories import SettingsRepository

VOICE_PROVIDER_API_KEYS_SETTINGS_KEY = "voice_provider_api_keys"
ELEVENLABS_PROVIDER_KEY = "elevenlabs"
MAX_VOICE_PROVIDER_API_KEY_LENGTH = 512


@dataclass(frozen=True, slots=True)
class EffectiveVoiceProviderCredential:
    api_key: str | None
    source: Literal["stored", "environment", "none"]


class VoiceProviderCredentialStore:
    """Server-only provider credential storage backed by the generic Settings table."""

    def __init__(self, session: Session, repository: SettingsRepository | None = None) -> None:
        self.session = session
        self.repository = repository or SettingsRepository(session)

    def stored_api_key(self, provider_key: str) -> str | None:
        payload = self._load_payload()
        raw = payload.get(provider_key)
        if raw is None:
            return None
        if not isinstance(raw, str):
            raise AppError("VOICE_PROVIDER_CREDENTIAL_STORE_INVALID", "Voice provider credential storage is malformed.", 500, retryable=False)
        value = raw.strip()
        return value or None

    def save_api_key(self, provider_key: str, api_key: str) -> None:
        value = validate_voice_provider_api_key(api_key)
        payload = self._load_payload()
        payload[provider_key] = value
        self.repository.upsert(Setting(key=VOICE_PROVIDER_API_KEYS_SETTINGS_KEY, value_json=payload, updated_at=utc_now_iso()))
        self.session.commit()

    def clear_api_key(self, provider_key: str) -> None:
        payload = self._load_payload()
        payload.pop(provider_key, None)
        self.repository.upsert(Setting(key=VOICE_PROVIDER_API_KEYS_SETTINGS_KEY, value_json=payload, updated_at=utc_now_iso()))
        self.session.commit()

    def _load_payload(self) -> dict[str, str]:
        row = self.repository.get(VOICE_PROVIDER_API_KEYS_SETTINGS_KEY)
        if row is None:
            return {}
        payload = row.value_json
        if not isinstance(payload, dict):
            raise AppError("VOICE_PROVIDER_CREDENTIAL_STORE_INVALID", "Voice provider credential storage is malformed.", 500, retryable=False)
        return dict(payload)


class ElevenLabsCredentialResolver:
    """Resolve the current effective ElevenLabs credential without caching it."""

    def __init__(self, settings: Settings | None = None, *, stored_key_loader: Callable[[], str | None] | None = None) -> None:
        self.settings = settings or get_settings()
        self._stored_key_loader = stored_key_loader

    def resolve(self) -> EffectiveVoiceProviderCredential:
        stored = self._stored_key_loader() if self._stored_key_loader is not None else None
        if stored:
            return EffectiveVoiceProviderCredential(api_key=stored, source="stored")
        environment = self.settings.elevenlabs_api_key.strip()
        if environment:
            return EffectiveVoiceProviderCredential(api_key=environment, source="environment")
        return EffectiveVoiceProviderCredential(api_key=None, source="none")

    def require_api_key(self) -> str:
        credential = self.resolve()
        if credential.api_key:
            return credential.api_key
        raise AppError("ELEVENLABS_API_KEY_NOT_CONFIGURED", "ElevenLabs API key is not configured.", 409, retryable=False)


def build_runtime_elevenlabs_credential_resolver(settings: Settings | None = None) -> ElevenLabsCredentialResolver:
    runtime = settings or get_settings()
    session_factory = get_session_factory()

    def load_stored_key() -> str | None:
        with session_factory() as session:
            return VoiceProviderCredentialStore(session).stored_api_key(ELEVENLABS_PROVIDER_KEY)

    return ElevenLabsCredentialResolver(runtime, stored_key_loader=load_stored_key)


def credential_status(*, settings: Settings | None = None, store: VoiceProviderCredentialStore) -> EffectiveVoiceProviderCredential:
    runtime = settings or get_settings()
    resolver = ElevenLabsCredentialResolver(runtime, stored_key_loader=lambda: store.stored_api_key(ELEVENLABS_PROVIDER_KEY))
    return resolver.resolve()


def validate_voice_provider_api_key(api_key: str) -> str:
    value = api_key.strip()
    if not value:
        raise AppError("ELEVENLABS_API_KEY_INVALID", "ElevenLabs API key must not be empty.", 422, retryable=False)
    if len(value) > MAX_VOICE_PROVIDER_API_KEY_LENGTH:
        raise AppError("ELEVENLABS_API_KEY_INVALID", "ElevenLabs API key exceeds the supported length.", 422, retryable=False)
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise AppError("ELEVENLABS_API_KEY_INVALID", "ElevenLabs API key contains unsupported control characters.", 422, retryable=False)
    return value
