from __future__ import annotations

from app.modules.v4.voice_pacing_calibration import find_voice_pacing_calibration


JUSTIN_VOICE_ID = "BzWc3iJ0MiRdqIo6RCvM"


def test_justin_has_150_wpm_calibration_for_active_voice_models() -> None:
    for model_id in ("eleven_flash_v2_5", "eleven_multilingual_v2"):
        calibration = find_voice_pacing_calibration(
            provider_key="elevenlabs",
            voice_id=JUSTIN_VOICE_ID,
            model_id=model_id,
        )
        assert calibration is not None
        assert calibration.native_wpm_at_rate_1 == 150.0
        assert calibration.metadata["voice_name"] == "Justin - Authentic Customer Service Agent"
        assert calibration.metadata["source"] == "owner_configured_baseline"


def test_justin_calibration_remains_fail_closed_for_unregistered_model() -> None:
    assert find_voice_pacing_calibration(
        provider_key="elevenlabs",
        voice_id=JUSTIN_VOICE_ID,
        model_id="eleven_turbo_v2_5",
    ) is None


ALEX_VOICE_ID = "cQb5fnQsTT1X0GdpmHoo"


def test_alex_has_provisional_150_wpm_calibration_for_active_voice_models() -> None:
    for model_id in ("eleven_flash_v2_5", "eleven_multilingual_v2"):
        calibration = find_voice_pacing_calibration(
            provider_key="elevenlabs",
            voice_id=ALEX_VOICE_ID,
            model_id=model_id,
        )
        assert calibration is not None
        assert calibration.native_wpm_at_rate_1 == 150.0
        assert calibration.metadata["voice_name"] == "Alex - Intelligent, Friendly, Confident"
        assert calibration.metadata["source"] == "provisional_configured_baseline"


def test_alex_calibration_remains_fail_closed_for_unregistered_model() -> None:
    assert find_voice_pacing_calibration(
        provider_key="elevenlabs",
        voice_id=ALEX_VOICE_ID,
        model_id="eleven_turbo_v2_5",
    ) is None
