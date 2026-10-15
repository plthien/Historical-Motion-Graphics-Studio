from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from app.api.errors import AppError


VOICE_PACING_CALIBRATION_POLICY_VERSION = "voice_native_wpm_registry_v1"


@dataclass(frozen=True, slots=True)
class VoicePacingCalibration:
    provider_key: str
    voice_id: str
    model_id: str
    native_wpm_at_rate_1: float
    metadata: dict[str, Any]

    def __post_init__(self) -> None:
        if not self.provider_key.strip() or not self.voice_id.strip() or not self.model_id.strip():
            raise ValueError("Voice pacing calibration identity must be complete.")
        if self.native_wpm_at_rate_1 <= 0:
            raise ValueError("Voice pacing native WPM must be positive.")

    def snapshot(self) -> dict[str, Any]:
        return {
            "policy_version": VOICE_PACING_CALIBRATION_POLICY_VERSION,
            "provider": self.provider_key,
            "voice_id": self.voice_id,
            "model_id": self.model_id,
            "native_wpm_at_rate_1": self.native_wpm_at_rate_1,
            "metadata": dict(self.metadata),
        }

    def identity_sha256(self) -> str:
        payload = json.dumps(
            self.snapshot(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def base_provider_rate_for_planned_average(self, planned_average_wpm: int) -> float:
        if planned_average_wpm <= 0:
            raise ValueError("Planned average WPM must be positive.")
        # Existing provider pacing is expressed as:
        #   base_rate * section_target_wpm / planned_average_wpm
        # Resolve the base rate from the calibrated native WPM so the resulting
        # initial section speed is exactly section_target_wpm / native_wpm_at_rate_1.
        return float(planned_average_wpm) / self.native_wpm_at_rate_1


VOICE_PACING_CALIBRATION_REGISTRY: dict[tuple[str, str, str], VoicePacingCalibration] = {
    (
        "elevenlabs",
        "j9jfwdrw7BRfcR43Qohk",
        "eleven_flash_v2_5",
    ): VoicePacingCalibration(
        provider_key="elevenlabs",
        voice_id="j9jfwdrw7BRfcR43Qohk",
        model_id="eleven_flash_v2_5",
        native_wpm_at_rate_1=142.3,
        metadata={
            "source": "owner_real_provider_acceptance",
            "statistic": "robust_native_wpm_baseline",
            "notes": "Seeded from PR #12 real-provider section observations.",
        },
    ),
    (
        "elevenlabs",
        "BzWc3iJ0MiRdqIo6RCvM",
        "eleven_flash_v2_5",
    ): VoicePacingCalibration(
        provider_key="elevenlabs",
        voice_id="BzWc3iJ0MiRdqIo6RCvM",
        model_id="eleven_flash_v2_5",
        native_wpm_at_rate_1=150.0,
        metadata={
            "source": "owner_configured_baseline",
            "statistic": "configured_native_wpm_baseline",
            "voice_name": "Justin - Authentic Customer Service Agent",
            "notes": "Owner-configured 150 WPM baseline for the active Flash v2.5 preset.",
        },
    ),
    (
        "elevenlabs",
        "BzWc3iJ0MiRdqIo6RCvM",
        "eleven_multilingual_v2",
    ): VoicePacingCalibration(
        provider_key="elevenlabs",
        voice_id="BzWc3iJ0MiRdqIo6RCvM",
        model_id="eleven_multilingual_v2",
        native_wpm_at_rate_1=150.0,
        metadata={
            "source": "owner_configured_baseline",
            "statistic": "configured_native_wpm_baseline",
            "voice_name": "Justin - Authentic Customer Service Agent",
            "notes": "Owner-configured 150 WPM baseline for the active Multilingual v2 preset.",
        },
    ),
    (
        "elevenlabs",
        "cQb5fnQsTT1X0GdpmHoo",
        "eleven_flash_v2_5",
    ): VoicePacingCalibration(
        provider_key="elevenlabs",
        voice_id="cQb5fnQsTT1X0GdpmHoo",
        model_id="eleven_flash_v2_5",
        native_wpm_at_rate_1=150.0,
        metadata={
            "source": "provisional_configured_baseline",
            "statistic": "configured_native_wpm_baseline",
            "voice_name": "Alex - Intelligent, Friendly, Confident",
            "notes": "Provisional 150 WPM baseline for the active Flash v2.5 preset; replace after real-provider calibration.",
        },
    ),
    (
        "elevenlabs",
        "cQb5fnQsTT1X0GdpmHoo",
        "eleven_multilingual_v2",
    ): VoicePacingCalibration(
        provider_key="elevenlabs",
        voice_id="cQb5fnQsTT1X0GdpmHoo",
        model_id="eleven_multilingual_v2",
        native_wpm_at_rate_1=150.0,
        metadata={
            "source": "provisional_configured_baseline",
            "statistic": "configured_native_wpm_baseline",
            "voice_name": "Alex - Intelligent, Friendly, Confident",
            "notes": "Provisional 150 WPM baseline for the active Multilingual v2 preset; replace after real-provider calibration.",
        },
    ),
}


def find_voice_pacing_calibration(
    *,
    provider_key: str,
    voice_id: str,
    model_id: str,
) -> VoicePacingCalibration | None:
    return VOICE_PACING_CALIBRATION_REGISTRY.get(
        (provider_key.strip(), voice_id.strip(), model_id.strip())
    )


def resolve_voice_pacing_calibration(
    *,
    provider_key: str,
    voice_id: str,
    model_id: str,
) -> VoicePacingCalibration:
    key = (provider_key.strip(), voice_id.strip(), model_id.strip())
    calibration = find_voice_pacing_calibration(
        provider_key=key[0],
        voice_id=key[1],
        model_id=key[2],
    )
    if calibration is None:
        raise AppError(
            "VOICE_PACING_CALIBRATION_REQUIRED",
            "Voice pacing calibration is required for the selected provider, voice, and model.",
            409,
            retryable=False,
            details={
                "provider": key[0],
                "voice_id": key[1],
                "model_id": key[2],
                "calibration_policy_version": VOICE_PACING_CALIBRATION_POLICY_VERSION,
            },
        )
    return calibration
