from __future__ import annotations

import pytest

from kokoro.runtime_response_calibration import KOKORO_FLOW_VERSION

from app.modules.v4.voice_pacing_calibration import VoicePacingCalibration
from app.modules.v4.voice_provider_adapters import KokoroProviderAdapter
from app.modules.v4.voice_provider_contracts import VoiceProviderPacingRequest
from app.modules.v4.voice_provider_contracts import VoiceProviderPacingObservation
from app.modules.v4.voice_provider_registry import CalibratedVoiceProviderRegistry


def _registry() -> CalibratedVoiceProviderRegistry:
    registry = CalibratedVoiceProviderRegistry()
    registry.register(KokoroProviderAdapter())
    return registry


def test_kokoro_adapter_normalization_constant_is_not_runtime_calibration() -> None:
    registry = _registry()

    directive = registry.pacing_directive(
        "kokoro_local",
        VoiceProviderPacingRequest(
            section_id="sec_001",
            target_narration_wpm=120,
            planned_average_wpm=160,
            base_native_speaking_rate=1.0,
        ),
    )

    assert directive.value == pytest.approx(0.5)
    assert directive.policy_version == KOKORO_FLOW_VERSION


def test_kokoro_nominal_initial_speed_with_fixed_pause_uses_duration_model() -> None:
    registry = _registry()

    directive = registry.pacing_directive(
        "kokoro_local",
        VoiceProviderPacingRequest(
            section_id="sec_001",
            target_narration_wpm=120,
            planned_average_wpm=160,
            base_native_speaking_rate=1.0,
            actual_word_count=100,
            target_duration_seconds=50.0,
            fixed_duration_seconds=10.0,
        ),
    )

    assert directive.value == pytest.approx(0.576923)


def test_kokoro_runtime_default_beta_controls_initial_section_speed() -> None:
    registry = _registry()
    registry._pacing_calibration.set(
        VoicePacingCalibration(
            provider_key="kokoro_local",
            voice_id="af_heart",
            model_id="kokoro_local",
            native_wpm_at_rate_1=160.0,
            metadata={
                "source": "kokoro_ready_marker_calibration",
                "speed_response_exponent": 0.5,
                "speed_response_model_source": "runtime_calibrated_default",
                "speed_response_model_identity": "r" * 64,
            },
        )
    )

    directive = registry.pacing_directive(
        "kokoro_local",
        VoiceProviderPacingRequest(
            section_id="sec_001",
            target_narration_wpm=150,
            planned_average_wpm=160,
            base_native_speaking_rate=1.0,
            actual_word_count=100,
            target_duration_seconds=40.0,
            fixed_duration_seconds=5.0,
        ),
    )

    assert directive.value == pytest.approx((37.5 / 35.0) ** 2)
    assert directive.speed_response_exponent == pytest.approx(0.5)
    assert directive.response_model_source == "runtime_calibrated_default"


def test_kokoro_initial_speed_clamps_when_duration_model_exceeds_bounds() -> None:
    registry = _registry()

    directive = registry.pacing_directive(
        "kokoro_local",
        VoiceProviderPacingRequest(
            section_id="sec_001",
            target_narration_wpm=240,
            planned_average_wpm=160,
            base_native_speaking_rate=1.0,
            actual_word_count=100,
            target_duration_seconds=25.0,
            fixed_duration_seconds=20.0,
        ),
    )

    assert directive.value == pytest.approx(2.0)
    assert directive.clamped is True


def test_kokoro_initial_speed_uses_max_rate_when_fixed_pause_exhausts_target_duration() -> None:
    registry = _registry()

    directive = registry.pacing_directive(
        "kokoro_local",
        VoiceProviderPacingRequest(
            section_id="sec_001",
            target_narration_wpm=240,
            planned_average_wpm=160,
            base_native_speaking_rate=1.0,
            actual_word_count=100,
            target_duration_seconds=25.0,
            fixed_duration_seconds=30.0,
        ),
    )

    assert directive.value == pytest.approx(2.0)
    assert directive.clamped is True
    assert directive.policy_version == KOKORO_FLOW_VERSION


def test_kokoro_first_probe_feedback_uses_linear_pause_aware_correction() -> None:
    adapter = KokoroProviderAdapter()
    words = 100
    target_wpm = 150
    fixed_duration = 4.0
    speech_material = 50.0
    current_rate = 1.0
    measured_duration = speech_material / (current_rate ** 0.5) + fixed_duration
    target_duration = 60.0 * words / target_wpm
    expected = current_rate * (measured_duration - fixed_duration) / (target_duration - fixed_duration)

    recommendation = adapter.recommend_pacing(
        VoiceProviderPacingObservation(
            section_id="sec_001",
            target_narration_wpm=target_wpm,
            measured_raw_wpm=60.0 * words / measured_duration,
            current_control_model="relative_multiplier",
            current_provider_pacing_value=current_rate,
            provider_value_range=(0.5, 2.0),
            actual_word_count=words,
            measured_raw_duration_seconds=measured_duration,
            target_duration_seconds=target_duration,
            fixed_duration_seconds=fixed_duration,
            speed_response_exponent=0.5,
        ),
    )

    assert recommendation.status == "supported"
    assert recommendation.recommended_value == pytest.approx(round(expected, 6))
    assert recommendation.speed_response_exponent_used == pytest.approx(1.0)
    assert recommendation.response_model_source is None
