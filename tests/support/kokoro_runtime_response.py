from __future__ import annotations

from kokoro.runtime_response_calibration import (
    KOKORO_FLOW_VERSION,
    RUNTIME_RESPONSE_RATES,
    RUNTIME_RESPONSE_TEXTS,
    RUNTIME_RESPONSE_TEXT_SHA256,
    RUNTIME_RESPONSE_TEXT_WORD_COUNTS,
    derive_native_wpm_at_rate_1,
    derive_runtime_response_beta,
    derive_runtime_native_wpm_at_rate_1,
    derive_runtime_response_sample,
)


def runtime_response_measurement(
    beta: float = 0.72,
    *,
    voice_id: str = "af_heart",
    native_wpm_at_rate_1: float = 195.0,
) -> dict[str, object]:
    samples: list[dict[str, object]] = []
    for sample_id, _text in RUNTIME_RESPONSE_TEXTS:
        canonical_word_count = RUNTIME_RESPONSE_TEXT_WORD_COUNTS[sample_id]
        base_duration = canonical_word_count * 60.0 / native_wpm_at_rate_1
        durations = {
            str(rate): round(base_duration / (rate ** beta), 6)
            for rate in RUNTIME_RESPONSE_RATES
        }
        derived = derive_runtime_response_sample(durations)
        assert derived is not None
        pair_betas, text_beta = derived
        text_native_wpm = derive_native_wpm_at_rate_1(
            canonical_word_count,
            durations[str(1.0)],
        )
        assert text_native_wpm is not None
        samples.append({
            "sample_text_id": sample_id,
            "text_sha256": RUNTIME_RESPONSE_TEXT_SHA256[sample_id],
            "canonical_word_count": canonical_word_count,
            "durations_seconds": durations,
            "pair_beta_estimates": pair_betas,
            "text_beta": text_beta,
            "native_wpm_at_rate_1": text_native_wpm,
        })
    runtime_beta = derive_runtime_response_beta([float(sample["text_beta"]) for sample in samples])
    runtime_native_wpm = derive_runtime_native_wpm_at_rate_1(
        [float(sample["native_wpm_at_rate_1"]) for sample in samples]
    )
    assert runtime_beta is not None
    assert runtime_native_wpm is not None
    return {
        "flow_version": KOKORO_FLOW_VERSION,
        "voice_id": voice_id,
        "rates": list(RUNTIME_RESPONSE_RATES),
        "samples": samples,
        "speed_response_exponent": runtime_beta,
        "native_wpm_at_rate_1": runtime_native_wpm,
    }
