from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from pathlib import Path
from typing import Any, Literal, Protocol

from app.api.errors import AppError

VoiceExecutionModel = Literal["blocking", "synchronous", "asynchronous"]
VoiceReadinessStatus = Literal["ready", "blocked", "degraded"]
VoiceSubmissionStatus = Literal["completed", "pending", "ambiguous"]
VoicePollStatus = Literal["pending", "completed"]
VoiceCancelStatus = Literal["cancelled", "accepted", "unsupported", "not_found"]
VoiceReconcileStatus = Literal["pending", "completed", "replay_safe", "ambiguous"]
VoiceProviderErrorKind = Literal["retryable", "non_retryable", "cancelled", "ambiguous_submission"]
VoicePacingControlModel = Literal["relative_multiplier", "native_wpm", "none"]
VoicePacingRecommendationStatus = Literal["supported", "unsupported"]


@dataclass(frozen=True, slots=True)
class VoiceProviderCapabilities:
    execution_model: VoiceExecutionModel
    supports_idempotency: bool = False
    supports_submission_lookup: bool = False
    supports_cancel: bool = False
    supports_execution_interrupt: bool = False
    supports_word_timing: bool = False
    supports_character_timing: bool = False
    supports_speaking_rate: bool = True
    pacing_control: VoicePacingControlModel = "relative_multiplier"
    pacing_value_range: tuple[float, float] | None = (0.5, 2.0)
    supports_language_selection: bool = True
    supported_output_formats: tuple[str, ...] = ("wav",)
    supported_sample_rates: tuple[int, ...] = (48_000,)
    max_input_characters: int | None = None


@dataclass(frozen=True, slots=True)
class VoiceProviderPacingDirective:
    control_model: VoicePacingControlModel
    value: float | None
    target_narration_wpm: int
    policy_version: str
    clamped: bool = False
    value_range: tuple[float, float] | None = None
    speed_response_exponent: float = 1.0
    response_model_source: str | None = None
    response_model_identity: str | None = None

    def __post_init__(self) -> None:
        if not self.policy_version.strip():
            raise ValueError("Voice provider pacing policy version must not be empty.")
        if self.target_narration_wpm <= 0:
            raise ValueError("Voice target narration WPM must be positive.")
        if self.control_model == "none" and self.value is not None:
            raise ValueError("Voice pacing control 'none' cannot carry a provider value.")
        if self.control_model != "none" and self.value is None:
            raise ValueError("Voice provider pacing control requires a provider value.")
        if self.value_range is not None and self.value_range[0] > self.value_range[1]:
            raise ValueError("Voice provider pacing value range is invalid.")
        if not math.isfinite(float(self.speed_response_exponent)) or float(self.speed_response_exponent) <= 0:
            raise ValueError("Voice provider pacing directive speed response exponent must be positive and finite.")


@dataclass(frozen=True, slots=True)
class VoiceProviderPacingRequest:
    section_id: str
    target_narration_wpm: int
    planned_average_wpm: int
    base_native_speaking_rate: float
    actual_word_count: int | None = None
    target_duration_seconds: float | None = None
    fixed_duration_seconds: float = 0.0
    speed_response_exponent: float = 1.0
    response_model_source: str | None = None
    response_model_identity: str | None = None

    def __post_init__(self) -> None:
        if not self.section_id.strip():
            raise ValueError("Voice pacing request section_id must not be empty.")
        if self.target_narration_wpm <= 0 or self.planned_average_wpm <= 0:
            raise ValueError("Voice pacing request WPM values must be positive.")
        if not math.isfinite(float(self.base_native_speaking_rate)) or self.base_native_speaking_rate <= 0:
            raise ValueError("Voice base native speaking rate must be positive.")
        if self.actual_word_count is not None and self.actual_word_count <= 0:
            raise ValueError("Voice pacing request word count must be positive.")
        if self.target_duration_seconds is not None and (
            not math.isfinite(float(self.target_duration_seconds))
            or float(self.target_duration_seconds) <= 0
        ):
            raise ValueError("Voice pacing request target duration must be positive and finite.")
        if not math.isfinite(float(self.fixed_duration_seconds)) or float(self.fixed_duration_seconds) < 0:
            raise ValueError("Voice pacing request fixed duration must be finite and non-negative.")
        if not math.isfinite(float(self.speed_response_exponent)) or float(self.speed_response_exponent) <= 0:
            raise ValueError("Voice pacing request speed response exponent must be positive and finite.")


@dataclass(frozen=True, slots=True)
class VoiceProviderPacingObservation:
    section_id: str
    target_narration_wpm: int
    measured_raw_wpm: float
    current_control_model: VoicePacingControlModel
    current_provider_pacing_value: float | None
    provider_value_range: tuple[float, float] | None
    actual_word_count: int | None = None
    measured_raw_duration_seconds: float | None = None
    target_duration_seconds: float | None = None
    fixed_duration_seconds: float = 0.0
    speed_response_exponent: float = 1.0
    response_model_source: str | None = None
    response_model_identity: str | None = None
    response_lineage_sha256: str | None = None

    def __post_init__(self) -> None:
        if not self.section_id.strip():
            raise ValueError("Voice pacing observation section_id must not be empty.")
        if (
            self.target_narration_wpm <= 0
            or not math.isfinite(float(self.measured_raw_wpm))
            or self.measured_raw_wpm <= 0
        ):
            raise ValueError("Voice pacing observation WPM values must be positive.")
        if self.current_control_model == "none" and self.current_provider_pacing_value is not None:
            raise ValueError("Voice pacing observation with no control cannot carry a provider value.")
        if self.current_control_model != "none" and self.current_provider_pacing_value is None:
            raise ValueError("Voice pacing observation requires the current provider value.")
        if self.current_provider_pacing_value is not None and not math.isfinite(float(self.current_provider_pacing_value)):
            raise ValueError("Voice pacing observation current provider value must be finite.")
        if self.provider_value_range is not None and self.provider_value_range[0] > self.provider_value_range[1]:
            raise ValueError("Voice pacing observation provider range is invalid.")
        if self.actual_word_count is not None and self.actual_word_count <= 0:
            raise ValueError("Voice pacing observation word count must be positive.")
        if self.measured_raw_duration_seconds is not None and (
            not math.isfinite(float(self.measured_raw_duration_seconds))
            or float(self.measured_raw_duration_seconds) <= 0
        ):
            raise ValueError("Voice pacing observation measured duration must be positive and finite.")
        if self.target_duration_seconds is not None and (
            not math.isfinite(float(self.target_duration_seconds))
            or float(self.target_duration_seconds) <= 0
        ):
            raise ValueError("Voice pacing observation target duration must be positive and finite.")
        if not math.isfinite(float(self.fixed_duration_seconds)) or float(self.fixed_duration_seconds) < 0:
            raise ValueError("Voice pacing observation fixed duration must be finite and non-negative.")
        if not math.isfinite(float(self.speed_response_exponent)) or float(self.speed_response_exponent) <= 0:
            raise ValueError("Voice pacing observation speed response exponent must be positive and finite.")
        if self.response_lineage_sha256 is not None and len(self.response_lineage_sha256) != 64:
            raise ValueError("Voice pacing observation response lineage must be a sha256 hex digest.")


@dataclass(frozen=True, slots=True)
class VoiceProviderPacingRecommendation:
    status: VoicePacingRecommendationStatus
    control_model: VoicePacingControlModel
    recommended_value: float | None
    policy_version: str
    clamped: bool = False
    value_range: tuple[float, float] | None = None
    reason: str | None = None
    speed_response_exponent_used: float | None = None
    response_model_source: str | None = None
    response_model_identity: str | None = None

    def __post_init__(self) -> None:
        if not self.policy_version.strip():
            raise ValueError("Voice provider pacing recommendation policy version must not be empty.")
        if self.status == "unsupported" and self.recommended_value is not None:
            raise ValueError("Unsupported Voice pacing recommendation cannot carry a provider value.")
        if self.status == "supported" and self.control_model != "none" and self.recommended_value is None:
            raise ValueError("Supported Voice pacing recommendation requires a provider value.")
        if self.control_model == "none" and self.recommended_value is not None:
            raise ValueError("Voice pacing control 'none' cannot carry a recommended provider value.")
        if self.value_range is not None and self.value_range[0] > self.value_range[1]:
            raise ValueError("Voice provider pacing recommendation range is invalid.")
        if self.speed_response_exponent_used is not None and (
            not math.isfinite(float(self.speed_response_exponent_used))
            or float(self.speed_response_exponent_used) <= 0
        ):
            raise ValueError("Voice provider pacing recommendation speed response exponent must be positive and finite.")


@dataclass(frozen=True, slots=True)
class VoiceProviderVoice:
    voice_id: str
    display_name: str
    language: str


@dataclass(frozen=True, slots=True)
class VoiceProviderProfile:
    profile_id: str
    provider_key: str
    enabled: bool
    display_name: str
    voice_id: str
    language: str
    speaking_rate: float
    requested_sample_rate_hz: int
    requested_output_format: str
    provider_options: dict[str, Any] = field(default_factory=dict)
    maximum_segment_characters: int | None = None


@dataclass(frozen=True, slots=True)
class VoiceProviderReadiness:
    status: VoiceReadinessStatus
    issue_code: str | None = None
    message: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def ready(self) -> bool:
        return self.status == "ready"


@dataclass(frozen=True, slots=True)
class VoiceSynthesisRequest:
    operation_id: str
    segment_task_id: str
    execution_attempt_token: str
    idempotency_key: str
    exact_text: str
    voice_id: str
    language: str
    speaking_rate: float
    requested_sample_rate_hz: int
    requested_output_format: str
    provider_options: dict[str, Any]
    attempt_workspace: Path
    previous_text: str | None = None
    next_text: str | None = None
    pacing: VoiceProviderPacingDirective | None = None

    @property
    def attempt_output_path(self) -> Path:
        output_format = self.requested_output_format.lower()
        suffix = ".wav" if output_format in {"wav", "wav_pcm_s16le"} else f".{output_format}"
        return self.attempt_workspace / f"voice{suffix}"


@dataclass(frozen=True, slots=True)
class VoiceProviderHandle:
    provider_key: str
    provider_task_id: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class VoiceProviderExecutionResult:
    operation_id: str
    segment_task_id: str
    execution_attempt_token: str
    provider_key: str
    audio_file_path: Path
    source_mime_type: str
    sample_rate_hz: int
    channels: int
    sample_width_bytes: int
    duration_seconds: float
    provider_task_id: str | None = None
    provider_word_timings: list[dict[str, Any]] | None = None
    provider_character_timings: list[dict[str, Any]] | None = None
    sanitized_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class VoiceProviderSubmission:
    status: VoiceSubmissionStatus
    handle: VoiceProviderHandle | None = None
    result: VoiceProviderExecutionResult | None = None

    def __post_init__(self) -> None:
        if self.status == "completed" and self.result is None:
            raise ValueError("Completed provider submission requires a result.")
        if self.status == "pending" and self.handle is None:
            raise ValueError("Pending provider submission requires a handle.")
        if self.status == "ambiguous" and (self.handle is not None or self.result is not None):
            raise ValueError("Ambiguous provider submission must not include a handle or result.")


@dataclass(frozen=True, slots=True)
class VoiceProviderPollResult:
    status: VoicePollStatus
    handle: VoiceProviderHandle
    result: VoiceProviderExecutionResult | None = None

    def __post_init__(self) -> None:
        if self.status == "completed" and self.result is None:
            raise ValueError("Completed provider poll requires a result.")


@dataclass(frozen=True, slots=True)
class VoiceProviderCancelResult:
    status: VoiceCancelStatus
    message: str | None = None


@dataclass(frozen=True, slots=True)
class VoiceProviderReconcileResult:
    status: VoiceReconcileStatus
    handle: VoiceProviderHandle | None = None
    result: VoiceProviderExecutionResult | None = None
    message: str | None = None

    def __post_init__(self) -> None:
        if self.status == "pending" and self.handle is None:
            raise ValueError("Pending reconciliation requires a provider handle.")
        if self.status == "completed" and self.result is None:
            raise ValueError("Completed reconciliation requires a provider result.")


class VoiceExecutionControl(Protocol):
    @property
    def execution_attempt_token(self) -> str: ...

    def cancellation_requested(self) -> bool: ...

    def deadline_reached(self) -> bool: ...


class VoiceProviderAdapter(Protocol):
    @property
    def provider_key(self) -> str: ...

    def capabilities(self) -> VoiceProviderCapabilities: ...

    def list_voices(self, profile: VoiceProviderProfile | None = None) -> tuple[VoiceProviderVoice, ...]: ...

    def readiness(self, profile: VoiceProviderProfile) -> VoiceProviderReadiness: ...

    def execution_identity(self, profile: VoiceProviderProfile) -> dict[str, Any]: ...

    def pacing_directive(self, request: VoiceProviderPacingRequest) -> VoiceProviderPacingDirective: ...

    def recommend_pacing(self, observation: VoiceProviderPacingObservation) -> VoiceProviderPacingRecommendation: ...

    def submit(self, request: VoiceSynthesisRequest, control: VoiceExecutionControl) -> VoiceProviderSubmission: ...

    def poll(self, handle: VoiceProviderHandle, control: VoiceExecutionControl) -> VoiceProviderPollResult: ...

    def reconcile_submission(
        self,
        request: VoiceSynthesisRequest,
        handle: VoiceProviderHandle | None,
        control: VoiceExecutionControl,
    ) -> VoiceProviderReconcileResult: ...

    def interrupt_execution(
        self,
        *,
        segment_task_id: str,
        execution_attempt_token: str,
        reason: Literal["cancelled", "timeout"],
    ) -> bool: ...

    def cancel(self, handle: VoiceProviderHandle | None, control: VoiceExecutionControl) -> VoiceProviderCancelResult: ...


class VoiceProviderRegistry:
    """Provider-neutral adapter registry.

    Durable orchestration resolves adapters only by provider_key. Provider-specific
    branching belongs in adapter construction, never in workflow state transitions.
    """

    def __init__(self) -> None:
        self._adapters: dict[str, VoiceProviderAdapter] = {}

    def register(self, adapter: VoiceProviderAdapter) -> None:
        key = adapter.provider_key.strip()
        if not key:
            raise ValueError("Voice provider_key must not be empty.")
        if key in self._adapters:
            raise ValueError(f"Voice provider already registered: {key}")
        self._adapters[key] = adapter

    def resolve(self, provider_key: str) -> VoiceProviderAdapter:
        adapter = self._adapters.get(provider_key)
        if adapter is None:
            raise AppError(
                "VOICE_PROVIDER_UNKNOWN",
                "The configured Voice provider is not registered.",
                409,
                retryable=False,
                details={"provider_key": provider_key},
            )
        return adapter

    def capabilities(self, provider_key: str) -> VoiceProviderCapabilities:
        return self.resolve(provider_key).capabilities()

    def pacing_directive(
        self,
        provider_key: str,
        request: VoiceProviderPacingRequest,
    ) -> VoiceProviderPacingDirective:
        capabilities = self.capabilities(provider_key)
        directive = self.resolve(provider_key).pacing_directive(request)
        if not isinstance(directive, VoiceProviderPacingDirective):
            raise AppError(
                "VOICE_PROVIDER_PACING_INVALID",
                "The Voice provider returned an invalid pacing directive.",
                502,
                retryable=True,
                details={"provider_key": provider_key},
            )
        if directive.control_model != capabilities.pacing_control:
            raise AppError(
                "VOICE_PROVIDER_PACING_INVALID",
                "The Voice provider pacing directive does not match its declared control model.",
                502,
                retryable=True,
                details={"provider_key": provider_key},
            )
        if directive.target_narration_wpm != request.target_narration_wpm:
            raise AppError(
                "VOICE_PROVIDER_PACING_INVALID",
                "The Voice provider pacing directive changed the requested narration WPM.",
                502,
                retryable=True,
                details={"provider_key": provider_key},
            )
        expected_range = capabilities.pacing_value_range
        actual_range = directive.value_range
        range_matches = (
            actual_range is None
            if expected_range is None
            else actual_range is not None
            and tuple(float(value) for value in actual_range) == tuple(float(value) for value in expected_range)
        )
        if not range_matches:
            raise AppError(
                "VOICE_PROVIDER_PACING_INVALID",
                "The Voice provider pacing directive range does not match its declared capability.",
                502,
                retryable=True,
                details={"provider_key": provider_key},
            )
        if directive.value is not None and expected_range is not None:
            minimum, maximum = (float(expected_range[0]), float(expected_range[1]))
            if not minimum <= float(directive.value) <= maximum:
                raise AppError(
                    "VOICE_PROVIDER_PACING_INVALID",
                    "The Voice provider pacing directive is outside its declared capability range.",
                    502,
                    retryable=True,
                    details={"provider_key": provider_key},
                )
        if capabilities.pacing_control == "none" and (directive.value is not None or directive.clamped):
            raise AppError(
                "VOICE_PROVIDER_PACING_INVALID",
                "A Voice provider without native pacing control returned a native pacing value.",
                502,
                retryable=True,
                details={"provider_key": provider_key},
            )
        return directive

    def recommend_pacing(
        self,
        provider_key: str,
        observation: VoiceProviderPacingObservation,
    ) -> VoiceProviderPacingRecommendation:
        capabilities = self.capabilities(provider_key)
        recommendation = self.resolve(provider_key).recommend_pacing(observation)
        if not isinstance(recommendation, VoiceProviderPacingRecommendation):
            raise AppError(
                "VOICE_PROVIDER_PACING_RECOMMENDATION_INVALID",
                "The Voice provider returned an invalid pacing recommendation.",
                502,
                retryable=True,
                details={"provider_key": provider_key},
            )
        if recommendation.control_model != capabilities.pacing_control:
            raise AppError(
                "VOICE_PROVIDER_PACING_RECOMMENDATION_INVALID",
                "The Voice provider pacing recommendation does not match its declared control model.",
                502,
                retryable=True,
                details={"provider_key": provider_key},
            )
        expected_range = capabilities.pacing_value_range
        actual_range = recommendation.value_range
        range_matches = (
            actual_range is None
            if expected_range is None
            else actual_range is not None
            and tuple(float(value) for value in actual_range) == tuple(float(value) for value in expected_range)
        )
        if not range_matches:
            raise AppError(
                "VOICE_PROVIDER_PACING_RECOMMENDATION_INVALID",
                "The Voice provider pacing recommendation range does not match its declared capability.",
                502,
                retryable=True,
                details={"provider_key": provider_key},
            )
        if recommendation.recommended_value is not None and expected_range is not None:
            minimum, maximum = (float(expected_range[0]), float(expected_range[1]))
            if not minimum <= float(recommendation.recommended_value) <= maximum:
                raise AppError(
                    "VOICE_PROVIDER_PACING_RECOMMENDATION_INVALID",
                    "The Voice provider pacing recommendation is outside its declared capability range.",
                    502,
                    retryable=True,
                    details={"provider_key": provider_key},
                )
        if capabilities.pacing_control == "none" and (recommendation.recommended_value is not None or recommendation.clamped):
            raise AppError(
                "VOICE_PROVIDER_PACING_RECOMMENDATION_INVALID",
                "A Voice provider without native pacing control returned a native pacing recommendation.",
                502,
                retryable=True,
                details={"provider_key": provider_key},
            )
        return recommendation

    def list_voices(
        self,
        provider_key: str,
        profile: VoiceProviderProfile | None = None,
    ) -> tuple[VoiceProviderVoice, ...]:
        voices = self.resolve(provider_key).list_voices(profile)
        if not isinstance(voices, tuple):
            raise self._catalog_invalid(
                provider_key,
                "The Voice provider returned an invalid voice catalog.",
            )
        ids: list[str] = []
        for voice in voices:
            if not isinstance(voice, VoiceProviderVoice):
                raise self._catalog_invalid(
                    provider_key,
                    "The Voice provider returned a malformed voice descriptor.",
                )
            if (
                not isinstance(voice.voice_id, str)
                or not isinstance(voice.display_name, str)
                or not isinstance(voice.language, str)
            ):
                raise self._catalog_invalid(
                    provider_key,
                    "The Voice provider returned a malformed voice descriptor.",
                )
            if not voice.voice_id.strip() or not voice.display_name.strip() or not voice.language.strip():
                raise self._catalog_invalid(
                    provider_key,
                    "The Voice provider returned an incomplete voice descriptor.",
                )
            ids.append(voice.voice_id)
        if len(ids) != len(set(ids)):
            raise self._catalog_invalid(
                provider_key,
                "The Voice provider returned duplicate voice IDs.",
            )
        return voices

    @staticmethod
    def _catalog_invalid(provider_key: str, message: str) -> AppError:
        return AppError(
            "VOICE_PROVIDER_CATALOG_INVALID",
            message,
            502,
            retryable=True,
            details={"provider_key": provider_key},
        )

    def readiness(self, profile: VoiceProviderProfile) -> VoiceProviderReadiness:
        if not profile.enabled:
            return VoiceProviderReadiness(
                status="blocked",
                issue_code="VOICE_PROVIDER_PROFILE_DISABLED",
                message="The Voice provider profile is disabled.",
            )
        return self.resolve(profile.provider_key).readiness(profile)

    def execution_identity(self, profile: VoiceProviderProfile) -> dict[str, Any]:
        if not profile.enabled:
            raise AppError(
                "VOICE_PROVIDER_PROFILE_DISABLED",
                "The Voice provider profile is disabled.",
                409,
                retryable=False,
                details={"provider_key": profile.provider_key},
            )
        identity = self.resolve(profile.provider_key).execution_identity(profile)
        if not isinstance(identity, dict) or not identity:
            raise AppError(
                "VOICE_PROVIDER_EXECUTION_IDENTITY_INVALID",
                "The Voice provider returned an invalid execution identity.",
                502,
                retryable=False,
                details={"provider_key": profile.provider_key},
            )
        try:
            rendered = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
            normalized = json.loads(rendered)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AppError(
                "VOICE_PROVIDER_EXECUTION_IDENTITY_INVALID",
                "The Voice provider returned an invalid execution identity.",
                502,
                retryable=False,
                details={"provider_key": profile.provider_key},
            ) from exc
        if not isinstance(normalized, dict):
            raise AppError(
                "VOICE_PROVIDER_EXECUTION_IDENTITY_INVALID",
                "The Voice provider returned an invalid execution identity.",
                502,
                retryable=False,
                details={"provider_key": profile.provider_key},
            )
        return normalized

    def provider_keys(self) -> tuple[str, ...]:
        return tuple(sorted(self._adapters))
