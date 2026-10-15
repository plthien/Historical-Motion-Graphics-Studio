from __future__ import annotations

import pytest

from app.api.errors import AppError
from app.modules.v4.elevenlabs_model_policy import (
    ELEVENLABS_POLICIES,
    ELEVEN_MULTILINGUAL_V2,
    ElevenLabsModelPolicy,
    parse_elevenlabs_provider_options,
    resolve_elevenlabs_policy,
)


def test_eleven_multilingual_v2_policy_contract() -> None:
    policy = resolve_elevenlabs_policy("eleven_multilingual_v2")

    assert policy is ELEVEN_MULTILINGUAL_V2
    assert policy.maximum_input_characters == 10_000
    assert policy.wire_output_format == "pcm_24000"
    assert policy.wire_sample_rate_hz == 24_000
    assert len(policy.supported_languages) == 29
    assert policy.supports_previous_next_text is True
    assert policy.supports_character_timing is False
    assert policy.supports_language_code is False
    assert policy.supports_audio_tags is False


def test_unknown_model_is_rejected() -> None:
    with pytest.raises(AppError) as exc:
        parse_elevenlabs_provider_options({"model_id": "eleven_v3"})

    assert exc.value.error_code == "ELEVENLABS_MODEL_UNSUPPORTED"
    assert exc.value.retryable is False


def test_policy_registry_accepts_future_policy_without_new_adapter_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    synthetic = ElevenLabsModelPolicy(
        model_id="synthetic_future_model",
        policy_version="synthetic_v1",
        maximum_input_characters=123,
        wire_output_format="pcm_24000",
        wire_sample_rate_hz=24_000,
        supported_languages=("en",),
        supports_language_code=True,
        supports_character_timing=True,
        supports_previous_next_text=True,
        supports_audio_tags=True,
    )
    patched = dict(ELEVENLABS_POLICIES)
    patched[synthetic.model_id] = synthetic
    monkeypatch.setattr("app.modules.v4.elevenlabs_model_policy.ELEVENLABS_POLICIES", patched)

    assert resolve_elevenlabs_policy("synthetic_future_model") is synthetic


@pytest.mark.parametrize(
    "options",
    [
        {"narration_language": 7},
        {"narration_language": "xx"},
        {"model_id": 7},
        {"model_id": "eleven_multilingual_v2", "unexpected": True},
    ],
)
def test_provider_options_are_strict(options: dict[str, object]) -> None:
    with pytest.raises(AppError):
        parse_elevenlabs_provider_options(options)
