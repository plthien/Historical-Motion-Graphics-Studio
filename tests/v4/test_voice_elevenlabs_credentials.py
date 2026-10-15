from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest

from app.api.routes.settings import clear_elevenlabs_credential, get_elevenlabs_credential, update_elevenlabs_credential
from app.api.voice_credential_schemas_v4 import UpdateElevenLabsCredentialRequest
from app.core.config import Settings
from app.core.time import utc_now_iso
from app.db.models import Setting
from app.db.session import get_session_factory
from app.modules.v4.elevenlabs_client import ElevenLabsHttpClient
from app.modules.v4.elevenlabs_credentials import (
    ELEVENLABS_PROVIDER_KEY,
    VOICE_PROVIDER_API_KEYS_SETTINGS_KEY,
    ElevenLabsCredentialResolver,
    VoiceProviderCredentialStore,
    validate_voice_provider_api_key,
)
from app.modules.v4.elevenlabs_provider_adapter import ElevenLabsProviderAdapter
from app.modules.v4.voice_provider_contracts import VoiceProviderProfile
from app.modules.v4.voice_provider_registry import build_voice_provider_registry
from app.repositories import SettingsRepository
from app.services.settings import SettingsService
from tests.v4.test_voice_elevenlabs_adapter_execution import _Control, _pcm, _request

SECRET_A = "ele_secret_MUST_NOT_LEAK_123"
SECRET_B = "ele_secret_SECOND_ACCOUNT_456"
ENV_SECRET = "ele_env_fallback_789"


@pytest.fixture(autouse=True)
def _reset_credential_store() -> None:
    with get_session_factory()() as session:
        SettingsRepository(session).upsert(Setting(key=VOICE_PROVIDER_API_KEYS_SETTINGS_KEY, value_json={}, updated_at=utc_now_iso()))
        session.commit()
    with ElevenLabsProviderAdapter._cache_lock:
        ElevenLabsProviderAdapter._voice_cache.clear()
        ElevenLabsProviderAdapter._model_cache.clear()
    yield
    with get_session_factory()() as session:
        SettingsRepository(session).upsert(Setting(key=VOICE_PROVIDER_API_KEYS_SETTINGS_KEY, value_json={}, updated_at=utc_now_iso()))
        session.commit()


def _profile(voice_id: str | None = None) -> VoiceProviderProfile:
    return VoiceProviderProfile(
        profile_id="elevenlabs_multilingual_v2",
        provider_key="elevenlabs",
        enabled=True,
        display_name="ElevenLabs Multilingual v2",
        voice_id=voice_id,
        language="en",
        speaking_rate=1.0,
        requested_sample_rate_hz=24_000,
        requested_output_format="wav_pcm_s16le",
        maximum_segment_characters=1_000,
        provider_options={"model_id": "eleven_multilingual_v2", "narration_language": "en"},
    )


def _client_factory(requests: list[tuple[str, str, str]]):
    def factory(key: str, base: str, max_response: int, timeout: float) -> ElevenLabsHttpClient:
        def handler(request: httpx.Request) -> httpx.Response:
            requests.append((key, request.method, request.url.path))
            if request.url.path == "/v1/models":
                return httpx.Response(200, json=[{"model_id": "eleven_multilingual_v2", "can_do_text_to_speech": True}])
            if request.url.path == "/v2/voices":
                suffix = "a" if key == SECRET_A else "b"
                return httpx.Response(
                    200,
                    json={
                        "voices": [
                            {
                                "voice_id": f"voice_{suffix}",
                                "name": f"Voice {suffix.upper()}",
                                "category": "professional",
                                "fine_tuning": {"state": {"eleven_multilingual_v2": "fine_tuned"}},
                            }
                        ],
                        "has_more": False,
                    },
                )
            if request.method == "POST":
                return httpx.Response(200, content=_pcm(), headers={"request-id": "req_credential"})
            raise AssertionError(f"Unexpected request: {request.method} {request.url.path}")

        raw = httpx.Client(transport=httpx.MockTransport(handler), base_url=base)
        return ElevenLabsHttpClient(key, base, max_response, timeout, client=raw)

    return factory


def test_effective_credential_precedence() -> None:
    current: dict[str, str | None] = {"stored": None}
    resolver = ElevenLabsCredentialResolver(
        Settings(root_dir=Path.cwd(), elevenlabs_api_key=ENV_SECRET),
        stored_key_loader=lambda: current["stored"],
    )
    assert resolver.resolve().api_key == ENV_SECRET
    assert resolver.resolve().source == "environment"
    current["stored"] = SECRET_A
    assert resolver.resolve().api_key == SECRET_A
    assert resolver.resolve().source == "stored"
    current["stored"] = None
    assert resolver.resolve().api_key == ENV_SECRET


def test_credential_routes_store_server_only_secret_and_never_echo() -> None:
    with get_session_factory()() as session:
        response = update_elevenlabs_credential(UpdateElevenLabsCredentialRequest(api_key=SECRET_A), session)
        row = SettingsRepository(session).get(VOICE_PROVIDER_API_KEYS_SETTINGS_KEY)
        assert row is not None and row.value_json == {ELEVENLABS_PROVIDER_KEY: SECRET_A}
        assert SECRET_A not in json.dumps(response.model_dump(mode="json"))
        assert response.api_key_configured is True and response.source == "stored"

        read = get_elevenlabs_credential(session)
        assert SECRET_A not in json.dumps(read.model_dump(mode="json"))
        assert read.api_key_configured is True

        generic = SettingsService(session, SettingsRepository(session))
        assert VOICE_PROVIDER_API_KEYS_SETTINGS_KEY not in generic.get_settings()
        generic.update_settings({"example_public_setting": {"enabled": True}, VOICE_PROVIDER_API_KEYS_SETTINGS_KEY: {"elevenlabs": "attacker"}})
        preserved = SettingsRepository(session).get(VOICE_PROVIDER_API_KEYS_SETTINGS_KEY)
        assert preserved is not None and preserved.value_json == {ELEVENLABS_PROVIDER_KEY: SECRET_A}

        cleared = clear_elevenlabs_credential(session)
        assert cleared.api_key_configured is False and cleared.source == "none"


def test_long_lived_registry_uses_saved_key_without_restart_and_cache_isolation(tmp_path: Path) -> None:
    requests: list[tuple[str, str, str]] = []
    runtime = Settings(root_dir=tmp_path, elevenlabs_api_key="", elevenlabs_api_base_url="https://api.elevenlabs.io")
    adapter = build_voice_provider_registry(runtime).resolve("elevenlabs")
    assert isinstance(adapter, ElevenLabsProviderAdapter)
    adapter._client_factory = _client_factory(requests)

    with pytest.raises(Exception):
        adapter.list_voices(_profile())

    with get_session_factory()() as session:
        VoiceProviderCredentialStore(session, SettingsRepository(session)).save_api_key(ELEVENLABS_PROVIDER_KEY, SECRET_A)
    assert [voice.voice_id for voice in adapter.list_voices(_profile())] == ["voice_a"]
    assert adapter.readiness(_profile("voice_a")).status == "ready"

    with get_session_factory()() as session:
        VoiceProviderCredentialStore(session, SettingsRepository(session)).save_api_key(ELEVENLABS_PROVIDER_KEY, SECRET_B)
    assert [voice.voice_id for voice in adapter.list_voices(_profile())] == ["voice_b"]
    assert "voice_a" not in {voice.voice_id for voice in adapter.list_voices(_profile())}


def test_submit_and_execution_identity_use_effective_credential_without_leak(tmp_path: Path) -> None:
    requests: list[tuple[str, str, str]] = []
    runtime = Settings(root_dir=tmp_path, elevenlabs_api_key=ENV_SECRET, elevenlabs_api_base_url="https://api.elevenlabs.io")
    adapter = build_voice_provider_registry(runtime).resolve("elevenlabs")
    assert isinstance(adapter, ElevenLabsProviderAdapter)
    adapter._client_factory = _client_factory(requests)

    env_identity = adapter.execution_identity(_profile())
    assert env_identity["credential_fingerprint_sha256"] == hashlib.sha256(ENV_SECRET.encode()).hexdigest()

    with get_session_factory()() as session:
        VoiceProviderCredentialStore(session, SettingsRepository(session)).save_api_key(ELEVENLABS_PROVIDER_KEY, SECRET_A)
    stored_identity = adapter.execution_identity(_profile())
    assert stored_identity["credential_fingerprint_sha256"] == hashlib.sha256(SECRET_A.encode()).hexdigest()

    result = adapter.submit(_request(tmp_path, voice_id="voice_a"), _Control()).result
    assert result is not None
    assert any(key == SECRET_A and method == "POST" for key, method, _path in requests)
    rendered = json.dumps({"identity": stored_identity, "metadata": result.sanitized_metadata})
    assert SECRET_A not in rendered and ENV_SECRET not in rendered

    with get_session_factory()() as session:
        VoiceProviderCredentialStore(session, SettingsRepository(session)).save_api_key(ELEVENLABS_PROVIDER_KEY, SECRET_B)
    replacement = adapter.execution_identity(_profile())
    assert replacement["credential_fingerprint_sha256"] == hashlib.sha256(SECRET_B.encode()).hexdigest()
    assert len({env_identity["credential_fingerprint_sha256"], stored_identity["credential_fingerprint_sha256"], replacement["credential_fingerprint_sha256"]}) == 3


def test_test_connection_is_non_billable(tmp_path: Path) -> None:
    requests: list[tuple[str, str, str]] = []
    resolver = ElevenLabsCredentialResolver(Settings(root_dir=tmp_path, elevenlabs_api_key=SECRET_A))
    adapter = ElevenLabsProviderAdapter(resolver.settings, credential_resolver=resolver, client_factory=_client_factory(requests))
    result = adapter.test_connection()
    assert result["status"] == "ready"
    assert {path for _key, _method, path in requests} == {"/v1/models", "/v2/voices"}
    assert all(method == "GET" for _key, method, _path in requests)


@pytest.mark.parametrize("value", ["", "   ", "line1\nline2", "tab\tvalue", "nul\x00value", "x" * 513])
def test_invalid_key_input_is_rejected_safely(value: str) -> None:
    with pytest.raises(Exception) as exc:
        validate_voice_provider_api_key(value)
    assert "ElevenLabs API key" in str(exc.value)
    if value:
        assert value not in str(exc.value)
