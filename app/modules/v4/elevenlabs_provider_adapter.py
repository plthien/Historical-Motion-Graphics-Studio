from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
import math
from threading import Lock
from time import monotonic
from typing import Any, Callable
from urllib.parse import urlsplit
import wave

from app.api.errors import AppError
from app.core.config import Settings, get_settings
from app.modules.v4.elevenlabs_client import ElevenLabsAmbiguousSubmission, ElevenLabsHttpClient
from app.modules.v4.elevenlabs_credentials import ElevenLabsCredentialResolver
from app.modules.v4.elevenlabs_model_policy import (
    ElevenLabsProviderOptions,
    parse_elevenlabs_provider_options,
)
from app.modules.v4.voice_provider_contracts import (
    VoiceExecutionControl,
    VoiceProviderCancelResult,
    VoiceProviderCapabilities,
    VoiceProviderExecutionResult,
    VoiceProviderHandle,
    VoiceProviderPacingDirective,
    VoiceProviderPacingObservation,
    VoiceProviderPacingRecommendation,
    VoiceProviderPacingRequest,
    VoiceProviderPollResult,
    VoiceProviderProfile,
    VoiceProviderReadiness,
    VoiceProviderReconcileResult,
    VoiceProviderSubmission,
    VoiceProviderVoice,
    VoiceSynthesisRequest,
)


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    expires_at: float
    value: Any


@dataclass(frozen=True, slots=True)
class _ElevenLabsActionContext:
    """Ephemeral account authority for exactly one provider action."""

    api_key: str = field(repr=False)
    credential_fingerprint_sha256: str
    api_base_url: str
    api_base_identity_material: str


class ElevenLabsProviderAdapter:
    provider_key = "elevenlabs"
    _PACING_POLICY_VERSION = "elevenlabs_relative_script_average_v1"
    _ADAPTER_CONTRACT = "elevenlabs_http_tts_v1"
    _VOICE_CACHE_SECONDS = 60.0
    _MODEL_CACHE_SECONDS = 300.0
    _MAX_CATALOG_PAGES = 100
    _cache_lock = Lock()
    _voice_cache: dict[tuple[str, str, str], _CacheEntry] = {}
    _model_cache: dict[tuple[str, str], _CacheEntry] = {}

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client_factory: Callable[[str, str, int, float], ElevenLabsHttpClient] | None = None,
        clock: Callable[[], float] = monotonic,
        credential_resolver: ElevenLabsCredentialResolver | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._client_factory = client_factory
        self._clock = clock
        self._credential_resolver = credential_resolver or ElevenLabsCredentialResolver(self.settings)

    def capabilities(self) -> VoiceProviderCapabilities:
        return VoiceProviderCapabilities(
            execution_model="synchronous",
            supports_idempotency=False,
            supports_submission_lookup=False,
            supports_cancel=False,
            supports_word_timing=False,
            supports_character_timing=True,
            supports_speaking_rate=True,
            pacing_control="relative_multiplier",
            pacing_value_range=(0.7, 1.2),
            supports_language_selection=False,
            supported_output_formats=("wav_pcm_s16le",),
            supported_sample_rates=(24_000,),
            max_input_characters=10_000,
        )

    def pacing_directive(self, request: VoiceProviderPacingRequest) -> VoiceProviderPacingDirective:
        value = float(request.base_native_speaking_rate) * (
            float(request.target_narration_wpm) / float(request.planned_average_wpm)
        )
        bounded, clamped = self._clamp_pacing_value(value, self.capabilities().pacing_value_range)
        return VoiceProviderPacingDirective(
            control_model="relative_multiplier",
            value=round(bounded, 6),
            target_narration_wpm=request.target_narration_wpm,
            policy_version=self._PACING_POLICY_VERSION,
            clamped=clamped,
            value_range=self.capabilities().pacing_value_range,
        )

    def recommend_pacing(self, observation: VoiceProviderPacingObservation) -> VoiceProviderPacingRecommendation:
        _ = observation
        capabilities = self.capabilities()
        return VoiceProviderPacingRecommendation(
            status="unsupported",
            control_model=capabilities.pacing_control,
            recommended_value=None,
            policy_version="elevenlabs_section_feedback_unsupported_v1",
            value_range=capabilities.pacing_value_range,
            reason="ElevenLabs Multilingual v2 measured-WPM feedback is not calibrated in this adapter.",
        )

    def list_voices(self, profile: VoiceProviderProfile | None = None) -> tuple[VoiceProviderVoice, ...]:
        action_context = self._action_context()
        client = self._client_for_action(action_context)
        try:
            return self._list_voices_for_action(
                profile,
                action_context=action_context,
                client=client,
            )
        finally:
            client.close()

    def readiness(self, profile: VoiceProviderProfile) -> VoiceProviderReadiness:
        try:
            action_context = self._action_context()
        except AppError as exc:
            return self._blocked(exc)
        try:
            options = parse_elevenlabs_provider_options(profile.provider_options)
        except AppError as exc:
            return self._blocked(exc)
        capabilities = self.capabilities()
        if profile.requested_output_format not in capabilities.supported_output_formats:
            return VoiceProviderReadiness(
                status="blocked",
                issue_code="VOICE_PROVIDER_OUTPUT_FORMAT_UNSUPPORTED",
                message="ElevenLabs does not support the requested Voice output format.",
            )
        if profile.requested_sample_rate_hz not in capabilities.supported_sample_rates:
            return VoiceProviderReadiness(
                status="blocked",
                issue_code="VOICE_PROVIDER_SAMPLE_RATE_UNSUPPORTED",
                message="ElevenLabs does not support the requested Voice sample rate.",
            )
        if not 0.7 <= float(profile.speaking_rate) <= 1.2:
            return VoiceProviderReadiness(
                status="blocked",
                issue_code="ELEVENLABS_SPEED_UNSUPPORTED",
                message="ElevenLabs Multilingual v2 supports speaking rates between 0.7 and 1.2.",
            )
        client = self._client_for_action(action_context)
        try:
            model = self._model(
                options.model_id,
                action_context=action_context,
                client=client,
            )
            if model.get("model_id") != options.model_id:
                return self._blocked_code(
                    "ELEVENLABS_MODEL_UNAVAILABLE",
                    "ElevenLabs model readiness metadata is unavailable.",
                )
            if model.get("can_do_text_to_speech") is not True:
                return self._blocked_code(
                    "ELEVENLABS_MODEL_TTS_UNAVAILABLE",
                    "The selected ElevenLabs model cannot do text-to-speech.",
                )
            live_max = model.get("maximum_text_length_per_request")
            if live_max is None:
                live_max = model.get("max_characters_request_subscribed_user")
            if isinstance(live_max, (int, float)) and int(live_max) > 0:
                configured_max = profile.maximum_segment_characters or options.policy.maximum_input_characters
                if configured_max > min(options.policy.maximum_input_characters, int(live_max)):
                    return self._blocked_code(
                        "ELEVENLABS_MODEL_SEGMENT_LIMIT_EXCEEDED",
                        "The configured segment size exceeds the live ElevenLabs model limit.",
                    )
            if not self._live_language_supported(model, options.narration_language):
                return self._blocked_code(
                    "ELEVENLABS_LANGUAGE_UNSUPPORTED",
                    "The selected narration language is not available for the live ElevenLabs model.",
                )
            voices = self._list_voices_for_action(
                profile,
                action_context=action_context,
                client=client,
            )
        except AppError as exc:
            return self._blocked(exc)
        finally:
            client.close()
        if not voices:
            return self._blocked_code(
                "TTS_VOICE_CATALOG_EMPTY",
                "ElevenLabs did not report any account voices.",
            )
        if profile.voice_id and profile.voice_id not in {voice.voice_id for voice in voices}:
            return self._blocked_code(
                "TTS_VOICE_INVALID",
                "The selected ElevenLabs voice is not available in the account catalog.",
            )
        return VoiceProviderReadiness(status="ready")

    @staticmethod
    def _clamp_pacing_value(value: float, bounds: tuple[float, float] | None) -> tuple[float, bool]:
        if bounds is None:
            return value, False
        minimum, maximum = float(bounds[0]), float(bounds[1])
        clamped = min(maximum, max(minimum, value))
        return clamped, abs(clamped - value) > 0.000_001

    def test_connection(self) -> dict[str, Any]:
        """Run only non-billable ElevenLabs discovery calls using one effective key."""
        action_context = self._action_context()
        options = parse_elevenlabs_provider_options({})
        client = self._client_for_action(action_context)
        try:
            response = client.list_models()
            if not isinstance(response.payload, list):
                raise AppError(
                    "ELEVENLABS_MODELS_INVALID",
                    "ElevenLabs model discovery returned a malformed payload.",
                    502,
                    retryable=True,
                )
            model = next(
                (
                    item
                    for item in response.payload
                    if isinstance(item, dict) and item.get("model_id") == options.model_id
                ),
                None,
            )
            if model is None:
                raise AppError(
                    "ELEVENLABS_MODEL_UNAVAILABLE",
                    "The selected ElevenLabs model is not available.",
                    409,
                    retryable=False,
                )
            if model.get("can_do_text_to_speech") is not True:
                raise AppError(
                    "ELEVENLABS_MODEL_TTS_UNAVAILABLE",
                    "The selected ElevenLabs model cannot do text-to-speech.",
                    409,
                    retryable=False,
                )
            voices = self._fetch_all_voices(client)
        finally:
            client.close()
        return {
            "provider": self.provider_key,
            "status": "ready",
            "model_id": options.model_id,
            "authentication_accepted": True,
            "catalog_reachable": True,
            "voice_count": len(voices),
        }

    def execution_identity(self, profile: VoiceProviderProfile) -> dict[str, Any]:
        options = parse_elevenlabs_provider_options(profile.provider_options)
        action_context = self._action_context()
        return {
            "schema_version": 1,
            "provider_key": self.provider_key,
            "adapter_contract": self._ADAPTER_CONTRACT,
            "model_policy": options.policy.policy_version,
            "model_id": options.model_id,
            "wire_output_format": options.policy.wire_output_format,
            "wire_sample_rate_hz": options.policy.wire_sample_rate_hz,
            "continuity_mode": "previous_next_text_v1",
            "api_base_identity_sha256": self._sha256_text(action_context.api_base_identity_material),
            "credential_fingerprint_sha256": action_context.credential_fingerprint_sha256,
        }

    def submit(
        self,
        request: VoiceSynthesisRequest,
        control: VoiceExecutionControl,
    ) -> VoiceProviderSubmission:
        _ = (request, control)
        raise AppError(
            "VOICE_FIRST_ADAPTER_REQUIRED",
            "ElevenLabs synthesis is owned by the Voice-first standard TTS adapter.",
            409,
            retryable=False,
        )

    @staticmethod
    def _raise_if_not_active(control: VoiceExecutionControl) -> None:
        if control.cancellation_requested():
            raise AppError(
                "VOICE_OPERATION_CANCELLED",
                "Voice generation was cancelled.",
                409,
                retryable=False,
            )
        if control.deadline_reached():
            raise AppError(
                "TTS_PROVIDER_TIMEOUT",
                "Voice provider deadline was reached before synthesis.",
                504,
                retryable=True,
            )

    def poll(
        self,
        handle: VoiceProviderHandle,
        control: VoiceExecutionControl,
    ) -> VoiceProviderPollResult:
        raise AppError(
            "VOICE_PROVIDER_POLL_UNSUPPORTED",
            "ElevenLabs synchronous TTS cannot be polled.",
            409,
            retryable=False,
        )

    def reconcile_submission(
        self,
        request: VoiceSynthesisRequest,
        handle: VoiceProviderHandle | None,
        control: VoiceExecutionControl,
    ) -> VoiceProviderReconcileResult:
        _ = request, handle, control
        return VoiceProviderReconcileResult(status="ambiguous")

    def cancel(
        self,
        handle: VoiceProviderHandle | None,
        control: VoiceExecutionControl,
    ) -> VoiceProviderCancelResult:
        _ = handle, control
        return VoiceProviderCancelResult(status="unsupported")

    def _list_voices_for_action(
        self,
        profile: VoiceProviderProfile | None,
        *,
        action_context: _ElevenLabsActionContext,
        client: ElevenLabsHttpClient,
    ) -> tuple[VoiceProviderVoice, ...]:
        options = parse_elevenlabs_provider_options((profile.provider_options if profile else None) or {})
        raw_voices = self._cached_voices(
            options,
            action_context=action_context,
            client=client,
        )
        voices: list[VoiceProviderVoice] = []
        for item in raw_voices:
            if not isinstance(item, dict):
                raise self._catalog_invalid("ElevenLabs voice catalog contains a malformed entry.")
            voice_id = item.get("voice_id")
            name = item.get("name")
            if not isinstance(voice_id, str) or not voice_id.strip() or not isinstance(name, str) or not name.strip():
                raise self._catalog_invalid("ElevenLabs voice catalog contains an incomplete voice entry.")
            voices.append(
                VoiceProviderVoice(
                    voice_id=voice_id.strip(),
                    display_name=name.strip(),
                    language=options.narration_language,
                )
            )
        return tuple(voices)

    def _cached_voices(
        self,
        options: ElevenLabsProviderOptions,
        *,
        action_context: _ElevenLabsActionContext,
        client: ElevenLabsHttpClient,
    ) -> tuple[Any, ...]:
        key = (*self._cache_identity(action_context), options.narration_language)
        cached = self._read_cache(self._voice_cache, key)
        if cached is not None:
            return cached
        voices = self._fetch_all_voices(client)
        self._write_cache(
            self._voice_cache,
            key,
            voices,
            self._VOICE_CACHE_SECONDS,
        )
        return voices

    def _fetch_all_voices(self, client: ElevenLabsHttpClient) -> tuple[Any, ...]:
        voices: list[Any] = []
        seen_tokens: set[str] = set()
        next_page_token: str | None = None
        for _page_index in range(self._MAX_CATALOG_PAGES):
            response = client.list_voices(next_page_token=next_page_token)
            payload = response.payload
            if not isinstance(payload, dict) or not isinstance(payload.get("voices"), list):
                raise self._catalog_invalid(
                    "ElevenLabs voice catalog payload was malformed."
                )
            voices.extend(payload["voices"])
            has_more = payload.get("has_more")
            if has_more is False:
                return tuple(voices)
            if has_more is not True:
                raise self._catalog_invalid(
                    "ElevenLabs voice catalog pagination was malformed."
                )
            token = payload.get("next_page_token")
            if not isinstance(token, str) or not token.strip() or token in seen_tokens:
                raise self._catalog_invalid(
                    "ElevenLabs voice catalog pagination token was malformed."
                )
            seen_tokens.add(token)
            next_page_token = token.strip()
        raise self._catalog_invalid(
            "ElevenLabs voice catalog pagination exceeded the safety limit."
        )

    def _model(
        self,
        model_id: str,
        *,
        action_context: _ElevenLabsActionContext,
        client: ElevenLabsHttpClient,
    ) -> dict[str, Any]:
        key = self._cache_identity(action_context)
        cached = self._read_cache(self._model_cache, key)
        models: tuple[dict[str, Any], ...]
        if cached is None:
            response = client.list_models()
            if not isinstance(response.payload, list):
                raise AppError(
                    "ELEVENLABS_MODELS_INVALID",
                    "ElevenLabs model discovery returned a malformed payload.",
                    502,
                    retryable=True,
                )
            models = tuple(item for item in response.payload if isinstance(item, dict))
            self._write_cache(
                self._model_cache,
                key,
                models,
                self._MODEL_CACHE_SECONDS,
            )
        else:
            models = cached
        model = next(
            (item for item in models if item.get("model_id") == model_id),
            None,
        )
        if model is None:
            raise AppError(
                "ELEVENLABS_MODEL_UNAVAILABLE",
                "The selected ElevenLabs model is not available.",
                409,
                retryable=False,
            )
        return model

    @staticmethod
    def _live_language_supported(
        model: dict[str, Any],
        language: str,
    ) -> bool:
        raw = model.get("languages")
        if raw is None:
            return True
        if not isinstance(raw, list):
            return False
        live_codes = {
            str(item.get("language_id") or item.get("language") or "")
            .strip()
            .lower()
            for item in raw
            if isinstance(item, dict)
        }
        live_codes.discard("")
        return not live_codes or language in live_codes

    def _decode_pcm(
        self,
        payload: dict[str, Any],
        *,
        duration_text: str,
    ) -> bytes:
        _ = duration_text
        audio_base64 = payload.get("audio_base64")
        if not isinstance(audio_base64, str) or not audio_base64:
            raise AppError(
                "VOICE_PROVIDER_AUDIO_INVALID",
                "ElevenLabs audio payload was missing or empty.",
                422,
                retryable=False,
            )
        try:
            pcm = base64.b64decode(audio_base64.encode("ascii"), validate=True)
        except (ValueError, UnicodeError) as exc:
            raise AppError(
                "VOICE_PROVIDER_AUDIO_INVALID",
                "ElevenLabs audio payload was invalid.",
                422,
                retryable=False,
            ) from exc
        if not pcm or len(pcm) % 2 != 0:
            raise AppError(
                "VOICE_PROVIDER_AUDIO_INVALID",
                "ElevenLabs PCM audio was empty or malformed.",
                422,
                retryable=False,
            )
        if len(pcm) > self.settings.tts_provider_max_output_bytes:
            raise AppError(
                "VOICE_PROVIDER_AUDIO_INVALID",
                "ElevenLabs audio exceeds the configured size limit.",
                422,
                retryable=False,
            )
        return pcm

    def _parse_alignment(
        self,
        alignment: Any,
        *,
        exact_text: str,
        duration_seconds: float,
    ) -> list[dict[str, Any]]:
        if not isinstance(alignment, dict):
            raise AppError(
                "VOICE_PROVIDER_TIMING_INVALID",
                "ElevenLabs alignment payload was missing.",
                422,
                retryable=False,
            )
        chars = alignment.get("characters")
        starts = alignment.get("character_start_times_seconds")
        ends = alignment.get("character_end_times_seconds")
        if (
            not isinstance(chars, list)
            or not isinstance(starts, list)
            or not isinstance(ends, list)
            or len(chars) != len(starts)
            or len(chars) != len(ends)
        ):
            raise AppError(
                "VOICE_PROVIDER_TIMING_INVALID",
                "ElevenLabs alignment arrays were malformed.",
                422,
                retryable=False,
            )
        if not chars and exact_text:
            raise AppError(
                "VOICE_PROVIDER_TIMING_INVALID",
                "ElevenLabs alignment was empty.",
                422,
                retryable=False,
            )
        timings: list[dict[str, Any]] = []
        cursor = 0.0
        rebuilt: list[str] = []
        for character, start, end in zip(chars, starts, ends, strict=True):
            if (
                not isinstance(character, str)
                or not isinstance(start, (int, float))
                or not isinstance(end, (int, float))
            ):
                raise AppError(
                    "VOICE_PROVIDER_TIMING_INVALID",
                    "ElevenLabs alignment fields were malformed.",
                    422,
                    retryable=False,
                )
            start_f = float(start)
            end_f = float(end)
            if (
                not math.isfinite(start_f)
                or not math.isfinite(end_f)
                or start_f < cursor
                or end_f < start_f
                or start_f < 0
                or end_f > duration_seconds + 1e-6
            ):
                raise AppError(
                    "VOICE_PROVIDER_TIMING_INVALID",
                    "ElevenLabs alignment timestamps were invalid.",
                    422,
                    retryable=False,
                )
            rebuilt.append(character)
            timings.append(
                {
                    "text": character,
                    "start_seconds": start_f,
                    "end_seconds": end_f,
                }
            )
            cursor = end_f
        if "".join(rebuilt) != exact_text:
            raise AppError(
                "VOICE_PROVIDER_TIMING_INVALID",
                "ElevenLabs alignment does not exactly cover the narration.",
                422,
                retryable=False,
            )
        return timings

    @staticmethod
    def _write_wav(path, pcm: bytes, *, sample_rate: int) -> None:
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            wav.writeframes(pcm)

    @staticmethod
    def _metadata(
        headers: Any,
        options: ElevenLabsProviderOptions,
    ) -> tuple[dict[str, Any], str | None]:
        request_id = headers.get("request-id")
        trace_id = headers.get("x-trace-id")
        raw_cost = headers.get("character-cost")
        payload: dict[str, Any] = {
            "model_id": options.model_id,
            "wire_output_format": options.policy.wire_output_format,
        }
        provider_task_id = (
            request_id.strip()
            if isinstance(request_id, str) and request_id.strip()
            else None
        )
        if provider_task_id:
            payload["request_id"] = provider_task_id
        if isinstance(trace_id, str) and trace_id.strip():
            payload["trace_id"] = trace_id.strip()
        if isinstance(raw_cost, str) and raw_cost.strip().isdigit():
            payload["character_cost"] = int(raw_cost.strip())
        return {"elevenlabs": payload}, provider_task_id

    def _action_context(
        self,
        expected_execution_identity: dict[str, Any] | None = None,
    ) -> _ElevenLabsActionContext:
        credential = self._credential_resolver.resolve()
        api_key = credential.api_key or ""
        if not api_key:
            raise AppError(
                "ELEVENLABS_API_KEY_NOT_CONFIGURED",
                "ElevenLabs API key is not configured.",
                409,
                retryable=False,
            )
        api_base_url = self._api_base_url()
        api_base_identity_material = self._api_base_identity_material(api_base_url)
        fingerprint = self._sha256_text(api_key)
        context = _ElevenLabsActionContext(
            api_key=api_key,
            credential_fingerprint_sha256=fingerprint,
            api_base_url=api_base_url,
            api_base_identity_material=api_base_identity_material,
        )
        if expected_execution_identity is None:
            return context

        expected_fingerprint = expected_execution_identity.get(
            "credential_fingerprint_sha256"
        )
        if not isinstance(expected_fingerprint, str) or not expected_fingerprint:
            raise AppError(
                "VOICE_PROVIDER_RUNTIME_IDENTITY_MISSING",
                "Voice operation is missing its provider credential identity.",
                409,
                retryable=False,
                details={"provider_key": self.provider_key},
            )
        expected_base = expected_execution_identity.get("api_base_identity_sha256")
        current_base = self._sha256_text(api_base_identity_material)
        if expected_fingerprint != fingerprint or (
            isinstance(expected_base, str)
            and expected_base
            and expected_base != current_base
        ):
            raise AppError(
                "VOICE_PROVIDER_RUNTIME_CHANGED",
                "The Voice provider runtime changed after this operation was created. Submit a new Voice generation operation.",
                409,
                retryable=False,
                details={"provider_key": self.provider_key},
            )
        return context

    def _client_for_action(
        self,
        action_context: _ElevenLabsActionContext,
    ) -> ElevenLabsHttpClient:
        max_response = max(
            self.settings.tts_provider_max_output_bytes * 2 + 1_048_576,
            1_048_576,
        )
        if self._client_factory is not None:
            return self._client_factory(
                action_context.api_key,
                action_context.api_base_url,
                max_response,
                float(self.settings.tts_provider_timeout_seconds),
            )
        return ElevenLabsHttpClient(
            api_key=action_context.api_key,
            api_base_url=action_context.api_base_url,
            max_response_bytes=max_response,
            timeout_seconds=float(self.settings.tts_provider_timeout_seconds),
        )

    def _profile_from_request(
        self,
        request: VoiceSynthesisRequest,
    ) -> VoiceProviderProfile:
        return VoiceProviderProfile(
            profile_id="request",
            provider_key=self.provider_key,
            enabled=True,
            display_name="ElevenLabs",
            voice_id=request.voice_id,
            language=request.language,
            speaking_rate=request.speaking_rate,
            requested_sample_rate_hz=request.requested_sample_rate_hz,
            requested_output_format=request.requested_output_format,
            provider_options=request.provider_options,
        )

    def _api_base_url(self) -> str:
        return (
            self.settings.elevenlabs_api_base_url.rstrip("/")
            or "https://api.elevenlabs.io"
        )

    @staticmethod
    def _api_base_identity_material(api_base_url: str) -> str:
        parsed = urlsplit(api_base_url)
        host = (parsed.hostname or "").lower()
        port = f":{parsed.port}" if parsed.port is not None else ""
        return f"{parsed.scheme.lower()}://{host}{port}{parsed.path.rstrip('/')}"

    @staticmethod
    def _cache_identity(
        action_context: _ElevenLabsActionContext,
    ) -> tuple[str, str]:
        return (
            action_context.api_base_identity_material,
            action_context.credential_fingerprint_sha256,
        )

    def _read_cache(
        self,
        cache: dict[Any, _CacheEntry],
        key: Any,
    ) -> Any | None:
        now = self._clock()
        with self._cache_lock:
            entry = cache.get(key)
            if entry is None:
                return None
            if entry.expires_at <= now:
                cache.pop(key, None)
                return None
            return entry.value

    def _write_cache(
        self,
        cache: dict[Any, _CacheEntry],
        key: Any,
        value: Any,
        ttl: float,
    ) -> None:
        with self._cache_lock:
            cache[key] = _CacheEntry(
                expires_at=self._clock() + ttl,
                value=value,
            )

    @staticmethod
    def _sha256_text(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _catalog_invalid(message: str) -> AppError:
        return AppError(
            "ELEVENLABS_VOICE_CATALOG_INVALID",
            message,
            502,
            retryable=True,
        )

    @staticmethod
    def _invalid_response(message: str) -> AppError:
        return AppError(
            "ELEVENLABS_RESPONSE_INVALID",
            message,
            502,
            retryable=False,
        )

    @staticmethod
    def _blocked(exc: AppError) -> VoiceProviderReadiness:
        return VoiceProviderReadiness(
            status="blocked",
            issue_code=exc.error_code,
            message=exc.message,
            details=dict(exc.details or {}),
        )

    @staticmethod
    def _blocked_code(
        issue_code: str,
        message: str,
    ) -> VoiceProviderReadiness:
        return VoiceProviderReadiness(
            status="blocked",
            issue_code=issue_code,
            message=message,
        )
