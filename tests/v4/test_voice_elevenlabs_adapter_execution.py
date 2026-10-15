from __future__ import annotations

import hashlib
import json
from pathlib import Path
import wave

import httpx
import pytest

from app.api.errors import AppError
from app.core.config import Settings
from app.modules.v4.elevenlabs_client import ElevenLabsHttpClient
from app.modules.v4.voice_first_elevenlabs_adapter import VoiceFirstElevenLabsProviderAdapter
from app.modules.v4.voice_first_policy import ELEVENLABS_STYLE
from app.modules.v4.voice_provider_contracts import VoiceProviderPacingDirective, VoiceSynthesisRequest


class _Control:
    @property
    def execution_attempt_token(self) -> str:
        return "attempt-token"

    def cancellation_requested(self) -> bool:
        return False

    def deadline_reached(self) -> bool:
        return False


class _Cancelled(_Control):
    def cancellation_requested(self) -> bool:
        return True


class _CancelAfterPreparation(_Control):
    def __init__(self) -> None:
        self.cancel_checks = 0

    def cancellation_requested(self) -> bool:
        self.cancel_checks += 1
        return self.cancel_checks > 1


def _settings(tmp_path: Path, *, max_bytes: int = 104_857_600) -> Settings:
    return Settings(
        root_dir=tmp_path,
        elevenlabs_api_key="test-eleven-key",
        elevenlabs_api_base_url="https://api.elevenlabs.io",
        tts_provider_max_output_bytes=max_bytes,
    )


def _pcm(sample_count: int = 2400) -> bytes:
    return b"\x00\x00" * sample_count


def _request(tmp_path: Path, *, model_id: str = "eleven_multilingual_v2", speed: float = 1.0, **overrides) -> VoiceSynthesisRequest:
    exact_text = "Hello."
    values = {
        "operation_id": "operation",
        "segment_task_id": "task",
        "execution_attempt_token": "attempt-token",
        "idempotency_key": "idempotency",
        "exact_text": exact_text,
        "voice_id": "voice_a",
        "language": "en",
        "speaking_rate": speed,
        "requested_sample_rate_hz": 24_000,
        "requested_output_format": "wav_pcm_s16le",
        "provider_options": {
            "model_id": model_id,
            "narration_language": "en",
            "stability": 0.61,
            "similarity_boost": 0.72,
            "use_speaker_boost": True,
            "_voice_first_segment_payloads": {
                "task": {
                    "provider_text": 'Hello.<break time="0.55s" />',
                    "seed": 123456,
                    "canonical_spoken_text_sha256": hashlib.sha256(exact_text.encode("utf-8")).hexdigest(),
                }
            },
        },
        "attempt_workspace": tmp_path / "attempt",
        "previous_text": "Before.",
        "next_text": "After.",
        "pacing": VoiceProviderPacingDirective(
            control_model="relative_multiplier",
            value=speed,
            target_narration_wpm=150,
            policy_version="test_elevenlabs_policy_v1",
            value_range=(0.7, 1.2),
        ),
    }
    values.update(overrides)
    return VoiceSynthesisRequest(**values)


def _adapter(tmp_path: Path, handler, *, max_bytes: int = 104_857_600) -> tuple[VoiceFirstElevenLabsProviderAdapter, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(recording), base_url="https://api.elevenlabs.io")
    factory = lambda key, base, max_response, timeout: ElevenLabsHttpClient(key, base, max_response, timeout, client=client)
    return VoiceFirstElevenLabsProviderAdapter(_settings(tmp_path, max_bytes=max_bytes), client_factory=factory), requests


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    with VoiceFirstElevenLabsProviderAdapter._cache_lock:
        VoiceFirstElevenLabsProviderAdapter._voice_cache.clear()
        VoiceFirstElevenLabsProviderAdapter._model_cache.clear()


@pytest.mark.parametrize(
    ("model_id", "speed"),
    [("eleven_flash_v2_5", 1.12), ("eleven_multilingual_v2", 0.93)],
)
def test_voice_first_synthesis_request_uses_standard_tts_contract(tmp_path: Path, model_id: str, speed: float) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/voices":
            return httpx.Response(200, json={"voices": [{"voice_id": "voice_a", "name": "Voice A"}], "has_more": False})
        assert request.method == "POST"
        assert request.url.path == "/v1/text-to-speech/voice_a"
        assert "/with-timestamps" not in request.url.path
        assert request.url.params["output_format"] == "pcm_24000"
        assert request.headers["xi-api-key"] == "test-eleven-key"
        body = json.loads(request.content)
        assert body == {
            "text": 'Hello.<break time="0.55s" />',
            "model_id": model_id,
            "voice_settings": {
                "stability": 0.61,
                "similarity_boost": 0.72,
                "style": ELEVENLABS_STYLE,
                "use_speaker_boost": True,
                "speed": speed,
            },
            "seed": 123456,
            "apply_text_normalization": "off",
            "previous_text": "Before.",
            "next_text": "After.",
        }
        return httpx.Response(200, content=_pcm(), headers={"request-id": "req_123"})

    adapter, requests = _adapter(tmp_path, handler)
    submission = adapter.submit(_request(tmp_path, model_id=model_id, speed=speed), _Control())

    assert submission.status == "completed"
    assert submission.result is not None
    assert submission.result.provider_task_id == "req_123"
    assert submission.result.sanitized_metadata["model_id"] == model_id
    assert submission.result.sanitized_metadata["text_normalization"] == "off"
    assert "test-eleven-key" not in json.dumps(submission.result.sanitized_metadata)
    with wave.open(str(submission.result.audio_file_path), "rb") as wav:
        assert wav.getframerate() == 24_000
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
    assert len(requests) == 2
    assert sum(1 for request in requests if request.method == "POST") == 1


@pytest.mark.parametrize("speed", [0.69, 1.21])
def test_speed_out_of_range_fails_before_post(tmp_path: Path, speed: float) -> None:
    posts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal posts
        if request.url.path == "/v2/voices":
            return httpx.Response(200, json={"voices": [{"voice_id": "voice_a", "name": "Voice A"}], "has_more": False})
        posts += 1
        return httpx.Response(500, json={"detail": {"code": "internal_error"}})

    adapter, _ = _adapter(tmp_path, handler)

    with pytest.raises(AppError) as exc:
        adapter.submit(_request(tmp_path, speed=speed), _Control())

    assert exc.value.error_code == "ELEVENLABS_SPEED_UNSUPPORTED"
    assert posts == 0


def test_cancelled_before_provider_call_makes_zero_requests(tmp_path: Path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500, json={})

    adapter, _ = _adapter(tmp_path, handler)

    with pytest.raises(AppError) as exc:
        adapter.submit(_request(tmp_path), _Cancelled())

    assert exc.value.error_code == "VOICE_OPERATION_CANCELLED"
    assert calls == 0


def test_cancel_visible_after_preparation_skips_billable_post(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/voices":
            return httpx.Response(200, json={"voices": [{"voice_id": "voice_a", "name": "Voice A"}], "has_more": False})
        return httpx.Response(200, content=_pcm())

    adapter, requests = _adapter(tmp_path, handler)

    with pytest.raises(AppError) as exc:
        adapter.submit(_request(tmp_path), _CancelAfterPreparation())

    assert exc.value.error_code == "VOICE_OPERATION_CANCELLED"
    assert [request.url.path for request in requests] == ["/v2/voices"]
    assert sum(1 for request in requests if request.method == "POST") == 0
