from __future__ import annotations

from copy import deepcopy
import hashlib
import math
from statistics import median
from typing import Any, Mapping, Sequence

KOKORO_FLOW_VERSION = "kokoro_flow_v8"
RUNTIME_RESPONSE_RATES = (0.75, 1.0, 1.25, 1.5)
RUNTIME_RESPONSE_TEXTS = (
    ("expository", "Ancient communities learned to shape stone, preserve food, and pass practical knowledge from one generation to the next."),
    ("question", "Why did people gather after sunset, tell stories beside the fire, and turn ordinary evenings into shared rituals?"),
    ("narrative", "At sunrise, a small group crossed the valley, followed fresh tracks, and returned before dark with enough food for everyone."),
)
RUNTIME_RESPONSE_TEXT_SHA256 = {
    sample_id: hashlib.sha256(text.encode("utf-8")).hexdigest()
    for sample_id, text in RUNTIME_RESPONSE_TEXTS
}
RUNTIME_RESPONSE_TEXT_WORD_COUNTS = {
    "expository": 18,
    "question": 18,
    "narrative": 20,
}
MIN_NATIVE_WPM_AT_RATE_1 = 60.0
MAX_NATIVE_WPM_AT_RATE_1 = 300.0
RUNTIME_RESPONSE_NUMERIC_TOLERANCE = 1e-5


def derive_runtime_response_sample(
    durations_seconds: Mapping[str, object],
) -> tuple[list[float], float] | None:
    expected_keys = {str(rate) for rate in RUNTIME_RESPONSE_RATES}
    if set(durations_seconds) != expected_keys:
        return None
    durations: dict[float, float] = {}
    for rate in RUNTIME_RESPONSE_RATES:
        value = durations_seconds.get(str(rate))
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
            or float(value) <= 0
        ):
            return None
        durations[rate] = float(value)

    raw_pair_betas = [
        math.log(durations[v1] / durations[v2]) / math.log(v2 / v1)
        for index, v1 in enumerate(RUNTIME_RESPONSE_RATES)
        for v2 in RUNTIME_RESPONSE_RATES[index + 1 :]
    ]
    if any(not math.isfinite(beta) or beta <= 0 for beta in raw_pair_betas):
        return None
    return (
        [round(beta, 6) for beta in raw_pair_betas],
        round(float(median(raw_pair_betas)), 6),
    )


def derive_runtime_response_beta(text_betas: Sequence[float]) -> float | None:
    if len(text_betas) != len(RUNTIME_RESPONSE_TEXTS):
        return None
    values = [float(value) for value in text_betas]
    if any(not math.isfinite(value) or value <= 0 for value in values):
        return None
    return round(float(median(values)), 6)


def derive_native_wpm_at_rate_1(
    canonical_word_count: int,
    duration_seconds_at_rate_1: float,
) -> float | None:
    if (
        not isinstance(canonical_word_count, int)
        or isinstance(canonical_word_count, bool)
        or canonical_word_count <= 0
        or not isinstance(duration_seconds_at_rate_1, (int, float))
        or isinstance(duration_seconds_at_rate_1, bool)
        or not math.isfinite(float(duration_seconds_at_rate_1))
        or float(duration_seconds_at_rate_1) <= 0
    ):
        return None
    native_wpm = canonical_word_count * 60.0 / float(duration_seconds_at_rate_1)
    return round(native_wpm, 6) if math.isfinite(native_wpm) and native_wpm > 0 else None


def derive_runtime_native_wpm_at_rate_1(text_native_wpms: Sequence[float]) -> float | None:
    if len(text_native_wpms) != len(RUNTIME_RESPONSE_TEXTS):
        return None
    values = [float(value) for value in text_native_wpms]
    if any(not math.isfinite(value) or value <= 0 for value in values):
        return None
    runtime_native_wpm = float(median(values))
    if not MIN_NATIVE_WPM_AT_RATE_1 <= runtime_native_wpm <= MAX_NATIVE_WPM_AT_RATE_1:
        return None
    return round(runtime_native_wpm, 6)


def validate_runtime_response_calibration(calibration: Any) -> dict[str, Any] | None:
    if not isinstance(calibration, dict):
        return None
    exponent = calibration.get("speed_response_exponent")
    native_wpm = calibration.get("native_wpm_at_rate_1")
    samples = calibration.get("samples")
    expected_rates = list(RUNTIME_RESPONSE_RATES)
    expected_sample_ids = set(RUNTIME_RESPONSE_TEXT_SHA256)
    if (
        calibration.get("flow_version") != KOKORO_FLOW_VERSION
        or calibration.get("rates") != expected_rates
        or not isinstance(calibration.get("voice_id"), str)
        or not calibration["voice_id"].strip()
        or not isinstance(exponent, (int, float))
        or isinstance(exponent, bool)
        or not math.isfinite(float(exponent))
        or not isinstance(native_wpm, (int, float))
        or isinstance(native_wpm, bool)
        or not math.isfinite(float(native_wpm))
        or not isinstance(samples, list)
        or len(samples) != len(expected_sample_ids)
    ):
        return None

    expected_text_betas: list[float] = []
    expected_text_native_wpms: list[float] = []
    sample_ids: set[str] = set()
    for sample in samples:
        if not isinstance(sample, dict):
            return None
        sample_id = sample.get("sample_text_id")
        pair_betas = sample.get("pair_beta_estimates")
        text_beta = sample.get("text_beta")
        sample_native_wpm = sample.get("native_wpm_at_rate_1")
        if (
            not isinstance(sample_id, str)
            or sample_id not in expected_sample_ids
            or sample_id in sample_ids
            or sample.get("text_sha256") != RUNTIME_RESPONSE_TEXT_SHA256[sample_id]
            or sample.get("canonical_word_count") != RUNTIME_RESPONSE_TEXT_WORD_COUNTS[sample_id]
            or not isinstance(sample.get("durations_seconds"), dict)
            or not isinstance(pair_betas, list)
            or not isinstance(text_beta, (int, float))
            or isinstance(text_beta, bool)
            or not isinstance(sample_native_wpm, (int, float))
            or isinstance(sample_native_wpm, bool)
        ):
            return None
        derived = derive_runtime_response_sample(sample["durations_seconds"])
        if derived is None:
            return None
        expected_pair_betas, expected_text_beta = derived
        expected_sample_native_wpm = derive_native_wpm_at_rate_1(
            RUNTIME_RESPONSE_TEXT_WORD_COUNTS[sample_id],
            sample["durations_seconds"].get(str(1.0)),
        )
        if (
            expected_sample_native_wpm is None
            or len(pair_betas) != len(expected_pair_betas)
            or any(
                not isinstance(actual, (int, float))
                or isinstance(actual, bool)
                or not math.isfinite(float(actual))
                or abs(float(actual) - expected) > RUNTIME_RESPONSE_NUMERIC_TOLERANCE
                for actual, expected in zip(pair_betas, expected_pair_betas, strict=True)
            )
            or not math.isfinite(float(text_beta))
            or abs(float(text_beta) - expected_text_beta) > RUNTIME_RESPONSE_NUMERIC_TOLERANCE
            or not math.isfinite(float(sample_native_wpm))
            or abs(float(sample_native_wpm) - expected_sample_native_wpm) > RUNTIME_RESPONSE_NUMERIC_TOLERANCE
        ):
            return None
        sample_ids.add(sample_id)
        expected_text_betas.append(expected_text_beta)
        expected_text_native_wpms.append(expected_sample_native_wpm)

    expected_runtime_beta = derive_runtime_response_beta(expected_text_betas)
    expected_runtime_native_wpm = derive_runtime_native_wpm_at_rate_1(expected_text_native_wpms)
    if (
        sample_ids != expected_sample_ids
        or expected_runtime_beta is None
        or expected_runtime_native_wpm is None
        or abs(float(exponent) - expected_runtime_beta) > RUNTIME_RESPONSE_NUMERIC_TOLERANCE
        or abs(float(native_wpm) - expected_runtime_native_wpm) > RUNTIME_RESPONSE_NUMERIC_TOLERANCE
    ):
        return None
    return deepcopy(calibration)
