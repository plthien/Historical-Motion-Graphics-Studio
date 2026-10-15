from __future__ import annotations

import io
import math
import shutil
import wave

import pytest

from kokoro.runtime_response_calibration import KOKORO_FLOW_VERSION

from app.api.errors import AppError
from app.modules.v4.voice_audio import CANONICAL_SAMPLE_RATE, CANONICAL_SAMPLE_WIDTH, validate_canonical_wav_bytes
from app.modules.v4.voice_pacing import (
    VOICE_PACING_FINAL_WPM_TOLERANCE_RELATIVE,
    VOICE_PACING_QUALITY_FENCE_RELATIVE,
    VOICE_PROVIDER_DURATION_MODEL_INVERSE_SPEED_PLUS_FIXED,
    VOICE_PACING_SAMPLE_ROUNDING_EPSILON,
    _fit_pcm_to_sample_count,
    analyze_section_pacing,
    apply_section_pacing,
    apply_section_pacing_recommendation,
    build_voice_pacing_plan,
    canonical_hash,
    pace_section_outputs,
    pacing_directive_for_segment,
    raw_segment_synthesis_identity_sha256,
    raw_synthesis_identity_sha256,
    validate_voice_pacing_plan,
)
from app.modules.v4.elevenlabs_provider_adapter import ElevenLabsProviderAdapter
from app.modules.v4.voice_first_elevenlabs_adapter import VoiceFirstElevenLabsProviderAdapter
from app.modules.v4.voice_provider_adapters import KokoroProviderAdapter
from app.modules.v4.voice_provider_contracts import (
    VoiceProviderCapabilities,
    VoiceProviderPacingDirective,
    VoiceProviderPacingObservation,
    VoiceProviderPacingRecommendation,
    VoiceProviderPacingRequest,
    VoiceProviderRegistry,
)


def _wav(samples: int, *, tone_hz: float = 220.0) -> bytes:
    output = io.BytesIO()
    frames = bytearray()
    for index in range(samples):
        value = int(8000 * math.sin(2 * math.pi * tone_hz * index / CANONICAL_SAMPLE_RATE))
        frames.extend(int(value).to_bytes(2, byteorder="little", signed=True))
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(CANONICAL_SAMPLE_WIDTH)
        wav.setframerate(CANONICAL_SAMPLE_RATE)
        wav.writeframes(bytes(frames))
    return output.getvalue()


def _lineage_record(task_id: str, artifact_id: str, sample_count: int) -> dict[str, object]:
    return {
        "segment_task_id": task_id,
        "segment_audio_artifact_id": artifact_id,
        "raw_segment_synthesis_identity_sha256": f"test_raw_segment_identity_{task_id}",
        "sample_count": sample_count,
        "provider_timing": None,
    }


def _plan(control: str, bounds, *, wpm: int = 120, average: int = 120, words: int = 4):
    narration = " ".join(f"word{index}" for index in range(words))
    script = {"planned_average_wpm": average, "script_sections": [{"section_id": "intro", "planned_narration_wpm": wpm, "narration": narration}]}
    segmentation = {"segments": [{"segment_id": "seg_a", "segment_order": 1, "section_id": "intro"}]}

    def resolve(request: VoiceProviderPacingRequest) -> VoiceProviderPacingDirective:
        if control == "relative_multiplier":
            value = request.base_native_speaking_rate * request.target_narration_wpm / request.planned_average_wpm
        elif control == "native_wpm":
            value = float(request.target_narration_wpm)
        elif control == "none":
            value = None
        else:
            raise AssertionError(control)
        clamped = False
        if value is not None and bounds is not None:
            next_value = min(float(bounds[1]), max(float(bounds[0]), value))
            clamped = next_value != value
            value = next_value
        return VoiceProviderPacingDirective(
            control_model=control,
            value=round(value, 6) if value is not None else None,
            target_narration_wpm=request.target_narration_wpm,
            policy_version=f"test_{control}_v1",
            clamped=clamped,
            value_range=bounds,
        )

    plan = build_voice_pacing_plan(
        script=script,
        segmentation=segmentation,
        base_native_speaking_rate=1.0,
        pacing_directive_resolver=resolve,
        source_script_revision_id="artifact_script",
    )
    return plan, segmentation


@pytest.mark.parametrize(
    ("control", "bounds", "target", "expected"),
    [
        ("relative_multiplier", (0.7, 1.2), 180, 1.125),
        ("native_wpm", (80.0, 240.0), 180, 180.0),
        ("none", None, 180, None),
    ],
)
def test_provider_pacing_modes_have_distinct_boundary_semantics(control, bounds, target, expected) -> None:
    plan, segmentation = _plan(control, bounds, wpm=target, average=160)
    directive = pacing_directive_for_segment(plan, segmentation["segments"][0])
    assert directive.control_model == control
    assert directive.value == expected
    assert directive.target_narration_wpm == target
    assert directive.policy_version == f"test_{control}_v1"


def test_provider_policy_clamps_native_directive_values() -> None:
    elevenlabs = ElevenLabsProviderAdapter()
    very_fast = VoiceProviderPacingRequest(
        section_id="intro",
        target_narration_wpm=240,
        planned_average_wpm=120,
        base_native_speaking_rate=1.0,
    )
    directive = elevenlabs.pacing_directive(very_fast)
    assert directive.value == 1.2
    assert directive.clamped is True
    assert directive.policy_version == "elevenlabs_relative_script_average_v1"


def _elevenlabs_voice_first_observation(*, target_wpm: int, measured_wpm: float, current_rate: float) -> VoiceProviderPacingObservation:
    return VoiceProviderPacingObservation(
        section_id="sec_001",
        target_narration_wpm=target_wpm,
        measured_raw_wpm=measured_wpm,
        current_control_model="relative_multiplier",
        current_provider_pacing_value=current_rate,
        provider_value_range=(0.7, 1.2),
    )


def test_elevenlabs_feedback_rejects_no_progress_at_max_speed() -> None:
    recommendation = VoiceFirstElevenLabsProviderAdapter().recommend_pacing(
        _elevenlabs_voice_first_observation(target_wpm=180, measured_wpm=160.0, current_rate=1.2)
    )

    assert recommendation.status == "unsupported"
    assert recommendation.recommended_value is None
    assert recommendation.reason == "provider_pacing_limit_reached"


def test_elevenlabs_feedback_rejects_no_progress_at_min_speed() -> None:
    recommendation = VoiceFirstElevenLabsProviderAdapter().recommend_pacing(
        _elevenlabs_voice_first_observation(target_wpm=120, measured_wpm=140.0, current_rate=0.7)
    )

    assert recommendation.status == "unsupported"
    assert recommendation.recommended_value is None
    assert recommendation.reason == "provider_pacing_limit_reached"


def test_elevenlabs_feedback_supports_materially_different_recommendation_inside_range() -> None:
    recommendation = VoiceFirstElevenLabsProviderAdapter().recommend_pacing(
        _elevenlabs_voice_first_observation(target_wpm=150, measured_wpm=125.0, current_rate=1.0)
    )

    assert recommendation.status == "supported"
    assert recommendation.recommended_value == pytest.approx(1.2)
    assert recommendation.clamped is False


def test_elevenlabs_feedback_supports_clamped_recommendation_that_moves_rate() -> None:
    recommendation = VoiceFirstElevenLabsProviderAdapter().recommend_pacing(
        _elevenlabs_voice_first_observation(target_wpm=180, measured_wpm=140.0, current_rate=1.0)
    )

    assert recommendation.status == "supported"
    assert recommendation.recommended_value == pytest.approx(1.2)
    assert recommendation.clamped is True


def test_raw_synthesis_identity_changes_when_provider_pacing_policy_changes() -> None:
    plan, _ = _plan("relative_multiplier", (0.7, 1.2), wpm=180, average=160)
    settings = {"speaking_rate": 1.0, "sample_rate_hz": 48_000, "output_format": "wav_pcm_s16le"}
    provider_identity = {"provider": "test", "runtime": "stable"}
    original = raw_synthesis_identity_sha256(
        provider_identity=provider_identity,
        settings_snapshot=settings,
        pacing_plan=plan,
    )

    changed_value = {**plan, "sections": [dict(plan["sections"][0], provider_pacing_value=1.2)]}
    changed_policy = {
        **plan,
        "sections": [dict(plan["sections"][0], provider_pacing_policy_version="test_relative_multiplier_v2")],
    }
    changed_directive = raw_synthesis_identity_sha256(
        provider_identity=provider_identity,
        settings_snapshot=settings,
        pacing_plan=changed_value,
    )
    changed_policy_identity = raw_synthesis_identity_sha256(
        provider_identity=provider_identity,
        settings_snapshot=settings,
        pacing_plan=changed_policy,
    )
    assert changed_directive != original
    assert changed_policy_identity != original


def _kokoro_observation(
    *,
    words: int = 100,
    target_wpm: int = 166,
    current_rate: float = 0.757,
    measured_wpm: float = 153.0,
    fixed_duration: float = 8.0,
    bounds: tuple[float, float] = (0.5, 2.0),
) -> VoiceProviderPacingObservation:
    return VoiceProviderPacingObservation(
        section_id="sec_001",
        target_narration_wpm=target_wpm,
        measured_raw_wpm=measured_wpm,
        current_control_model="relative_multiplier",
        current_provider_pacing_value=current_rate,
        provider_value_range=bounds,
        actual_word_count=words,
        measured_raw_duration_seconds=60.0 * words / measured_wpm,
        target_duration_seconds=60.0 * words / target_wpm,
        fixed_duration_seconds=fixed_duration,
    )


def test_kokoro_feedback_zero_fixed_pause_matches_linear_wpm_ratio() -> None:
    adapter = KokoroProviderAdapter()
    observation = _kokoro_observation(fixed_duration=0.0)

    recommendation = adapter.recommend_pacing(observation)

    assert recommendation.status == "supported"
    assert recommendation.policy_version == KOKORO_FLOW_VERSION
    assert recommendation.recommended_value == pytest.approx(
        round(float(observation.current_provider_pacing_value) * observation.target_narration_wpm / observation.measured_raw_wpm, 6)
    )


def test_kokoro_feedback_fixed_pause_is_more_aggressive_for_slow_section() -> None:
    adapter = KokoroProviderAdapter()
    observation = _kokoro_observation()
    old_ratio = float(observation.current_provider_pacing_value) * observation.target_narration_wpm / observation.measured_raw_wpm
    expected = float(observation.current_provider_pacing_value) * (
        float(observation.measured_raw_duration_seconds) - observation.fixed_duration_seconds
    ) / (float(observation.target_duration_seconds) - observation.fixed_duration_seconds)

    recommendation = adapter.recommend_pacing(observation)

    assert recommendation.status == "supported"
    assert recommendation.recommended_value == pytest.approx(round(expected, 6))
    assert recommendation.recommended_value > old_ratio
    speech_material_at_rate_1 = float(observation.current_provider_pacing_value) * (
        float(observation.measured_raw_duration_seconds) - observation.fixed_duration_seconds
    )
    predicted_duration = speech_material_at_rate_1 / expected + observation.fixed_duration_seconds
    assert predicted_duration == pytest.approx(observation.target_duration_seconds)


def test_kokoro_feedback_reduces_speed_for_fast_section() -> None:
    adapter = KokoroProviderAdapter()
    observation = _kokoro_observation(target_wpm=120, measured_wpm=150, current_rate=1.0, fixed_duration=2.0)

    recommendation = adapter.recommend_pacing(observation)

    assert recommendation.status == "supported"
    assert recommendation.recommended_value is not None
    assert recommendation.recommended_value < 1.0


def test_kokoro_feedback_rejects_invalid_fixed_duration_model() -> None:
    adapter = KokoroProviderAdapter()
    observation = _kokoro_observation(words=10, target_wpm=120, measured_wpm=120, current_rate=1.0, fixed_duration=5.0)

    recommendation = adapter.recommend_pacing(observation)

    assert recommendation.status == "unsupported"
    assert recommendation.recommended_value is None
    assert recommendation.reason == "kokoro_duration_model_invalid"


def test_kokoro_feedback_reports_limit_when_exact_target_exhausts_pause_and_band_unreachable() -> None:
    adapter = KokoroProviderAdapter()
    observation = _observation_from_duration_model(
        words=100,
        target_wpm=220,
        current_rate=1.0,
        speech_at_rate_1=3.0,
        fixed_duration=30.0,
    )

    recommendation = adapter.recommend_pacing(observation)

    assert recommendation.status == "unsupported"
    assert recommendation.recommended_value is None
    assert recommendation.reason == "provider_pacing_limit_reached"


def test_kokoro_feedback_targets_pass_band_when_exact_target_exhausts_pause_but_band_reachable() -> None:
    adapter = KokoroProviderAdapter()
    observation = _observation_from_duration_model(
        words=100,
        target_wpm=200,
        current_rate=1.0,
        speech_at_rate_1=3.0,
        fixed_duration=30.0,
    )

    recommendation = adapter.recommend_pacing(observation)

    assert recommendation.status == "supported"
    assert recommendation.recommended_value is not None
    assert 0.5 <= recommendation.recommended_value <= 2.0
    predicted_duration = 3.0 / recommendation.recommended_value + 30.0
    predicted_wpm = 60.0 * 100 / predicted_duration
    assert abs(predicted_wpm / 200.0 - 1.0) <= VOICE_PACING_QUALITY_FENCE_RELATIVE + 1e-12


@pytest.mark.parametrize(
    "bad_value",
    [math.nan, math.inf],
)
def test_kokoro_feedback_rejects_nonfinite_values(bad_value: float) -> None:
    with pytest.raises(ValueError):
        VoiceProviderPacingObservation(
            section_id="sec_001",
            target_narration_wpm=120,
            measured_raw_wpm=120,
            current_control_model="relative_multiplier",
            current_provider_pacing_value=1.0,
            provider_value_range=(0.5, 2.0),
            actual_word_count=100,
            measured_raw_duration_seconds=bad_value,
            target_duration_seconds=50.0,
            fixed_duration_seconds=0.0,
        )


def _observation_from_duration_model(*, words: int, target_wpm: int, current_rate: float, speech_at_rate_1: float, fixed_duration: float) -> VoiceProviderPacingObservation:
    measured_duration = speech_at_rate_1 / current_rate + fixed_duration
    return VoiceProviderPacingObservation(
        section_id="sec_001",
        target_narration_wpm=target_wpm,
        measured_raw_wpm=60.0 * words / measured_duration,
        current_control_model="relative_multiplier",
        current_provider_pacing_value=current_rate,
        provider_value_range=(0.5, 2.0),
        actual_word_count=words,
        measured_raw_duration_seconds=measured_duration,
        target_duration_seconds=60.0 * words / target_wpm,
        fixed_duration_seconds=fixed_duration,
    )


def test_kokoro_feedback_supports_exact_target_inside_bounds() -> None:
    recommendation = KokoroProviderAdapter().recommend_pacing(
        _observation_from_duration_model(words=100, target_wpm=120, current_rate=1.0, speech_at_rate_1=60.0, fixed_duration=5.0)
    )

    assert recommendation.status == "supported"
    assert 0.5 <= float(recommendation.recommended_value) <= 2.0


def test_kokoro_feedback_supports_bound_when_exact_target_outside_but_pass_band_reachable() -> None:
    observation = _observation_from_duration_model(words=100, target_wpm=166, current_rate=1.0, speech_at_rate_1=58.0, fixed_duration=8.0)

    recommendation = KokoroProviderAdapter().recommend_pacing(observation)

    assert recommendation.status == "supported"
    assert recommendation.recommended_value == pytest.approx(2.0)
    assert recommendation.clamped is True


def test_kokoro_feedback_rejects_unreachable_too_slow_at_max() -> None:
    observation = _observation_from_duration_model(words=100, target_wpm=220, current_rate=2.0, speech_at_rate_1=120.0, fixed_duration=8.0)

    recommendation = KokoroProviderAdapter().recommend_pacing(observation)

    assert recommendation.status == "unsupported"
    assert recommendation.reason == "provider_pacing_limit_reached"


def test_kokoro_feedback_rejects_unreachable_too_fast_at_min() -> None:
    observation = _observation_from_duration_model(words=100, target_wpm=80, current_rate=0.5, speech_at_rate_1=10.0, fixed_duration=1.0)

    recommendation = KokoroProviderAdapter().recommend_pacing(observation)

    assert recommendation.status == "unsupported"
    assert recommendation.reason == "provider_pacing_limit_reached"


def test_kokoro_feedback_rejects_identical_max_no_progress() -> None:
    observation = _observation_from_duration_model(words=100, target_wpm=200, current_rate=2.0, speech_at_rate_1=50.0, fixed_duration=10.0)

    recommendation = KokoroProviderAdapter().recommend_pacing(observation)

    assert recommendation.status == "unsupported"
    assert recommendation.reason in {"provider_pacing_limit_reached", "provider_pacing_no_progress"}


def test_kokoro_feedback_rejects_identical_min_no_progress() -> None:
    observation = _observation_from_duration_model(words=100, target_wpm=90, current_rate=0.5, speech_at_rate_1=10.0, fixed_duration=1.0)

    recommendation = KokoroProviderAdapter().recommend_pacing(observation)

    assert recommendation.status == "unsupported"
    assert recommendation.reason in {"provider_pacing_limit_reached", "provider_pacing_no_progress"}


def test_kokoro_feedback_rejects_rounded_no_progress() -> None:
    observation = VoiceProviderPacingObservation(
        section_id="sec_001",
        target_narration_wpm=120,
        measured_raw_wpm=100,
        current_control_model="relative_multiplier",
        current_provider_pacing_value=1.0,
        provider_value_range=(0.5, 2.0),
        actual_word_count=100,
        measured_raw_duration_seconds=50.0,
        target_duration_seconds=50.0,
        fixed_duration_seconds=0.0,
    )

    recommendation = KokoroProviderAdapter().recommend_pacing(observation)

    assert recommendation.status == "unsupported"
    assert recommendation.reason == "provider_pacing_no_progress"


def test_kokoro_feedback_converges_in_one_step_for_exact_duration_model() -> None:
    words = 100
    target_wpm = 150
    speech_at_rate_1 = 52.0
    fixed_duration = 6.0
    observation = _observation_from_duration_model(
        words=words,
        target_wpm=target_wpm,
        current_rate=0.8,
        speech_at_rate_1=speech_at_rate_1,
        fixed_duration=fixed_duration,
    )
    recommendation = KokoroProviderAdapter().recommend_pacing(observation)
    assert recommendation.status == "supported"
    next_duration = speech_at_rate_1 / float(recommendation.recommended_value) + fixed_duration
    next_wpm = 60.0 * words / next_duration
    assert abs(next_wpm / target_wpm - 1.0) <= VOICE_PACING_QUALITY_FENCE_RELATIVE


def _observation_from_beta_duration_model(
    *,
    words: int,
    target_wpm: int,
    current_rate: float,
    speech_material: float,
    fixed_duration: float,
    beta: float,
) -> VoiceProviderPacingObservation:
    measured_duration = speech_material / (current_rate ** beta) + fixed_duration
    return VoiceProviderPacingObservation(
        section_id="sec_001",
        target_narration_wpm=target_wpm,
        measured_raw_wpm=60.0 * words / measured_duration,
        current_control_model="relative_multiplier",
        current_provider_pacing_value=current_rate,
        provider_value_range=(0.5, 2.0),
        actual_word_count=words,
        measured_raw_duration_seconds=measured_duration,
        target_duration_seconds=60.0 * words / target_wpm,
        fixed_duration_seconds=fixed_duration,
        speed_response_exponent=beta,
    )


@pytest.mark.parametrize("beta", [0.5, 1.4])
def test_kokoro_feedback_reverts_to_linear_pause_aware_correction_when_beta_is_present(beta: float) -> None:
    words = 100
    target_wpm = 150
    speech_material = 50.0
    fixed_duration = 4.0
    observation = _observation_from_beta_duration_model(
        words=words,
        target_wpm=target_wpm,
        current_rate=1.0,
        speech_material=speech_material,
        fixed_duration=fixed_duration,
        beta=beta,
    )
    speech_current = float(observation.measured_raw_duration_seconds) - fixed_duration
    speech_target = float(observation.target_duration_seconds) - fixed_duration
    expected = float(observation.current_provider_pacing_value) * speech_current / speech_target

    recommendation = KokoroProviderAdapter().recommend_pacing(observation)

    assert recommendation.status == "supported"
    assert recommendation.recommended_value == pytest.approx(round(expected, 6))
    assert recommendation.speed_response_exponent_used == pytest.approx(1.0)


def test_kokoro_feedback_unreachable_model_has_terminal_outcome() -> None:
    observation = _observation_from_duration_model(words=100, target_wpm=250, current_rate=2.0, speech_at_rate_1=100.0, fixed_duration=10.0)

    recommendation = KokoroProviderAdapter().recommend_pacing(observation)

    assert recommendation.status == "unsupported"
    assert recommendation.recommended_value is None


@pytest.mark.parametrize(
    ("provider_key", "initial_policy", "remediation_policy"),
    [
        ("kokoro_local", KOKORO_FLOW_VERSION, KOKORO_FLOW_VERSION),
        ("elevenlabs", "elevenlabs_relative_script_average_v1", "elevenlabs_voice_first_section_feedback_v1"),
    ],
)
def test_raw_segment_identity_is_section_local_when_operation_policy_composition_changes(
    provider_key: str,
    initial_policy: str,
    remediation_policy: str,
) -> None:
    script = {
        "planned_average_wpm": 150,
        "script_sections": [
            {"section_id": "sec_a", "planned_narration_wpm": 150, "narration": "alpha beta gamma"},
            {"section_id": "sec_b", "planned_narration_wpm": 150, "narration": "delta epsilon zeta"},
            {"section_id": "sec_c", "planned_narration_wpm": 150, "narration": "eta theta iota"},
        ],
    }
    segmentation = {
        "segments": [
            {
                "segment_id": f"seg_{section_id[-1]}",
                "segment_order": index,
                "section_id": section_id,
                "source_start": index * 10,
                "source_end": index * 10 + 8,
                "exact_text": f"section {section_id}",
                "text_sha256": canonical_hash(f"section {section_id}"),
                "voice_first": {"provider_rendering": "test_rendering_v1"},
            }
            for index, section_id in enumerate(["sec_a", "sec_b", "sec_c"], 1)
        ]
    }

    plan = build_voice_pacing_plan(
        script=script,
        segmentation=segmentation,
        base_native_speaking_rate=1.0,
        pacing_directive_resolver=lambda request: VoiceProviderPacingDirective(
            control_model="relative_multiplier",
            value=1.0,
            target_narration_wpm=request.target_narration_wpm,
            policy_version=initial_policy,
            value_range=(0.5, 2.0),
        ),
        source_script_revision_id="script_identity",
    )
    provider_identity = {"provider": provider_key, "runtime": "stable"}
    base_settings = {
        "generation_provider": provider_key,
        "voice_id": "voice_identity",
        "language": "en",
        "sample_rate_hz": 48_000,
        "output_format": "wav_pcm_s16le",
        "voice_pacing_policy_identity_sha256": canonical_hash({"policy_versions": [initial_policy]}),
        "pacing_identity_sha256": plan["pacing_identity_sha256"],
        "raw_synthesis_identity_sha256": "r" * 64,
    }

    before = {
        segment["segment_id"]: raw_segment_synthesis_identity_sha256(
            provider_identity=provider_identity,
            settings_snapshot=base_settings,
            pacing_plan=plan,
            segment=segment,
            synthesis_context_sha256=None,
        )
        for segment in segmentation["segments"]
    }

    remediated = apply_section_pacing_recommendation(
        plan,
        section_id="sec_b",
        recommended_provider_pacing_value=0.91,
        recommendation_policy_version=remediation_policy,
    )
    assert remediated["sections"][1]["provider_pacing_policy_version"] == remediation_policy
    if provider_key == "kokoro_local":
        assert remediation_policy == initial_policy == KOKORO_FLOW_VERSION
    else:
        assert remediated["sections"][1]["provider_pacing_policy_version"] != initial_policy
    remediated_settings = {
        **base_settings,
        "voice_pacing_policy_identity_sha256": canonical_hash({"policy_versions": [initial_policy, remediation_policy]}),
        "pacing_identity_sha256": remediated["pacing_identity_sha256"],
        "raw_synthesis_identity_sha256": "s" * 64,
    }
    after = {
        segment["segment_id"]: raw_segment_synthesis_identity_sha256(
            provider_identity=provider_identity,
            settings_snapshot=remediated_settings,
            pacing_plan=remediated,
            segment=segment,
            synthesis_context_sha256=None,
        )
        for segment in segmentation["segments"]
    }

    assert after["seg_a"] == before["seg_a"]
    assert after["seg_b"] != before["seg_b"]
    assert after["seg_c"] == before["seg_c"]


def test_pacing_identity_is_recomputed_and_bound_to_script_revision() -> None:
    plan, segmentation = _plan("relative_multiplier", (0.7, 1.2))
    assert validate_voice_pacing_plan(plan, expected_script_revision_id="artifact_script", segmentation=segmentation) == plan["pacing_identity_sha256"]
    plan["sections"][0]["planned_narration_wpm"] = 121
    with pytest.raises(AppError) as exc_info:
        validate_voice_pacing_plan(plan, expected_script_revision_id="artifact_script", segmentation=segmentation)
    assert exc_info.value.error_code == "VOICE_PACING_PLAN_INVALID"


def test_sample_fit_only_allows_tiny_rounding_epsilon() -> None:
    frame = b"\x01\x00"
    source = frame * 100
    assert len(_fit_pcm_to_sample_count(source, 100 + VOICE_PACING_SAMPLE_ROUNDING_EPSILON)) == (100 + VOICE_PACING_SAMPLE_ROUNDING_EPSILON) * 2
    with pytest.raises(AppError) as exc_info:
        _fit_pcm_to_sample_count(source, 100 + VOICE_PACING_SAMPLE_ROUNDING_EPSILON + 1)
    assert exc_info.value.error_code == "VOICE_PACING_SAMPLE_ADJUSTMENT_OUT_OF_RANGE"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="real FFmpeg is unavailable")
@pytest.mark.parametrize(("raw_seconds", "target_wpm"), [(2.08, 120), (1.92, 120), (2.0, 120)])
def test_real_ffmpeg_pacing_uses_actual_output_duration(raw_seconds: float, target_wpm: int) -> None:
    # Four words at 120 WPM target exactly 2 seconds. Fixtures exercise slow-down,
    # speed-up and no-op through the real configured FFmpeg path.
    plan, segmentation = _plan("none", None, wpm=target_wpm, average=target_wpm, words=4)
    raw_samples = round(raw_seconds * CANONICAL_SAMPLE_RATE)
    record = _lineage_record("task_a", "raw_a", raw_samples)
    paced = pace_section_outputs(segment_bytes=[_wav(raw_samples)], segment_records=[record], segmentation=segmentation, pacing_plan=plan)
    info = validate_canonical_wav_bytes(paced.segment_bytes[0])
    summary = paced.pacing_summary["sections"][0]
    actual_wpm = 4 * 60.0 / info.duration_seconds
    assert info.sample_rate_hz == 48_000 and info.channels == 1 and info.sample_width_bytes == 2
    assert info.sample_count == summary["final_sample_count"]
    assert abs(actual_wpm - target_wpm) / target_wpm <= VOICE_PACING_FINAL_WPM_TOLERANCE_RELATIVE
    assert summary["final_duration_seconds"] == pytest.approx(info.duration_seconds, abs=1 / CANONICAL_SAMPLE_RATE)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="real FFmpeg is unavailable")
def test_real_ffmpeg_multi_segment_section_uses_one_factor_and_actual_samples() -> None:
    narration = "one two three four"
    script = {"planned_average_wpm": 120, "script_sections": [{"section_id": "intro", "planned_narration_wpm": 120, "narration": narration}]}
    segmentation = {"segments": [
        {"segment_id": "seg_a", "segment_order": 1, "section_id": "intro"},
        {"segment_id": "seg_b", "segment_order": 2, "section_id": "intro"},
    ]}
    plan = build_voice_pacing_plan(
        script=script,
        segmentation=segmentation,
        base_native_speaking_rate=1.0,
        pacing_directive_resolver=lambda request: VoiceProviderPacingDirective(
            control_model="none",
            value=None,
            target_narration_wpm=request.target_narration_wpm,
            policy_version="test_none_v1",
        ),
        source_script_revision_id="artifact_script",
    )
    raw = [_wav(round(1.05 * CANONICAL_SAMPLE_RATE), tone_hz=220), _wav(round(1.05 * CANONICAL_SAMPLE_RATE), tone_hz=330)]
    records = [
        _lineage_record("task_a", "raw_a", round(1.05 * CANONICAL_SAMPLE_RATE)),
        _lineage_record("task_b", "raw_b", round(1.05 * CANONICAL_SAMPLE_RATE)),
    ]
    paced = pace_section_outputs(segment_bytes=raw, segment_records=records, segmentation=segmentation, pacing_plan=plan)
    counts = [validate_canonical_wav_bytes(item).sample_count for item in paced.segment_bytes]
    assert sum(counts) == paced.full_info.sample_count
    assert abs(paced.pacing_summary["sections"][0]["final_wpm"] - 120) / 120 <= VOICE_PACING_FINAL_WPM_TOLERANCE_RELATIVE


def test_successful_multi_section_pacing_records_use_owning_script_section_metadata() -> None:
    script = {
        "planned_average_wpm": 132,
        "script_sections": [
            {"section_id": "sec_001", "planned_narration_wpm": 120, "narration": "one two three four"},
            {"section_id": "sec_002", "planned_narration_wpm": 180, "narration": "five six seven eight nine ten"},
        ],
    }
    segmentation = {
        "segments": [
            {"segment_id": "seg_001", "segment_order": 1, "section_id": "sec_001"},
            {"segment_id": "seg_002", "segment_order": 2, "section_id": "sec_001"},
            {"segment_id": "seg_003", "segment_order": 3, "section_id": "sec_002"},
        ]
    }

    def resolve(request: VoiceProviderPacingRequest) -> VoiceProviderPacingDirective:
        if request.section_id == "sec_001":
            return VoiceProviderPacingDirective(
                control_model="relative_multiplier",
                value=0.75,
                target_narration_wpm=request.target_narration_wpm,
                policy_version="section_one_policy_v1",
                clamped=False,
                value_range=(0.5, 2.0),
            )
        if request.section_id == "sec_002":
            return VoiceProviderPacingDirective(
                control_model="native_wpm",
                value=180.0,
                target_narration_wpm=request.target_narration_wpm,
                policy_version="section_two_policy_v1",
                clamped=True,
                value_range=(80.0, 240.0),
            )
        raise AssertionError(request.section_id)

    plan = build_voice_pacing_plan(
        script=script,
        segmentation=segmentation,
        base_native_speaking_rate=1.0,
        pacing_directive_resolver=resolve,
        source_script_revision_id="artifact_script",
    )
    segment_records = [
        _lineage_record("task_001", "raw_001", CANONICAL_SAMPLE_RATE),
        _lineage_record("task_002", "raw_002", CANONICAL_SAMPLE_RATE),
        _lineage_record("task_003", "raw_003", 2 * CANONICAL_SAMPLE_RATE),
    ]
    analysis = analyze_section_pacing(
        segment_bytes=[
            _wav(CANONICAL_SAMPLE_RATE),
            _wav(CANONICAL_SAMPLE_RATE),
            _wav(2 * CANONICAL_SAMPLE_RATE),
        ],
        segment_records=segment_records,
        segmentation=segmentation,
        pacing_plan=plan,
    )

    assert analysis.report["status"] == "pass"
    paced = apply_section_pacing(
        analysis=analysis,
        segment_records=segment_records,
        segmentation=segmentation,
        pacing_plan=plan,
    )

    records_by_id = {record["segment_task_id"]: record for record in paced.segment_records}
    for task_id in ("task_001", "task_002"):
        assert records_by_id[task_id]["provider_pacing_value"] == 0.75
        assert records_by_id[task_id]["provider_pacing_control"] == "relative_multiplier"
        assert records_by_id[task_id]["provider_pacing_policy_version"] == "section_one_policy_v1"
        assert records_by_id[task_id]["provider_pacing_clamped"] is False
        assert records_by_id[task_id]["section_target_duration_seconds"] == pytest.approx(2.0)
        assert records_by_id[task_id]["pacing_identity_sha256"] == plan["pacing_identity_sha256"]
    assert records_by_id["task_003"]["provider_pacing_value"] == 180.0
    assert records_by_id["task_003"]["provider_pacing_control"] == "native_wpm"
    assert records_by_id["task_003"]["provider_pacing_policy_version"] == "section_two_policy_v1"
    assert records_by_id["task_003"]["provider_pacing_clamped"] is True
    assert records_by_id["task_003"]["section_target_duration_seconds"] == pytest.approx(2.0)
    assert records_by_id["task_003"]["pacing_identity_sha256"] == plan["pacing_identity_sha256"]
    assert paced.pacing_summary["pacing_identity_sha256"] == plan["pacing_identity_sha256"]


def test_quality_fence_boundary_and_overflow() -> None:
    plan, segmentation = _plan("none", None, wpm=120, average=120, words=4)
    target_samples = 2 * CANONICAL_SAMPLE_RATE
    # WPM is inverse to duration. Construct sample counts from explicit WPM
    # deviations so the boundary test measures the product metric, not tempo factor.
    within = round(target_samples / (1 + VOICE_PACING_QUALITY_FENCE_RELATIVE - 0.001))
    if shutil.which("ffmpeg"):
        pace_section_outputs(segment_bytes=[_wav(within)], segment_records=[_lineage_record("t", "a", within)], segmentation=segmentation, pacing_plan=plan)
    over = round(target_samples / (1 + VOICE_PACING_QUALITY_FENCE_RELATIVE + 0.01))
    with pytest.raises(AppError) as exc_info:
        pace_section_outputs(segment_bytes=[_wav(over)], segment_records=[_lineage_record("t", "a", over)], segmentation=segmentation, pacing_plan=plan)
    assert exc_info.value.error_code == "VOICE_PACING_REGENERATION_REQUIRED"
    assert exc_info.value.details["sections"][0]["issue_code"] == "VOICE_PACING_WPM_OUT_OF_RANGE"

def test_pacing_progress_callback_runs_before_deterministic_quality_failure() -> None:
    plan, segmentation = _plan("none", None, wpm=120, average=120, words=4)
    pulses: list[str] = []
    raw_samples = round(2.4 * CANONICAL_SAMPLE_RATE)
    with pytest.raises(AppError) as exc_info:
        pace_section_outputs(
            segment_bytes=[_wav(raw_samples)],
            segment_records=[_lineage_record("t", "a", raw_samples)],
            segmentation=segmentation,
            pacing_plan=plan,
            progress_callback=lambda: pulses.append("pulse"),
        )
    assert exc_info.value.error_code == "VOICE_PACING_REGENERATION_REQUIRED"
    assert pulses == ["pulse"]


def test_analyze_section_pacing_infers_native_wpm_with_effective_feedback_beta() -> None:
    words = 10
    narration = " ".join(f"word{index}" for index in range(words))
    script = {
        "planned_average_wpm": 150,
        "script_sections": [{"section_id": "intro", "planned_narration_wpm": 150, "narration": narration}],
    }
    segmentation = {"segments": [{"segment_id": "seg_a", "segment_order": 1, "section_id": "intro"}]}
    plan = build_voice_pacing_plan(
        script=script,
        segmentation=segmentation,
        base_native_speaking_rate=1.0,
        pacing_directive_resolver=lambda request: VoiceProviderPacingDirective(
            control_model="relative_multiplier",
            value=1.44,
            target_narration_wpm=request.target_narration_wpm,
            policy_version="test_kokoro_beta_v1",
            value_range=(0.5, 2.0),
            speed_response_exponent=1.0,
        ),
        source_script_revision_id="artifact_script",
        provider_duration_models_by_section={
            "intro": {
                "provider_duration_model": VOICE_PROVIDER_DURATION_MODEL_INVERSE_SPEED_PLUS_FIXED,
                "provider_fixed_duration_seconds": 0.5,
            }
        },
    )
    raw_seconds = 5.0
    raw_samples = round(raw_seconds * CANONICAL_SAMPLE_RATE)
    record = _lineage_record("task_beta", "raw_beta", raw_samples)
    record["raw_segment_material_identity_sha256"] = "m" * 64

    analysis = analyze_section_pacing(
        segment_bytes=[_wav(raw_samples)],
        segment_records=[record],
        segmentation=segmentation,
        pacing_plan=plan,
        recommendation_resolver=lambda observation: VoiceProviderPacingRecommendation(
            status="supported",
            control_model="relative_multiplier",
            recommended_value=1.6,
            policy_version=KOKORO_FLOW_VERSION,
            value_range=(0.5, 2.0),
            speed_response_exponent_used=0.5,
            response_model_source="exact_material_learned",
            response_model_identity="r" * 64,
        ),
    )

    section = analysis.report["sections"][0]
    expected_native_wpm = 60.0 * words / ((raw_seconds - 0.5) * (1.44 ** 0.5))
    assert section["speed_response_exponent"] == pytest.approx(1.0)
    assert section["speed_response_exponent_used"] == pytest.approx(0.5)
    assert section["inferred_native_wpm_at_rate_1"] == pytest.approx(expected_native_wpm)


def test_analyze_section_pacing_reports_all_bad_sections_in_order() -> None:
    script = {
        "planned_average_wpm": 120,
        "script_sections": [
            {"section_id": "sec_001", "planned_narration_wpm": 120, "narration": "one two three four"},
            {"section_id": "sec_002", "planned_narration_wpm": 120, "narration": "five six seven eight"},
        ],
    }
    segmentation = {
        "segments": [
            {"segment_id": "seg_001", "segment_order": 1, "section_id": "sec_001"},
            {"segment_id": "seg_002", "segment_order": 2, "section_id": "sec_002"},
        ]
    }
    plan = build_voice_pacing_plan(
        script=script,
        segmentation=segmentation,
        base_native_speaking_rate=1.0,
        pacing_directive_resolver=lambda request: VoiceProviderPacingDirective(
            control_model="none",
            value=None,
            target_narration_wpm=request.target_narration_wpm,
            policy_version="test_none_v1",
        ),
        source_script_revision_id="artifact_script",
    )
    analysis = analyze_section_pacing(
        segment_bytes=[_wav(round(2.4 * CANONICAL_SAMPLE_RATE)), _wav(round(1.5 * CANONICAL_SAMPLE_RATE))],
        segment_records=[
            _lineage_record("task_001", "raw_001", round(2.4 * CANONICAL_SAMPLE_RATE)),
            _lineage_record("task_002", "raw_002", round(1.5 * CANONICAL_SAMPLE_RATE)),
        ],
        segmentation=segmentation,
        pacing_plan=plan,
    )
    assert analysis.report["status"] == "regeneration_required"
    assert [item["section_id"] for item in analysis.report["sections"] if item["status"] == "regeneration_required"] == ["sec_001", "sec_002"]


class _DirectiveOnlyAdapter:
    provider_key = "directive_test"

    def __init__(self, directive: VoiceProviderPacingDirective) -> None:
        self.directive = directive

    def capabilities(self) -> VoiceProviderCapabilities:
        return VoiceProviderCapabilities(
            execution_model="synchronous",
            pacing_control="relative_multiplier",
            pacing_value_range=(0.5, 2.0),
        )

    def pacing_directive(self, request: VoiceProviderPacingRequest) -> VoiceProviderPacingDirective:
        _ = request
        return self.directive


def _assert_registry_rejects_directive(directive: VoiceProviderPacingDirective) -> None:
    registry = VoiceProviderRegistry()
    registry.register(_DirectiveOnlyAdapter(directive))
    request = VoiceProviderPacingRequest(
        section_id="intro",
        target_narration_wpm=180,
        planned_average_wpm=160,
        base_native_speaking_rate=1.0,
    )
    with pytest.raises(AppError) as exc_info:
        registry.pacing_directive("directive_test", request)
    assert exc_info.value.error_code == "VOICE_PROVIDER_PACING_INVALID"


def test_provider_registry_rejects_changed_target_wpm() -> None:
    _assert_registry_rejects_directive(
        VoiceProviderPacingDirective(
            control_model="relative_multiplier",
            value=1.1,
            target_narration_wpm=181,
            policy_version="bad_target_v1",
            value_range=(0.5, 2.0),
        )
    )


def test_provider_registry_rejects_capability_range_mismatch() -> None:
    _assert_registry_rejects_directive(
        VoiceProviderPacingDirective(
            control_model="relative_multiplier",
            value=1.1,
            target_narration_wpm=180,
            policy_version="bad_range_v1",
            value_range=(0.7, 1.2),
        )
    )


def test_provider_registry_rejects_value_outside_declared_range() -> None:
    _assert_registry_rejects_directive(
        VoiceProviderPacingDirective(
            control_model="relative_multiplier",
            value=2.2,
            target_narration_wpm=180,
            policy_version="bad_value_v1",
            value_range=(0.5, 2.0),
        )
    )
