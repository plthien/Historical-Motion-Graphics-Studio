from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Callable

from app.api.errors import AppError
from app.core.config import Settings, get_settings
from app.modules.v4.elevenlabs_credentials import build_runtime_elevenlabs_credential_resolver
from app.modules.v4.elevenlabs_model_policy import DEFAULT_ELEVENLABS_MODEL_ID
from app.modules.v4.voice_first_elevenlabs_adapter import VoiceFirstElevenLabsProviderAdapter
from app.modules.v4.voice_provider_adapters import KokoroProviderAdapter
from kokoro.runtime_response_calibration import (
    KOKORO_FLOW_VERSION,
    validate_runtime_response_calibration,
)
from app.modules.v4.voice_pacing_calibration import (
    VoicePacingCalibration,
    find_voice_pacing_calibration,
)
from app.modules.v4.voice_pacing import canonical_hash
from app.modules.v4.voice_pacing_calibration_settings import KOKORO_LOCAL_MODEL_ID
from app.modules.v4.voice_provider_contracts import (
    VoiceProviderPacingDirective,
    VoiceProviderPacingRequest,
    VoiceProviderProfile,
    VoiceProviderRegistry,
)


VoicePacingCalibrationResolver = Callable[[str, str, str], VoicePacingCalibration | None]


class CalibratedVoiceProviderRegistry(VoiceProviderRegistry):
    """Bind exact provider calibration to pacing without changing runtime identity.

    ElevenLabs uses the configured calibration resolver. Kokoro reads its measured
    calibration directly from the validated ready marker and verifies the adapter
    identity before and after that read. ContextVar keeps concurrent planning
    isolated.
    """

    def __init__(
        self,
        *,
        pacing_calibration_resolver: VoicePacingCalibrationResolver | None = None,
    ) -> None:
        super().__init__()
        self._pacing_calibration_resolver = pacing_calibration_resolver
        self._pacing_calibration: ContextVar[VoicePacingCalibration | None] = ContextVar(
            f"voice_pacing_calibration_{id(self)}",
            default=None,
        )

    def execution_identity(self, profile: VoiceProviderProfile) -> dict[str, Any]:
        identity = super().execution_identity(profile)
        if profile.provider_key == VoiceFirstElevenLabsProviderAdapter.provider_key:
            model_id = str(profile.provider_options.get("model_id") or "").strip()
            resolver = self._pacing_calibration_resolver
            self._pacing_calibration.set(
                resolver(profile.provider_key, profile.voice_id, model_id)
                if resolver is not None
                else find_voice_pacing_calibration(
                    provider_key=profile.provider_key,
                    voice_id=profile.voice_id,
                    model_id=model_id,
                )
            )
        elif profile.provider_key == KokoroProviderAdapter.provider_key:
            adapter = self.resolve(KokoroProviderAdapter.provider_key)
            read_calibration = getattr(adapter, "runtime_response_calibration", None)
            measurement = (
                validate_runtime_response_calibration(read_calibration(profile))
                if callable(read_calibration)
                else None
            )
            measurement_sha256 = canonical_hash(measurement) if measurement is not None else None
            if (
                measurement is None
                or identity.get("flow_version") != KOKORO_FLOW_VERSION
                or identity.get("runtime_response_calibration_sha256") != measurement_sha256
            ):
                raise AppError(
                    "VOICE_PACING_CALIBRATION_IDENTITY_INVALID",
                    "Kokoro runtime calibration does not match the frozen execution identity.",
                    409,
                    retryable=True,
                )
            if adapter.execution_identity(profile) != identity:
                raise AppError(
                    "VOICE_PACING_CALIBRATION_IDENTITY_INVALID",
                    "Kokoro runtime identity changed while calibration was being resolved.",
                    409,
                    retryable=True,
                )
            execution_identity_sha256 = canonical_hash(identity)
            self._pacing_calibration.set(VoicePacingCalibration(
                provider_key=profile.provider_key,
                voice_id=profile.voice_id,
                model_id=KOKORO_LOCAL_MODEL_ID,
                native_wpm_at_rate_1=float(measurement["native_wpm_at_rate_1"]),
                metadata={
                    "source": "kokoro_ready_marker_calibration",
                    "statistic": "median_canonical_text_native_wpm_at_rate_1",
                    "speed_response_exponent": float(measurement["speed_response_exponent"]),
                    "speed_response_model_source": "runtime_calibrated_default",
                    "speed_response_model_identity": canonical_hash({
                        "flow_version": KOKORO_FLOW_VERSION,
                        "execution_identity_sha256": execution_identity_sha256,
                        "runtime_response_calibration_sha256": measurement_sha256,
                    }),
                    "execution_identity_sha256": execution_identity_sha256,
                },
            ))
        else:
            self._pacing_calibration.set(None)
        return identity

    def pacing_directive(
        self,
        provider_key: str,
        request: VoiceProviderPacingRequest,
    ) -> VoiceProviderPacingDirective:
        if provider_key == KokoroProviderAdapter.provider_key:
            calibration = self._pacing_calibration.get()
            if calibration is None:
                return super().pacing_directive(provider_key, request)
            normalization_wpm = KokoroProviderAdapter._RATE_NORMALIZATION_WPM
            calibrated_request = VoiceProviderPacingRequest(
                section_id=request.section_id,
                target_narration_wpm=request.target_narration_wpm,
                planned_average_wpm=request.planned_average_wpm,
                base_native_speaking_rate=normalization_wpm / calibration.native_wpm_at_rate_1,
                actual_word_count=request.actual_word_count,
                target_duration_seconds=request.target_duration_seconds,
                fixed_duration_seconds=request.fixed_duration_seconds,
                speed_response_exponent=float(calibration.metadata.get("speed_response_exponent", 1.0)),
                response_model_source=str(calibration.metadata.get("speed_response_model_source", "fallback")),
                response_model_identity=(
                    str(calibration.metadata["speed_response_model_identity"])
                    if isinstance(calibration.metadata.get("speed_response_model_identity"), str)
                    else calibration.identity_sha256()
                ),
            )
            return super().pacing_directive(provider_key, calibrated_request)
        if provider_key != VoiceFirstElevenLabsProviderAdapter.provider_key:
            return super().pacing_directive(provider_key, request)
        calibration = self._pacing_calibration.get()
        if calibration is None:
            from app.api.errors import AppError

            raise AppError(
                "VOICE_PACING_CALIBRATION_REQUIRED",
                "Voice pacing calibration is required for the selected ElevenLabs voice and model.",
                409,
                retryable=False,
                details={
                    "provider": provider_key,
                    "section_id": request.section_id,
                },
            )
        calibrated_request = VoiceProviderPacingRequest(
            section_id=request.section_id,
            target_narration_wpm=request.target_narration_wpm,
            planned_average_wpm=request.planned_average_wpm,
            base_native_speaking_rate=calibration.base_provider_rate_for_planned_average(
                request.planned_average_wpm
            ),
            actual_word_count=request.actual_word_count,
            target_duration_seconds=request.target_duration_seconds,
            fixed_duration_seconds=request.fixed_duration_seconds,
        )
        directive = super().pacing_directive(provider_key, calibrated_request)
        return VoiceProviderPacingDirective(
            control_model=directive.control_model,
            value=directive.value,
            target_narration_wpm=directive.target_narration_wpm,
            policy_version=(
                "elevenlabs_calibrated_native_wpm_v1:"
                f"{calibration.identity_sha256()[:16]}"
            ),
            clamped=directive.clamped,
            value_range=directive.value_range,
        )


def build_voice_provider_registry(
    settings: Settings | None = None,
    *,
    pacing_calibration_resolver: VoicePacingCalibrationResolver | None = None,
) -> VoiceProviderRegistry:
    """Build the active Voice generation provider registry."""
    runtime = settings or get_settings()
    registry = CalibratedVoiceProviderRegistry(
        pacing_calibration_resolver=pacing_calibration_resolver,
    )
    registry.register(
        VoiceFirstElevenLabsProviderAdapter(
            runtime,
            credential_resolver=build_runtime_elevenlabs_credential_resolver(runtime),
        )
    )
    registry.register(KokoroProviderAdapter(runtime))
    return registry


def disabled_voice_settings() -> dict[str, Any]:
    return {
        "active_profile_id": "disabled",
        "profiles": [
            {
                "profile_id": "disabled",
                "provider": "disabled",
                "enabled": False,
                "display_name": "Disabled",
                "default_voice_id": None,
                "default_speaking_rate": 1.0,
                "sample_rate_hz": 48_000,
                "output_format": "wav_pcm_s16le",
                "maximum_segment_characters": 1_000,
            }
        ],
    }


def _elevenlabs_bootstrap_profiles() -> list[dict[str, Any]]:
    return [
        {
            "profile_id": "elevenlabs_multilingual_v2",
            "provider": VoiceFirstElevenLabsProviderAdapter.provider_key,
            "enabled": True,
            "display_name": "ElevenLabs Multilingual v2",
            "default_voice_id": None,
            "default_speaking_rate": 1.0,
            "sample_rate_hz": 24_000,
            "output_format": "wav_pcm_s16le",
            "maximum_segment_characters": 10_000,
            "provider_options": {
                "model_id": DEFAULT_ELEVENLABS_MODEL_ID,
                "narration_language": "en",
            },
        }
    ]


def _kokoro_bootstrap_profile() -> dict[str, Any]:
    return {
        "profile_id": "kokoro_local",
        "provider": KokoroProviderAdapter.provider_key,
        "enabled": True,
        "display_name": "Kokoro Local",
        "default_voice_id": None,
        "default_speaking_rate": 1.0,
        "sample_rate_hz": 48_000,
        "output_format": "wav_pcm_s16le",
        "maximum_segment_characters": 800,
        "provider_options": {
            "runtime": "local_kokoro_cli",
        },
    }


def build_default_voice_settings(
    settings: Settings | None = None,
    *,
    registry: VoiceProviderRegistry | None = None,
) -> dict[str, Any]:
    """Bootstrap registered production generation profiles.

    `TTS_PROVIDER_MODE` no longer selects one synthesis provider globally; it
    only disables Voice generation when explicitly empty/disabled.
    """
    runtime = settings or get_settings()
    provider_key = runtime.tts_provider_mode.strip().lower()
    if provider_key in {"", "disabled"}:
        return disabled_voice_settings()

    active_registry = registry or build_voice_provider_registry(runtime)
    try:
        active_registry.resolve(VoiceFirstElevenLabsProviderAdapter.provider_key)
        active_registry.resolve(KokoroProviderAdapter.provider_key)
    except Exception:
        return disabled_voice_settings()

    profiles = [*_elevenlabs_bootstrap_profiles(), _kokoro_bootstrap_profile()]
    return {"active_profile_id": profiles[0]["profile_id"], "profiles": profiles}
