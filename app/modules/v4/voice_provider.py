from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from app.api.errors import AppError
from app.modules.v4.voice_audio import deterministic_fake_wav, validate_canonical_wav_file


@dataclass(frozen=True, slots=True)
class TTSRequest:
    operation_id: str
    segment_task_id: str
    idempotency_key: str
    exact_text: str
    voice_id: str
    language: str
    speaking_rate: float
    sample_rate_hz: int
    output_format: str
    attempt_output_path: Path
    cancellation_check: Callable[[], bool] | None = None


@dataclass(frozen=True, slots=True)
class TTSResult:
    provider: str
    provider_task_id: str | None
    audio_file_path: Path
    source_mime_type: str
    sample_rate_hz: int
    channels: int
    sample_width_bytes: int
    duration_seconds: float
    provider_word_timings: list[dict[str, Any]] | None = None
    provider_character_timings: list[dict[str, Any]] | None = None
    sanitized_metadata: dict[str, Any] = field(default_factory=dict)


class TTSProvider(Protocol):
    def synthesize(self, request: TTSRequest) -> TTSResult: ...


class DisabledTTSProvider:
    def synthesize(self, request: TTSRequest) -> TTSResult:
        raise AppError("TTS_PROVIDER_DISABLED", "The TTS provider is disabled.", 409, retryable=False)


class DeterministicFakeTTSProvider:
    def __init__(self) -> None:
        self.calls: list[TTSRequest] = []

    def synthesize(self, request: TTSRequest) -> TTSResult:
        self.calls.append(request)
        request.attempt_output_path.parent.mkdir(parents=True, exist_ok=True)
        request.attempt_output_path.write_bytes(
            deterministic_fake_wav(request.exact_text, speaking_rate=request.speaking_rate)
        )
        info = validate_canonical_wav_file(request.attempt_output_path)
        return TTSResult(
            provider="fake",
            provider_task_id=f"fake:{request.idempotency_key}",
            audio_file_path=request.attempt_output_path,
            source_mime_type="audio/wav",
            sample_rate_hz=info.sample_rate_hz,
            channels=info.channels,
            sample_width_bytes=info.sample_width_bytes,
            duration_seconds=info.duration_seconds,
            sanitized_metadata={"deterministic": True},
        )
