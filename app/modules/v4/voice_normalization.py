from __future__ import annotations

# Python 3.12 runtime note: the current minimal PCM-WAV converter uses audioop.
# audioop is removed in Python 3.13+, so a runtime upgrade must replace this
# implementation behind VoiceAudioNormalizer without changing the canonical
# 48 kHz mono PCM S16LE artifact contract.
import audioop
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import wave

from app.api.errors import AppError
from app.modules.v4.voice_alignment import (
    fallback_segment_alignment,
    validate_provider_word_timings,
    word_timings_from_provider_characters,
)
from app.modules.v4.voice_audio import (
    CANONICAL_CHANNELS,
    CANONICAL_SAMPLE_RATE,
    CANONICAL_SAMPLE_WIDTH,
    WavInfo,
    validate_canonical_wav_file,
)


@dataclass(frozen=True, slots=True)
class NormalizedVoiceTiming:
    timing_source: str
    words: list[dict[str, Any]]
    character_boundaries: list[int] | None = None


class VoiceAudioNormalizer:
    """Provider-output boundary for canonical Voice segment audio.

    Provider adapters may return provider-native PCM WAV characteristics. The
    durable Voice artifact contract remains fixed at mono PCM S16LE 48 kHz. A
    canonical provider result is returned unchanged; supported non-canonical PCM
    WAV is converted behind this boundary before artifact materialization.
    """

    def __init__(self, *, maximum_bytes: int = 104_857_600) -> None:
        self.maximum_bytes = maximum_bytes

    def normalize_file(self, source_path: Path) -> tuple[Path, WavInfo]:
        try:
            return source_path, validate_canonical_wav_file(source_path, maximum_bytes=self.maximum_bytes)
        except AppError as exc:
            if exc.error_code not in {"VOICE_AUDIO_FORMAT_INVALID", "VOICE_AUDIO_INVALID"}:
                raise

        if not source_path.is_file() or source_path.stat().st_size <= 0:
            raise AppError(
                "VOICE_PROVIDER_AUDIO_INVALID",
                "Voice provider audio output is missing or empty.",
                422,
                retryable=False,
            )
        if source_path.stat().st_size > self.maximum_bytes:
            raise AppError(
                "VOICE_PROVIDER_AUDIO_INVALID",
                "Voice provider audio output exceeds the configured size limit.",
                422,
                retryable=False,
            )

        try:
            with wave.open(str(source_path), "rb") as wav:
                channels = wav.getnchannels()
                sample_width = wav.getsampwidth()
                sample_rate = wav.getframerate()
                frame_count = wav.getnframes()
                compression = wav.getcomptype()
                pcm = wav.readframes(frame_count)
        except (wave.Error, EOFError, OSError) as exc:
            raise AppError(
                "VOICE_PROVIDER_AUDIO_FORMAT_UNSUPPORTED",
                "Voice provider audio is not a supported PCM WAV source.",
                422,
                retryable=False,
            ) from exc

        if compression != "NONE" or channels not in {1, 2} or sample_width not in {1, 2, 3, 4} or sample_rate <= 0:
            raise AppError(
                "VOICE_PROVIDER_AUDIO_FORMAT_UNSUPPORTED",
                "Voice provider audio format cannot be normalized by the current Voice boundary.",
                422,
                retryable=False,
            )
        if frame_count <= 0 or not pcm:
            raise AppError(
                "VOICE_PROVIDER_AUDIO_INVALID",
                "Voice provider audio is empty or truncated.",
                422,
                retryable=False,
            )

        converted = pcm
        width = sample_width
        if channels == 2:
            converted = audioop.tomono(converted, width, 0.5, 0.5)
        if width != CANONICAL_SAMPLE_WIDTH:
            converted = audioop.lin2lin(converted, width, CANONICAL_SAMPLE_WIDTH)
            width = CANONICAL_SAMPLE_WIDTH
        if sample_rate != CANONICAL_SAMPLE_RATE:
            converted, _state = audioop.ratecv(
                converted,
                width,
                CANONICAL_CHANNELS,
                sample_rate,
                CANONICAL_SAMPLE_RATE,
                None,
            )

        normalized_path = source_path.with_name(f"{source_path.stem}.canonical.wav")
        try:
            with wave.open(str(normalized_path), "wb") as wav:
                wav.setnchannels(CANONICAL_CHANNELS)
                wav.setsampwidth(CANONICAL_SAMPLE_WIDTH)
                wav.setframerate(CANONICAL_SAMPLE_RATE)
                wav.writeframes(converted)
            info = validate_canonical_wav_file(normalized_path, maximum_bytes=self.maximum_bytes)
        except Exception:
            normalized_path.unlink(missing_ok=True)
            raise
        return normalized_path, info


class VoiceTimingNormalizer:
    """Single authority for provider timing and deterministic Voice fallback."""

    def normalize_segment(
        self,
        *,
        exact_text: str,
        source_start: int,
        sample_start: int,
        sample_end: int,
        duration_seconds: float,
        provider_word_timings: list[dict[str, Any]] | None,
        provider_character_timings: list[dict[str, Any]] | None,
    ) -> NormalizedVoiceTiming:
        if provider_word_timings:
            words = validate_provider_word_timings(
                exact_text=exact_text,
                duration_seconds=duration_seconds,
                timings=provider_word_timings,
            )
            return NormalizedVoiceTiming(
                timing_source="provider_word_timing",
                words=self._provider_words_to_samples(
                    words=words,
                    source_start=source_start,
                    sample_start=sample_start,
                    sample_end=sample_end,
                    duration_seconds=duration_seconds,
                ),
            )

        if provider_character_timings:
            words = word_timings_from_provider_characters(
                exact_text=exact_text,
                duration_seconds=duration_seconds,
                timings=provider_character_timings,
            )
            return NormalizedVoiceTiming(
                timing_source="provider_character_timing",
                words=self._provider_words_to_samples(
                    words=words,
                    source_start=source_start,
                    sample_start=sample_start,
                    sample_end=sample_end,
                    duration_seconds=duration_seconds,
                ),
            )

        fallback = fallback_segment_alignment(
            exact_text=exact_text,
            source_start=source_start,
            sample_start=sample_start,
            sample_end=sample_end,
        )
        return NormalizedVoiceTiming(
            timing_source=str(fallback["timing_source"]),
            words=list(fallback["words"]),
            character_boundaries=list(fallback["character_boundaries"]),
        )

    @staticmethod
    def _provider_words_to_samples(
        *,
        words: list[dict[str, Any]],
        source_start: int,
        sample_start: int,
        sample_end: int,
        duration_seconds: float,
    ) -> list[dict[str, Any]]:
        sample_span = sample_end - sample_start
        if duration_seconds <= 0 or sample_span <= 0:
            raise ValueError("Provider timing normalization requires positive duration and sample span.")
        normalized: list[dict[str, Any]] = []
        for index, word in enumerate(words, 1):
            local_start = int(word["local_source_start"])
            local_end = int(word["local_source_end"])
            start_seconds = float(word["start_seconds"])
            end_seconds = float(word["end_seconds"])
            normalized.append(
                {
                    "word_id": f"word_{index:05d}",
                    "text": str(word["text"]),
                    "source_start": source_start + local_start,
                    "source_end": source_start + local_end,
                    "sample_start": sample_start + min(sample_span, round(sample_span * start_seconds / duration_seconds)),
                    "sample_end": sample_start + min(sample_span, round(sample_span * end_seconds / duration_seconds)),
                }
            )
        return normalized
