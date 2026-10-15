from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from app.api.errors import AppError
from app.core.config import Settings
from app.modules.v4.elevenlabs_client import ElevenLabsHttpClient
from app.modules.v4.elevenlabs_provider_adapter import ElevenLabsProviderAdapter
from app.modules.v4.voice_provider_contracts import VoiceProviderProfile, VoiceProviderRegistry


def _settings(tmp_path: Path) -> Settings:
    return Settings(root_dir=tmp_path, elevenlabs_api_key="test-eleven-key", elevenlabs_api_base_url="https://api.elevenlabs.io")


def _profile() -> VoiceProviderProfile:
    return VoiceProviderProfile(
        profile_id="eleven",
        provider_key="elevenlabs",
        enabled=True,
        display_name="Eleven",
        voice_id="voice_a",
        language="en",
        speaking_rate=1.0,
        requested_sample_rate_hz=24_000,
        requested_output_format="wav_pcm_s16le",
        provider_options={"model_id": "eleven_multilingual_v2", "narration_language": "en"},
        maximum_segment_characters=1_000,
    )


def _adapter(tmp_path: Path, handler, *, clock=lambda: 0.0) -> tuple[ElevenLabsProviderAdapter, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(recording), base_url="https://api.elevenlabs.io")
    factory = lambda key, base, max_bytes, timeout: ElevenLabsHttpClient(key, base, max_bytes, timeout, client=client)
    return ElevenLabsProviderAdapter(_settings(tmp_path), client_factory=factory, clock=clock), requests


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    with ElevenLabsProviderAdapter._cache_lock:
        ElevenLabsProviderAdapter._voice_cache.clear()
        ElevenLabsProviderAdapter._model_cache.clear()


def test_one_page_catalog_uses_v2_endpoint_and_total_count_is_not_authority(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/voices"
        assert request.url.params["page_size"] == "100"
        assert request.url.params["include_total_count"] == "false"
        assert request.headers["xi-api-key"] == "test-eleven-key"
        return httpx.Response(200, json={"voices": [{"voice_id": "voice_a", "name": "Voice A"}], "has_more": False, "total_count": 999})

    adapter, requests = _adapter(tmp_path, handler)

    voices = adapter.list_voices(_profile())

    assert [(voice.voice_id, voice.display_name, voice.language) for voice in voices] == [("voice_a", "Voice A", "en")]
    assert len(requests) == 1


def test_catalog_paginates_on_has_more_and_next_page_token(tmp_path: Path) -> None:
    pages = [
        {"voices": [{"voice_id": "voice_a", "name": "Voice A"}], "has_more": True, "next_page_token": "next-1", "total_count": 1},
        {"voices": [{"voice_id": "voice_b", "name": "Voice B"}], "has_more": False, "total_count": 1},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        index = 1 if request.url.params.get("next_page_token") == "next-1" else 0
        return httpx.Response(200, json=pages[index])

    adapter, requests = _adapter(tmp_path, handler)

    assert [voice.voice_id for voice in adapter.list_voices(_profile())] == ["voice_a", "voice_b"]
    assert requests[1].url.params["next_page_token"] == "next-1"


@pytest.mark.parametrize(
    "payload",
    [
        {"voices": [], "has_more": True},
        {"voices": [], "has_more": True, "next_page_token": ""},
        {"voices": [], "has_more": "yes", "next_page_token": "x"},
        {"voices": [7], "has_more": False},
        {"voices": [{"voice_id": "", "name": "Voice"}], "has_more": False},
    ],
)
def test_malformed_catalog_is_rejected(tmp_path: Path, payload: dict[str, Any]) -> None:
    adapter, _ = _adapter(tmp_path, lambda request: httpx.Response(200, json=payload))

    with pytest.raises(AppError) as exc:
        VoiceProviderRegistry().register(adapter)
        adapter.list_voices(_profile())

    assert exc.value.error_code == "ELEVENLABS_VOICE_CATALOG_INVALID"


def test_registry_rejects_duplicate_voice_ids(tmp_path: Path) -> None:
    adapter, _ = _adapter(
        tmp_path,
        lambda request: httpx.Response(
            200,
            json={"voices": [{"voice_id": "dup", "name": "One"}, {"voice_id": "dup", "name": "Two"}], "has_more": False},
        ),
    )
    registry = VoiceProviderRegistry()
    registry.register(adapter)

    with pytest.raises(AppError) as exc:
        registry.list_voices("elevenlabs", _profile())

    assert exc.value.error_code == "VOICE_PROVIDER_CATALOG_INVALID"


def test_success_cache_hits_then_expires_and_failures_are_not_cached(tmp_path: Path) -> None:
    now = 0.0
    calls = 0

    def clock() -> float:
        return now

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"voices": [{"voice_id": f"voice_{calls}", "name": "Voice"}], "has_more": False})

    adapter, _ = _adapter(tmp_path, handler, clock=clock)
    assert adapter.list_voices(_profile())[0].voice_id == "voice_1"
    assert adapter.list_voices(_profile())[0].voice_id == "voice_1"
    now = 61.0
    assert adapter.list_voices(_profile())[0].voice_id == "voice_2"

    with ElevenLabsProviderAdapter._cache_lock:
        ElevenLabsProviderAdapter._voice_cache.clear()
    failing, _ = _adapter(tmp_path, lambda request: httpx.Response(401, json={"detail": {"type": "authentication_error", "code": "bad"}}), clock=clock)
    with pytest.raises(AppError):
        failing.list_voices(_profile())
    with pytest.raises(AppError):
        failing.list_voices(_profile())
