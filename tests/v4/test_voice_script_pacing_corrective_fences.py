from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_durable_submit_uses_frozen_project_script_id_not_undefined_variable() -> None:
    source = (ROOT / "app/modules/v4/durable_voice_service.py").read_text(encoding="utf-8")
    assert "script_artifact.artifact_id" not in source
    assert "source_script_revision_id=str(project.script_artifact_id)" in source


def test_voice_pacing_has_no_provider_name_branch_and_no_material_fit() -> None:
    pacing = (ROOT / "app/modules/v4/voice_pacing.py").read_text(encoding="utf-8")
    service = (ROOT / "app/modules/v4/durable_voice_service.py").read_text(encoding="utf-8")
    assert 'provider == "elevenlabs"' not in pacing + service
    assert 'provider == "kokoro' not in pacing + service
    assert "VOICE_PACING_SAMPLE_ADJUSTMENT_OUT_OF_RANGE" in pacing
    assert "actual_section_samples" in pacing


def test_voice_primary_ui_follows_script_plan_not_native_speed_slider() -> None:
    panel = (ROOT / "src/features/studio/VoicePanel.tsx").read_text(encoding="utf-8")
    components = (ROOT / "src/features/studio/voice/VoiceReviewComponents.tsx").read_text(encoding="utf-8")
    assert "voice-preset-select" in panel
    assert "voice-speaking-rate" not in panel + components
    assert "Native speed hint:" not in panel + components
    assert "voice-profile-select" not in panel + components
    assert "voice-voice-select" not in panel + components


def test_legacy_voice_service_generation_is_fenced_after_task2_cutover() -> None:
    source = (ROOT / "app/modules/v4/voice_service.py").read_text(encoding="utf-8")
    submit = source[source.index("    def submit_generation"):source.index("    def _stage_segment_artifact")]
    assert "VOICE_TASK2_DURABLE_SERVICE_REQUIRED" in submit
    assert "voice_preset" not in submit


def test_public_voice_ui_has_no_segment_regeneration_cta() -> None:
    panel = (ROOT / "src/features/studio/VoicePanel.tsx").read_text(encoding="utf-8")
    components = (ROOT / "src/features/studio/voice/VoiceReviewComponents.tsx").read_text(encoding="utf-8")
    client = (ROOT / "src/api/client.ts").read_text(encoding="utf-8")
    assert "regenerateVoiceSegment" not in client
    assert "/voice/segments/${segmentId}/regenerate/jobs" not in client
    assert "Regenerate Segment" not in panel + components
    assert "Regenerate Section" in components
