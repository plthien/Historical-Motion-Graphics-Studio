from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.core.config import Settings
from app.modules.v4.elevenlabs_client import ElevenLabsHttpClient
from app.modules.v4.elevenlabs_provider_adapter import ElevenLabsProviderAdapter
from app.modules.v4.voice_provider_contracts import VoiceProviderProfile
from app.services.tts_settings import TTSSettingsService
from app.repositories import SettingsRepository


def _settings(tmp_path: Path, *, api_key: str = "test-key") -> Settings:
    return Settings(root_dir=tmp_path, elevenlabs_api_key=api_key, elevenlabs_api_base_url="https://api.elevenlabs.io")


def _profile(**overrides) -> VoiceProviderProfile:
    values = {
        "profile_id": "eleven",
        "provider_key": "elevenlabs",
        "enabled": True,
        "display_name": "Eleven",
        "voice_id": "voice_a",
        "language": "en",
        "speaking_rate": 1.0,
        "requested_sample_rate_hz": 24_000,
        "requested_output_format": "wav_pcm_s16le",
        "provider_options": {"model_id": "eleven_multilingual_v2", "narration_language": "en"},
        "maximum_segment_characters": 1_000,
    }
    values.update(overrides)
    return VoiceProviderProfile(**values)


def _adapter(tmp_path: Path, handler, *, api_key: str = "test-key") -> ElevenLabsProviderAdapter:
    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.elevenlabs.io")
    factory = lambda key, base, max_bytes, timeout: ElevenLabsHttpClient(key, base, max_bytes, timeout, client=client)
    return ElevenLabsProviderAdapter(_settings(tmp_path, api_key=api_key), client_factory=factory)


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    with ElevenLabsProviderAdapter._cache_lock:
        ElevenLabsProviderAdapter._voice_cache.clear()
        ElevenLabsProviderAdapter._model_cache.clear()


def _ready_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/v1/models":
        return httpx.Response(
            200,
            json=[
                {
                    "model_id": "eleven_multilingual_v2",
                    "can_do_text_to_speech": True,
                    "maximum_text_length_per_request": 10_000,
                    "languages": [{"language_id": "en"}],
                }
            ],
        )
    return httpx.Response(200, json={"voices": [{"voice_id": "voice_a", "name": "Voice A"}], "has_more": False})


def test_missing_api_key_is_blocked_without_network(tmp_path: Path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _ready_handler(request)

    result = _adapter(tmp_path, handler, api_key="").readiness(_profile())

    assert result.status == "blocked"
    assert result.issue_code == "ELEVENLABS_API_KEY_NOT_CONFIGURED"
    assert calls == 0


def test_ready_profile_uses_models_and_catalog_without_synthesis(tmp_path: Path) -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return _ready_handler(request)

    result = _adapter(tmp_path, handler).readiness(_profile())

    assert result.status == "ready"
    assert paths == ["/v1/models", "/v2/voices"]


@pytest.mark.parametrize(
    ("model_payload", "issue"),
    [
        ([], "ELEVENLABS_MODEL_UNAVAILABLE"),
        ([{"model_id": "eleven_multilingual_v2", "can_do_text_to_speech": False}], "ELEVENLABS_MODEL_TTS_UNAVAILABLE"),
        ([{"model_id": "eleven_multilingual_v2", "can_do_text_to_speech": True, "maximum_text_length_per_request": 500}], "ELEVENLABS_MODEL_SEGMENT_LIMIT_EXCEEDED"),
        ([{"model_id": "eleven_multilingual_v2", "can_do_text_to_speech": True, "languages": [{"language_id": "ja"}]}], "ELEVENLABS_LANGUAGE_UNSUPPORTED"),
    ],
)
def test_model_readiness_failures(tmp_path: Path, model_payload: list[dict[str, object]], issue: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(200, json=model_payload)
        return httpx.Response(200, json={"voices": [{"voice_id": "voice_a", "name": "Voice A"}], "has_more": False})

    result = _adapter(tmp_path, handler).readiness(_profile(maximum_segment_characters=1_000))

    assert result.status == "blocked"
    assert result.issue_code == issue


@pytest.mark.parametrize(
    ("profile", "issue"),
    [
        (_profile(requested_sample_rate_hz=48_000), "VOICE_PROVIDER_SAMPLE_RATE_UNSUPPORTED"),
        (_profile(requested_output_format="mp3"), "VOICE_PROVIDER_OUTPUT_FORMAT_UNSUPPORTED"),
        (_profile(speaking_rate=1.3), "ELEVENLABS_SPEED_UNSUPPORTED"),
    ],
)
def test_static_readiness_guards_do_not_call_elevenlabs(tmp_path: Path, profile: VoiceProviderProfile, issue: str) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _ready_handler(request)

    result = _adapter(tmp_path, handler).readiness(profile)

    assert result.issue_code == issue
    assert calls == 0


def test_selected_voice_missing_is_blocked(tmp_path: Path) -> None:
    result = _adapter(tmp_path, _ready_handler).readiness(_profile(voice_id="missing"))

    assert result.status == "blocked"
    assert result.issue_code == "TTS_VOICE_INVALID"


def test_tts_settings_readiness_allows_null_default_with_runtime_catalog(tmp_path: Path) -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return _ready_handler(request)

    adapter = _adapter(tmp_path, handler)
    from app.modules.v4.voice_provider_contracts import VoiceProviderRegistry
    from app.db.session import get_session_factory

    registry = VoiceProviderRegistry()
    registry.register(adapter)
    with get_session_factory()() as session:
        service = TTSSettingsService(session, SettingsRepository(session), runtime_settings=_settings(tmp_path), registry=registry)
        payload = {
            "active_profile_id": "eleven",
            "profiles": [
                {
                    "profile_id": "eleven",
                    "provider": "elevenlabs",
                    "enabled": True,
                    "display_name": "Eleven",
                    "default_voice_id": None,
                    "default_speaking_rate": 1.0,
                    "sample_rate_hz": 24_000,
                    "output_format": "wav_pcm_s16le",
                    "maximum_segment_characters": 1_000,
                    "provider_options": {"model_id": "eleven_multilingual_v2", "narration_language": "en"},
                }
            ],
        }
        service.put_settings(payload)
        readiness = service.readiness("eleven", None)

    assert readiness["status"] == "ready"
    assert readiness["voice_id"] is None
    assert paths == ["/v2/voices", "/v1/models"]
