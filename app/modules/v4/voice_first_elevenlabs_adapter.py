from __future__ import annotations

import hashlib
import wave
from typing import Any

from app.api.errors import AppError
from app.modules.v4.elevenlabs_client import ElevenLabsAmbiguousSubmission
from app.modules.v4.elevenlabs_model_policy import parse_elevenlabs_provider_options
from app.modules.v4.elevenlabs_provider_adapter import ElevenLabsProviderAdapter
from app.modules.v4.voice_first_policy import (
    ACTIVE_VOICE_PRESETS,
    ELEVENLABS_STYLE,
    ELEVENLABS_SEED_POLICY_VERSION,
    ELEVENLABS_SPEED_MAX,
    ELEVENLABS_SPEED_MIN,
    ELEVENLABS_SYNTHESIS_POLICY_VERSION,
)
from app.modules.v4.voice_provider_contracts import (
    VoiceExecutionControl,
    VoiceProviderCapabilities,
    VoiceProviderExecutionResult,
    VoiceProviderPacingObservation,
    VoiceProviderPacingRecommendation,
    VoiceProviderProfile,
    VoiceProviderReadiness,
    VoiceProviderSubmission,
    VoiceSynthesisRequest,
)


_INTERNAL_PAYLOADS_KEY = "_voice_first_segment_payloads"


class VoiceFirstElevenLabsProviderAdapter(ElevenLabsProviderAdapter):
    """Active Task-2 ElevenLabs adapter using standard binary TTS only."""

    provider_key = "elevenlabs"
    _ADAPTER_CONTRACT = "elevenlabs_voice_first_standard_tts_v1"
    _PACING_POLICY_VERSION = "elevenlabs_voice_first_relative_wpm_v1"

    @staticmethod
    def _public_options(raw: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in raw.items() if key != _INTERNAL_PAYLOADS_KEY}

    def capabilities(self) -> VoiceProviderCapabilities:
        return VoiceProviderCapabilities(
            execution_model="synchronous",
            supports_idempotency=False,
            supports_submission_lookup=False,
            supports_cancel=False,
            supports_word_timing=False,
            supports_character_timing=False,
            supports_speaking_rate=True,
            pacing_control="relative_multiplier",
            pacing_value_range=(ELEVENLABS_SPEED_MIN, ELEVENLABS_SPEED_MAX),
            supports_language_selection=False,
            supported_output_formats=("wav_pcm_s16le",),
            supported_sample_rates=(24_000,),
            max_input_characters=40_000,
        )

    def readiness(self, profile: VoiceProviderProfile) -> VoiceProviderReadiness:
        clean_profile = VoiceProviderProfile(
            profile_id=profile.profile_id,
            provider_key=profile.provider_key,
            enabled=profile.enabled,
            display_name=profile.display_name,
            voice_id=profile.voice_id,
            language=profile.language,
            speaking_rate=profile.speaking_rate,
            requested_sample_rate_hz=profile.requested_sample_rate_hz,
            requested_output_format=profile.requested_output_format,
            provider_options=self._public_options(profile.provider_options),
            maximum_segment_characters=profile.maximum_segment_characters,
        )
        base = super().readiness(clean_profile)
        if not base.ready:
            return base
        options = parse_elevenlabs_provider_options(clean_profile.provider_options)
        try:
            action_context = self._action_context()
        except AppError as exc:
            return VoiceProviderReadiness(status="blocked", issue_code=exc.error_code, message=exc.message, details=exc.details)
        client = self._client_for_action(action_context)
        try:
            raw_voices = self._cached_voices(options, action_context=action_context, client=client)
        except AppError as exc:
            return VoiceProviderReadiness(status="blocked", issue_code=exc.error_code, message=exc.message, details=exc.details)
        finally:
            client.close()
        raw = next(
            (item for item in raw_voices if isinstance(item, dict) and item.get("voice_id") == clean_profile.voice_id),
            None,
        )
        fine_tuning = raw.get("fine_tuning") if isinstance(raw, dict) else None
        states = fine_tuning.get("state") if isinstance(fine_tuning, dict) else None
        if (
            not isinstance(raw, dict)
            or raw.get("category") != "professional"
            or not isinstance(states, dict)
            or states.get(options.model_id) != "fine_tuned"
        ):
            return VoiceProviderReadiness(
                status="blocked",
                issue_code="VOICE_PVC_NOT_READY",
                message="The selected Channel voice is not a fine-tuned Professional Voice Clone for the active model.",
                details={"voice_id": clean_profile.voice_id, "model_id": options.model_id},
            )
        return VoiceProviderReadiness(status="ready")

    def professional_voice_catalog(self) -> tuple[dict[str, Any], ...]:
        """Return account Professional Voice Clones and preset readiness without synthesis."""
        action_context = self._action_context()
        client = self._client_for_action(action_context)
        try:
            options = parse_elevenlabs_provider_options({})
            normalized = {
                voice.voice_id: voice
                for voice in self._list_voices_for_action(
                    None,
                    action_context=action_context,
                    client=client,
                )
            }
            raw_voices = self._cached_voices(
                options,
                action_context=action_context,
                client=client,
            )
        finally:
            client.close()

        catalog: list[dict[str, Any]] = []
        for item in raw_voices:
            if not isinstance(item, dict) or item.get("category") != "professional":
                continue
            voice_id = item.get("voice_id")
            voice = normalized.get(str(voice_id)) if isinstance(voice_id, str) else None
            if voice is None:
                continue
            fine_tuning = item.get("fine_tuning") if isinstance(item.get("fine_tuning"), dict) else {}
            states = fine_tuning.get("state") if isinstance(fine_tuning.get("state"), dict) else {}
            ready_presets = [
                preset
                for preset, model_id in ACTIVE_VOICE_PRESETS.items()
                if states.get(model_id) == "fine_tuned"
            ]
            catalog.append(
                {
                    "voice_id": voice.voice_id,
                    "display_name": voice.display_name,
                    "language": voice.language,
                    "ready_presets": ready_presets,
                }
            )
        return tuple(catalog)

    def execution_identity(self, profile: VoiceProviderProfile) -> dict[str, Any]:
        clean_options = self._public_options(profile.provider_options)
        options = parse_elevenlabs_provider_options(clean_options)
        action_context = self._action_context()
        return {
            "schema_version": 2,
            "provider_key": self.provider_key,
            "adapter_contract": self._ADAPTER_CONTRACT,
            "synthesis_policy_version": ELEVENLABS_SYNTHESIS_POLICY_VERSION,
            "model_policy": options.policy.policy_version,
            "model_id": options.model_id,
            "wire_output_format": options.policy.wire_output_format,
            "wire_sample_rate_hz": options.policy.wire_sample_rate_hz,
            "continuity_mode": "canonical_spoken_previous_next_text_v1",
            "text_normalization": "off",
            "style": ELEVENLABS_STYLE,
            "seed_policy": ELEVENLABS_SEED_POLICY_VERSION,
            "endpoint": "/v1/text-to-speech/{voice_id}",
            "api_base_identity_sha256": self._sha256_text(action_context.api_base_identity_material),
            "credential_fingerprint_sha256": action_context.credential_fingerprint_sha256,
        }

    def recommend_pacing(self, observation: VoiceProviderPacingObservation) -> VoiceProviderPacingRecommendation:
        current = observation.current_provider_pacing_value
        if current is None or observation.current_control_model != "relative_multiplier":
            return VoiceProviderPacingRecommendation(
                status="unsupported",
                control_model="relative_multiplier",
                recommended_value=None,
                policy_version="elevenlabs_voice_first_section_feedback_v1",
                value_range=(ELEVENLABS_SPEED_MIN, ELEVENLABS_SPEED_MAX),
                reason="Voice section feedback requires an existing ElevenLabs relative speed value.",
            )
        proposed = float(current) * float(observation.target_narration_wpm) / float(observation.measured_raw_wpm)
        bounded = min(ELEVENLABS_SPEED_MAX, max(ELEVENLABS_SPEED_MIN, proposed))
        rounded = round(bounded, 6)
        if abs(rounded - float(current)) <= 0.000_001:
            return VoiceProviderPacingRecommendation(
                status="unsupported",
                control_model="relative_multiplier",
                recommended_value=None,
                policy_version="elevenlabs_voice_first_section_feedback_v1",
                value_range=(ELEVENLABS_SPEED_MIN, ELEVENLABS_SPEED_MAX),
                reason=(
                    "provider_pacing_limit_reached"
                    if abs(bounded - proposed) > 0.000_001
                    else "provider_pacing_no_progress"
                ),
            )
        return VoiceProviderPacingRecommendation(
            status="supported",
            control_model="relative_multiplier",
            recommended_value=rounded,
            policy_version="elevenlabs_voice_first_section_feedback_v1",
            clamped=abs(bounded - proposed) > 0.000_001,
            value_range=(ELEVENLABS_SPEED_MIN, ELEVENLABS_SPEED_MAX),
        )

    def submit(self, request: VoiceSynthesisRequest, control: VoiceExecutionControl) -> VoiceProviderSubmission:
        self._raise_if_not_active(control)
        clean_options = self._public_options(request.provider_options)
        options = parse_elevenlabs_provider_options(clean_options)
        payloads = request.provider_options.get(_INTERNAL_PAYLOADS_KEY)
        if not isinstance(payloads, dict):
            raise AppError("VOICE_SYNTHESIS_IDENTITY_INVALID", "Voice-First provider payloads are missing from the frozen operation.", 409, retryable=False)
        segment_payload = payloads.get(request.segment_task_id)
        if not isinstance(segment_payload, dict):
            raise AppError("VOICE_SYNTHESIS_IDENTITY_INVALID", "Voice-First provider payload is missing for the segment task.", 409, retryable=False)
        provider_text = segment_payload.get("provider_text")
        seed = segment_payload.get("seed")
        canonical_sha = segment_payload.get("canonical_spoken_text_sha256")
        if (
            not isinstance(provider_text, str)
            or not provider_text
            or not isinstance(seed, int)
            or not 0 <= seed <= 4_294_967_295
            or canonical_sha != hashlib.sha256(request.exact_text.encode("utf-8")).hexdigest()
        ):
            raise AppError("VOICE_SYNTHESIS_IDENTITY_INVALID", "Voice-First provider payload does not match canonical spoken text.", 409, retryable=False)

        pacing = request.pacing
        if pacing is None or pacing.control_model != "relative_multiplier" or pacing.value is None:
            raise AppError("VOICE_PROVIDER_PACING_INVALID", "ElevenLabs requires relative-multiplier pacing.", 409, retryable=False)
        speed = float(pacing.value)
        if not ELEVENLABS_SPEED_MIN <= speed <= ELEVENLABS_SPEED_MAX:
            raise AppError("ELEVENLABS_SPEED_UNSUPPORTED", "ElevenLabs Voice-First speed must be between 0.70 and 1.20.", 422, retryable=False)

        action_context = self._action_context(
            getattr(control, "expected_execution_identity", None)
            if isinstance(getattr(control, "expected_execution_identity", None), dict)
            else None
        )
        client = self._client_for_action(action_context)
        try:
            clean_profile = VoiceProviderProfile(
                profile_id="channel_elevenlabs_pvc",
                provider_key=self.provider_key,
                enabled=True,
                display_name="Channel ElevenLabs PVC",
                voice_id=request.voice_id,
                language=request.language,
                speaking_rate=request.speaking_rate,
                requested_sample_rate_hz=request.requested_sample_rate_hz,
                requested_output_format=request.requested_output_format,
                provider_options=clean_options,
                maximum_segment_characters=options.policy.maximum_input_characters,
            )
            if request.voice_id not in {
                voice.voice_id
                for voice in self._list_voices_for_action(clean_profile, action_context=action_context, client=client)
            }:
                raise AppError("TTS_VOICE_INVALID", "The selected Channel PVC is not available in the account catalog.", 422, retryable=False)

            body: dict[str, Any] = {
                "text": provider_text,
                "model_id": options.model_id,
                "voice_settings": {
                    "stability": options.stability,
                    "similarity_boost": options.similarity_boost,
                    "style": ELEVENLABS_STYLE,
                    "use_speaker_boost": options.use_speaker_boost,
                    "speed": speed,
                },
                "seed": seed,
                "apply_text_normalization": "off",
            }
            if request.previous_text is not None:
                body["previous_text"] = request.previous_text
            if request.next_text is not None:
                body["next_text"] = request.next_text
            try:
                self._raise_if_not_active(control)
                response = client.synthesize(
                    voice_id=request.voice_id,
                    output_format=options.policy.wire_output_format,
                    body=body,
                )
            except ElevenLabsAmbiguousSubmission:
                return VoiceProviderSubmission(status="ambiguous")
        finally:
            client.close()

        pcm = response.content
        if not pcm or len(pcm) % 2 != 0 or len(pcm) > self.settings.tts_provider_max_output_bytes:
            raise AppError("VOICE_PROVIDER_AUDIO_INVALID", "ElevenLabs PCM audio was empty or malformed.", 422, retryable=False)
        output_path = request.attempt_output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output_path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(options.policy.wire_sample_rate_hz)
            wav.writeframes(pcm)
        request_id = response.headers.get("request-id") or response.headers.get("x-request-id")
        metadata = {
            "provider": self.provider_key,
            "model_id": options.model_id,
            "request_id": request_id if isinstance(request_id, str) and request_id else None,
            "synthesis_policy_version": ELEVENLABS_SYNTHESIS_POLICY_VERSION,
            "text_normalization": "off",
            "seed": seed,
        }
        return VoiceProviderSubmission(
            status="completed",
            result=VoiceProviderExecutionResult(
                operation_id=request.operation_id,
                segment_task_id=request.segment_task_id,
                execution_attempt_token=request.execution_attempt_token,
                provider_key=self.provider_key,
                provider_task_id=request_id if isinstance(request_id, str) and request_id else None,
                audio_file_path=output_path,
                source_mime_type="audio/wav",
                sample_rate_hz=options.policy.wire_sample_rate_hz,
                channels=1,
                sample_width_bytes=2,
                duration_seconds=len(pcm) / float(options.policy.wire_sample_rate_hz * 2),
                provider_word_timings=None,
                provider_character_timings=None,
                sanitized_metadata=metadata,
            ),
        )
