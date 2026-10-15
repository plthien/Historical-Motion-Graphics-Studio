from __future__ import annotations

import io
import wave

import pytest

from app.modules.v4.voice_audio import CANONICAL_SAMPLE_RATE
from app.modules.v4.voice_pacing import (
    VOICE_PACING_ATEMPO_SAFETY_FENCE_RELATIVE,
    VOICE_PACING_QUALITY_FENCE_RELATIVE,
    analyze_section_pacing,
    build_voice_pacing_plan,
)
from app.modules.v4.voice_provider_contracts import VoiceProviderPacingDirective
from app.modules.v4.voice_segmentation import segment_narration


def _wav(sample_count: int) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(CANONICAL_SAMPLE_RATE)
        handle.writeframes(b"\x00\x00" * sample_count)
    return output.getvalue()


def _analysis(*, raw_to_target_wpm_ratio: float, provider_rate: float = 1.0):
    narration = " ".join(["word"] * 100)
    script = {
        "planned_average_wpm": 100,
        "script_sections": [
            {
                "section_id": "sec_001",
                "planned_narration_wpm": 100,
                "narration": narration,
            }
        ],
        "full_script": narration,
    }
    segmentation = segment_narration(script, maximum_characters=10_000)
    plan = build_voice_pacing_plan(
        script=script,
        segmentation=segmentation,
        base_native_speaking_rate=1.0,
        pacing_directive_resolver=lambda request: VoiceProviderPacingDirective(
            control_model="relative_multiplier",
            value=provider_rate,
            target_narration_wpm=request.target_narration_wpm,
            policy_version="test_relative_pacing_v1",
            clamped=False,
            value_range=(0.5, 2.0),
        ),
        source_script_revision_id="artifact_script",
    )
    target_samples = 60 * CANONICAL_SAMPLE_RATE
    raw_samples = round(target_samples / raw_to_target_wpm_ratio)
    return analyze_section_pacing(
        segment_bytes=[_wav(raw_samples)],
        segment_records=[{
            "segment_task_id": "task_001",
            "segment_audio_artifact_id": "raw_001",
            "raw_segment_synthesis_identity_sha256": "test_raw_segment_identity_task_001",
            "sample_count": raw_samples,
            "provider_timing": None,
        }],
        segmentation=segmentation,
        pacing_plan=plan,
    )


def test_product_qc_uses_actual_wpm_deviation_at_five_percent() -> None:
    assert VOICE_PACING_QUALITY_FENCE_RELATIVE == pytest.approx(0.05)
    assert VOICE_PACING_ATEMPO_SAFETY_FENCE_RELATIVE == pytest.approx(0.15)

    within = _analysis(raw_to_target_wpm_ratio=1.049)
    outside = _analysis(raw_to_target_wpm_ratio=1.051)

    assert within.report["status"] == "pass"
    assert outside.report["status"] == "regeneration_required"
    section = outside.report["sections"][0]
    assert section["issue_code"] == "VOICE_PACING_WPM_OUT_OF_RANGE"
    assert section["wpm_deviation_relative"] == pytest.approx(0.051, abs=2e-4)
    assert section["correction_relative"] < 0


def test_analysis_exposes_median_ready_native_rate_calibration_evidence() -> None:
    analysis = _analysis(raw_to_target_wpm_ratio=1.8, provider_rate=1.2)
    section = analysis.report["sections"][0]
    # 180 raw WPM at provider rate 1.2 implies 150 WPM at rate=1 for this exact
    # text/voice observation.  The report records evidence; it does not mutate a
    # provider baseline or create an adaptive tuning loop.
    assert section["raw_wpm"] == pytest.approx(180.0, abs=0.01)
    assert section["inferred_native_wpm_at_rate_1"] == pytest.approx(150.0, abs=0.01)
    assert analysis.report["inferred_native_wpm_at_rate_1_median"] == pytest.approx(150.0, abs=0.01)
    assert analysis.report["inferred_native_wpm_observation_count"] == 1
