from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
from pathlib import Path
import wave

from app.api.errors import AppError

CANONICAL_SAMPLE_RATE = 48_000
CANONICAL_CHANNELS = 1
CANONICAL_SAMPLE_WIDTH = 2
CANONICAL_MIME = "audio/wav"
CANONICAL_CODEC = "pcm_s16le"


@dataclass(frozen=True, slots=True)
class WavInfo:
    sample_rate_hz: int
    channels: int
    sample_width_bytes: int
    sample_count: int
    duration_seconds: float
    sha256: str
    size_bytes: int


def validate_canonical_wav_bytes(data: bytes, *, maximum_bytes: int = 104_857_600) -> WavInfo:
    if not data or len(data) > maximum_bytes:
        raise AppError("VOICE_AUDIO_INVALID", "Narration audio is empty or exceeds the configured size limit.", 422, retryable=False)
    try:
        with wave.open(io.BytesIO(data), "rb") as wav:
            channels = wav.getnchannels()
            sample_width = wav.getsampwidth()
            rate = wav.getframerate()
            frames = wav.getnframes()
            compression = wav.getcomptype()
            pcm = wav.readframes(frames)
    except (wave.Error, EOFError) as exc:
        raise AppError("VOICE_AUDIO_INVALID", "Narration audio is not a valid RIFF/WAVE file.", 422, retryable=False) from exc
    if compression != "NONE" or channels != CANONICAL_CHANNELS or sample_width != CANONICAL_SAMPLE_WIDTH or rate != CANONICAL_SAMPLE_RATE:
        raise AppError(
            "VOICE_AUDIO_FORMAT_INVALID",
            "Narration audio is not canonical mono PCM S16LE at 48 kHz.",
            422,
            retryable=False,
            details={"channels": channels, "sample_width_bytes": sample_width, "sample_rate_hz": rate, "compression": compression},
        )
    if frames <= 0 or len(pcm) != frames * channels * sample_width:
        raise AppError("VOICE_AUDIO_INVALID", "Narration audio is empty or truncated.", 422, retryable=False)
    return WavInfo(rate, channels, sample_width, frames, frames / rate, hashlib.sha256(data).hexdigest(), len(data))


AUDIO_ARTIFACT_VALIDATOR_VERSION = "canonical_pcm_wav_artifact_bytes_v1"


@dataclass(frozen=True, slots=True)
class AudioArtifactByteInfo:
    validator_version: str
    mime_type: str
    codec: str
    container: str
    sample_rate_hz: int
    channels: int
    sample_width_bytes: int
    sample_count: int
    duration_seconds: float
    byte_count: int
    sha256: str


def validate_audio_artifact_bytes(
    data: bytes,
    *,
    declared_mime_type: str | None,
    expected_size_bytes: int | None = None,
    expected_sha256: str | None = None,
    maximum_bytes: int = 104_857_600,
) -> AudioArtifactByteInfo:
    """Validate immutable audio bytes through the shared Phase 12 WAV authority.

    Phase 13 intentionally supports only the current canonical format: RIFF/WAVE,
    uncompressed mono PCM S16LE at 48 kHz.  The declared MIME type and persisted
    metadata are treated as claims and are checked against the actual bytes.
    """

    if declared_mime_type != CANONICAL_MIME:
        raise AppError(
            "AUDIO_FORMAT_UNSUPPORTED",
            "The declared audio format is not supported by the current strict byte validator.",
            422,
            retryable=False,
            details={"declared_mime_type": declared_mime_type or "missing"},
        )
    if not data:
        raise AppError("AUDIO_BYTES_MISSING", "Audio bytes are missing or empty.", 422, retryable=False)
    byte_count = len(data)
    if expected_size_bytes is None or expected_size_bytes < 0 or byte_count != expected_size_bytes:
        raise AppError(
            "AUDIO_SIZE_MISMATCH",
            "Audio byte count does not match persisted metadata.",
            422,
            retryable=False,
            details={"expected_byte_count": expected_size_bytes, "actual_byte_count": byte_count},
        )
    actual_sha256 = hashlib.sha256(data).hexdigest()
    if not expected_sha256 or actual_sha256 != expected_sha256:
        raise AppError(
            "AUDIO_HASH_MISMATCH",
            "Audio SHA-256 does not match persisted metadata.",
            422,
            retryable=False,
        )
    try:
        wav = validate_canonical_wav_bytes(data, maximum_bytes=maximum_bytes)
    except AppError as exc:
        if exc.error_code == "VOICE_AUDIO_FORMAT_INVALID":
            raise AppError(
                "AUDIO_FORMAT_UNSUPPORTED",
                "Audio bytes are not canonical PCM S16LE WAV.",
                422,
                retryable=False,
                details=exc.details,
            ) from exc
        raise AppError(
            "AUDIO_INVALID",
            "Audio bytes are malformed, empty, truncated, or internally inconsistent.",
            422,
            retryable=False,
        ) from exc
    return AudioArtifactByteInfo(
        validator_version=AUDIO_ARTIFACT_VALIDATOR_VERSION,
        mime_type=CANONICAL_MIME,
        codec=CANONICAL_CODEC,
        container="wav",
        sample_rate_hz=wav.sample_rate_hz,
        channels=wav.channels,
        sample_width_bytes=wav.sample_width_bytes,
        sample_count=wav.sample_count,
        duration_seconds=wav.duration_seconds,
        byte_count=wav.size_bytes,
        sha256=wav.sha256,
    )


def validate_canonical_wav_file(path: Path, *, maximum_bytes: int = 104_857_600) -> WavInfo:
    if not path.exists() or not path.is_file():
        raise AppError("VOICE_AUDIO_FILE_MISSING", "Narration audio output is missing.", 422, retryable=False)
    return validate_canonical_wav_bytes(path.read_bytes(), maximum_bytes=maximum_bytes)


def assemble_canonical_wav(segment_bytes: list[bytes]) -> tuple[bytes, WavInfo]:
    if not segment_bytes:
        raise AppError("VOICE_AUDIO_ASSEMBLY_INVALID", "At least one segment is required for narration assembly.", 422, retryable=False)
    frames: list[bytes] = []
    for data in segment_bytes:
        info = validate_canonical_wav_bytes(data)
        with wave.open(io.BytesIO(data), "rb") as wav:
            frames.append(wav.readframes(info.sample_count))
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(CANONICAL_CHANNELS)
        wav.setsampwidth(CANONICAL_SAMPLE_WIDTH)
        wav.setframerate(CANONICAL_SAMPLE_RATE)
        wav.writeframes(b"".join(frames))
    result = output.getvalue()
    return result, validate_canonical_wav_bytes(result)


def deterministic_fake_wav(text: str, *, speaking_rate: float = 1.0) -> bytes:
    """Test-only deterministic PCM signal; never exposed as a production profile."""
    duration_samples = max(480, int((len(text.encode("utf-8")) * 120) / max(speaking_rate, 0.1)))
    seed = hashlib.sha256(text.encode("utf-8")).digest()
    pcm = bytearray()
    for index in range(duration_samples):
        value = ((seed[index % len(seed)] - 128) * 24)
        pcm.extend(int(value).to_bytes(2, "little", signed=True))
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(CANONICAL_CHANNELS)
        wav.setsampwidth(CANONICAL_SAMPLE_WIDTH)
        wav.setframerate(CANONICAL_SAMPLE_RATE)
        wav.writeframes(bytes(pcm))
    return output.getvalue()
