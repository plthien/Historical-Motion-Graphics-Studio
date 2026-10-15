from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from app.api.errors import AppError
from app.modules.v4.voice_first_policy import (
    DEFAULT_SIMILARITY_BOOST,
    DEFAULT_STABILITY,
    DEFAULT_USE_SPEAKER_BOOST,
)


@dataclass(frozen=True, slots=True)
class ElevenLabsModelPolicy:
    model_id: str
    policy_version: str
    maximum_input_characters: int
    wire_output_format: str
    wire_sample_rate_hz: int
    supported_languages: tuple[str, ...]
    supports_language_code: bool
    supports_character_timing: bool
    supports_previous_next_text: bool
    supports_audio_tags: bool


# Provider capability metadata is owned here. Keep the model lists distinct:
# Flash v2.5 currently supports 32 languages, while Multilingual v2 supports 29.
ELEVEN_MULTILINGUAL_V2_LANGUAGES = (
    "en", "ja", "zh", "de", "hi", "fr", "ko", "pt", "it", "es", "id", "nl",
    "tr", "fil", "pl", "sv", "bg", "ro", "ar", "cs", "el", "fi", "hr", "ms",
    "sk", "da", "ta", "uk", "ru",
)
ELEVEN_FLASH_V2_5_LANGUAGES = (
    *ELEVEN_MULTILINGUAL_V2_LANGUAGES,
    "hu", "no", "vi",
)

ELEVEN_MULTILINGUAL_V2 = ElevenLabsModelPolicy(
    model_id="eleven_multilingual_v2",
    policy_version="multilingual_v2_v1",
    maximum_input_characters=10_000,
    wire_output_format="pcm_24000",
    wire_sample_rate_hz=24_000,
    supported_languages=ELEVEN_MULTILINGUAL_V2_LANGUAGES,
    supports_language_code=False,
    supports_character_timing=False,
    supports_previous_next_text=True,
    supports_audio_tags=False,
)

ELEVEN_FLASH_V2_5 = ElevenLabsModelPolicy(
    model_id="eleven_flash_v2_5",
    policy_version="flash_v2_5_v1",
    maximum_input_characters=40_000,
    wire_output_format="pcm_24000",
    wire_sample_rate_hz=24_000,
    supported_languages=ELEVEN_FLASH_V2_5_LANGUAGES,
    supports_language_code=False,
    supports_character_timing=False,
    supports_previous_next_text=True,
    supports_audio_tags=False,
)

DEFAULT_ELEVENLABS_MODEL_ID = ELEVEN_MULTILINGUAL_V2.model_id
ELEVENLABS_POLICIES: dict[str, ElevenLabsModelPolicy] = {
    ELEVEN_FLASH_V2_5.model_id: ELEVEN_FLASH_V2_5,
    ELEVEN_MULTILINGUAL_V2.model_id: ELEVEN_MULTILINGUAL_V2,
}
_LANGUAGE_CODE = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")


@dataclass(frozen=True, slots=True)
class ElevenLabsProviderOptions:
    model_id: str
    narration_language: str
    policy: ElevenLabsModelPolicy
    stability: float
    similarity_boost: float
    use_speaker_boost: bool


def resolve_elevenlabs_policy(model_id: str) -> ElevenLabsModelPolicy:
    policy = ELEVENLABS_POLICIES.get(model_id)
    if policy is None:
        raise AppError(
            "ELEVENLABS_MODEL_UNSUPPORTED",
            "The selected ElevenLabs model is not supported by this Voice provider.",
            422,
            retryable=False,
            details={"model_id": model_id},
        )
    return policy


def _unit_interval(value: object, *, field: str, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AppError("ELEVENLABS_PROVIDER_OPTIONS_INVALID", f"ElevenLabs {field} must be numeric.", 422, retryable=False)
    parsed = float(value)
    if not 0.0 <= parsed <= 1.0:
        raise AppError("ELEVENLABS_PROVIDER_OPTIONS_INVALID", f"ElevenLabs {field} must be between 0 and 1.", 422, retryable=False)
    return parsed


def parse_elevenlabs_provider_options(raw: dict[str, Any] | None) -> ElevenLabsProviderOptions:
    options = dict(raw or {})
    allowed = {
        "model_id",
        "narration_language",
        "stability",
        "similarity_boost",
        "use_speaker_boost",
    }
    unknown = sorted(key for key in options if key not in allowed)
    if unknown:
        raise AppError(
            "ELEVENLABS_PROVIDER_OPTIONS_INVALID",
            "ElevenLabs provider options contain unsupported keys.",
            422,
            retryable=False,
            details={"unsupported_options": unknown},
        )
    model_id = options.get("model_id", DEFAULT_ELEVENLABS_MODEL_ID)
    if not isinstance(model_id, str) or not model_id.strip():
        raise AppError("ELEVENLABS_MODEL_UNSUPPORTED", "ElevenLabs model_id must be a non-empty string.", 422, retryable=False)
    model_id = model_id.strip()
    policy = resolve_elevenlabs_policy(model_id)
    narration_language = options.get("narration_language", "en")
    if not isinstance(narration_language, str) or not narration_language.strip():
        raise AppError(
            "ELEVENLABS_LANGUAGE_UNSUPPORTED",
            "ElevenLabs narration_language must be a non-empty language code.",
            422,
            retryable=False,
        )
    language = narration_language.strip().lower()
    if not _LANGUAGE_CODE.fullmatch(language) or language not in policy.supported_languages:
        raise AppError(
            "ELEVENLABS_LANGUAGE_UNSUPPORTED",
            "The selected narration language is not supported by the ElevenLabs model policy.",
            422,
            retryable=False,
            details={"model_id": model_id, "narration_language": language},
        )
    use_speaker_boost = options.get("use_speaker_boost", DEFAULT_USE_SPEAKER_BOOST)
    if not isinstance(use_speaker_boost, bool):
        raise AppError("ELEVENLABS_PROVIDER_OPTIONS_INVALID", "ElevenLabs use_speaker_boost must be boolean.", 422, retryable=False)
    return ElevenLabsProviderOptions(
        model_id=model_id,
        narration_language=language,
        policy=policy,
        stability=_unit_interval(options.get("stability"), field="stability", default=DEFAULT_STABILITY),
        similarity_boost=_unit_interval(options.get("similarity_boost"), field="similarity_boost", default=DEFAULT_SIMILARITY_BOOST),
        use_speaker_boost=use_speaker_boost,
    )
