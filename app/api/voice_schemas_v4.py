from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.modules.v4.voice_first_policy import VOICE_ARCHITECTURE_VERSION


class TTSVoiceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    voice_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    language: str = Field(min_length=1)


class TTSStoredProfileModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    enabled: bool
    display_name: str = Field(min_length=1)
    default_voice_id: str | None = None
    default_speaking_rate: float = Field(ge=0.5, le=2.0)
    sample_rate_hz: int = Field(
        gt=0,
        description="Sample rate requested from the selected provider. Internal Voice artifacts are normalized separately to the canonical 48 kHz contract.",
    )
    output_format: str = Field(
        min_length=1,
        description="Output format requested from the selected provider. Internal Voice artifacts are normalized separately to canonical mono PCM S16LE WAV.",
    )
    maximum_segment_characters: int = Field(ge=16, le=10000)
    provider_options: dict[str, Any] = Field(default_factory=dict)


class TTSProfileModel(TTSStoredProfileModel):
    voices: list[TTSVoiceModel]


class TTSStoredSettingsModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    active_profile_id: str = Field(min_length=1)
    profiles: list[TTSStoredProfileModel] = Field(min_length=1)


class TTSSettingsModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    active_profile_id: str = Field(min_length=1)
    profiles: list[TTSProfileModel] = Field(min_length=1)


class TTSReadinessModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ready", "not_configured"]
    issues: list[dict[str, str]]
    profile_id: str | None = None
    provider: str | None = None
    voice_id: str | None = None
    requires_ffmpeg: bool


class TTSSettingsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    settings: TTSSettingsModel
    readiness: TTSReadinessModel


class UpdateTTSSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    settings: TTSStoredSettingsModel


class VoiceGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_type: Literal["generate_all", "generate_missing"]
    expected_narration_audio_artifact_id: str | None = None
    expected_narration_word_timing_map_artifact_id: str | None = None
    generation_provider: Literal["elevenlabs", "kokoro_local"]
    generation_voice_id: str | None = None
    voice_preset: Literal["economy", "quality_cloned_voice"] | None = None

    @model_validator(mode="after")
    def validate_generation_identity(self) -> "VoiceGenerateRequest":
        if self.generation_provider == "elevenlabs":
            if self.voice_preset is None:
                raise ValueError("voice_preset is required for ElevenLabs generation.")
            if self.generation_voice_id is not None:
                raise ValueError("generation_voice_id must not replace the Channel PVC for ElevenLabs generation.")
        if self.generation_provider == "kokoro_local":
            if self.voice_preset is not None:
                raise ValueError("voice_preset must be null for Kokoro generation.")
            if not isinstance(self.generation_voice_id, str) or not self.generation_voice_id.strip():
                raise ValueError("generation_voice_id is required for Kokoro generation.")
        return self


class VoiceSectionRegenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_narration_audio_artifact_id: str | None = None
    expected_narration_word_timing_map_artifact_id: str | None = None
    source_remediation_operation_id: str = Field(min_length=1)
    expected_remediation_report_sha256: str = Field(min_length=64, max_length=64)


class VoiceApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_narration_audio_artifact_id: str = Field(min_length=1)
    candidate_narration_word_timing_map_artifact_id: str = Field(min_length=1)
    expected_narration_audio_artifact_id: str | None = None
    expected_narration_word_timing_map_artifact_id: str | None = None

    @model_validator(mode="after")
    def validate_expected_pair(self) -> "VoiceApproveRequest":
        if (self.expected_narration_audio_artifact_id is None) != (self.expected_narration_word_timing_map_artifact_id is None):
            raise ValueError("Expected approved Voice pointers must be both null or both set.")
        return self


class VoiceRealignCurrentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_narration_audio_artifact_id: str = Field(min_length=1)
    expected_narration_word_timing_map_artifact_id: str = Field(min_length=1)


class VoiceRealignCurrentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["realigned"]
    narration_audio_artifact_id: str
    previous_narration_word_timing_map_artifact_id: str
    narration_word_timing_map_artifact_id: str
    beat_timing_map_artifact_id: str | None
    preserved_scene_plan_artifact_id: str | None
    preserved_visual_plan_artifact_id: str | None
    preserved_prompt_bundle_artifact_id: str | None
    preserved_scene_asset_manifest_artifact_id: str | None


class VoiceJobSubmissionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str
    operation_id: str
    status: str
    segment_count: int
    submitted_count: int
    reused_count: int


class VoiceJobProblemModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    error_code: str
    message: str
    details: dict[str, Any] | list[Any] | str | int | float | bool | None = None


class VoiceJobStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str
    operation_id: str
    operation_type: str
    status: str
    progress: int
    segment_count: int
    completed_segment_count: int
    failed_segment_count: int
    error_code: str | None = None
    error_message: str | None = None
    current_segment_id: str | None = None
    provider: str | None = None
    action: str | None = None
    retry_count: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=0, ge=0)
    next_retry_at: str | None = None
    cancel_requested: bool = False
    problem: VoiceJobProblemModel | None = None


class VoiceReadinessIssueModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    error_code: str
    message: str
    cause_error_code: str | None = None


class VoiceReadinessModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ready", "not_ready"]
    issues: list[VoiceReadinessIssueModel]
    profile_id: str | None = None
    provider: str | None = None
    voice_id: str | None = None
    requires_ffmpeg: bool


class VoiceActiveOperationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str
    job_id: str
    status: Literal["queued", "running", "waiting_for_provider", "assembling", "finalizing"]
    operation_type: Literal["generate_all", "generate_missing", "regenerate_section"]
    target_section_id: str | None = None


class VoiceGenerationPresetStatusModel(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())
    ready: bool
    issue: VoiceReadinessIssueModel | None
    model_id: str = Field(min_length=1)


class VoiceGenerationPresetsModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    economy: VoiceGenerationPresetStatusModel
    quality_cloned_voice: VoiceGenerationPresetStatusModel


class VoiceGenerationProviderStatusModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    generation_provider: Literal["elevenlabs", "kokoro_local"]
    display_name: str = Field(min_length=1)
    ready: bool
    issue: VoiceReadinessIssueModel | None
    generation_ready: bool
    generation_issue: VoiceReadinessIssueModel | None
    generation_presets: VoiceGenerationPresetsModel | None = None
    alignment_ready: bool
    alignment_issue: VoiceReadinessIssueModel | None
    alignment_provider: Literal["elevenlabs"]


class VoiceSegmentWorkspaceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    segment_id: str
    segment_order: int
    section_id: str
    source_start: int
    source_end: int
    exact_text: str
    text_sha256: str
    status: str
    segment_task_id: str | None = None
    segment_audio_artifact_id: str | None = None
    duration_seconds: float | None = None
    sha256: str | None = None
    audio_url: str | None = None
    timing_source: str | None = None
    error: dict[str, Any] | None = None
    can_regenerate: bool


class VoiceSectionWorkspaceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    section_id: str
    section_order: int
    segment_ids: list[str]
    target_wpm: int | None = None
    raw_wpm: float | None = None
    pacing_status: Literal["pending", "pass", "regeneration_required"]
    correction_relative: float | None = None
    current_provider_pacing_value: float | None = None
    recommended_provider_pacing_value: float | None = None
    recommendation_policy_version: str | None = None
    recommendation_reason: str | None = None
    remediation_identity: str | None = Field(default=None, min_length=64, max_length=64)
    remediation_report_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    source_remediation_operation_id: str | None = None
    regeneration_supported: bool
    can_regenerate: bool
    can_accept_pacing_deviation: bool
    remediation_block_reason: str | None = None
    issue: dict[str, Any] | None = None


class VoiceWorkspaceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str
    readiness: VoiceReadinessModel
    script_artifact_id: str | None
    selected_tts_profile_id: str | None
    selected_generation_provider: Literal["elevenlabs", "kokoro_local"] | None = None
    selected_voice_id: str | None
    selected_voice_label: str | None = None
    speaking_rate: float | None
    available_generation_providers: list[VoiceGenerationProviderStatusModel] = Field(default_factory=list)
    kokoro_voice_catalog: list[dict[str, str]] = Field(default_factory=list)
    active_operation: VoiceActiveOperationModel | None
    current_narration_audio_artifact_id: str | None
    audio_duration_seconds: float | None
    audio_mime_type: str | None
    audio_sha256: str | None
    audio_url: str | None
    current_narration_word_timing_map_artifact_id: str | None
    draft_narration_audio_artifact_id: str | None = None
    draft_narration_word_timing_map_artifact_id: str | None = None
    draft_operation_id: str | None = None
    draft_generation_provider: Literal["elevenlabs", "kokoro_local"] | None = None
    draft_generation_voice_id: str | None = None
    draft_voice_preset: Literal["economy", "quality_cloned_voice"] | None = None
    draft_candidate_identity_sha256: str | None = None
    draft_audio_url: str | None = None
    alignment_status: Literal["queued", "submitting", "running", "completed", "submit_outcome_unknown", "failed"] | None = None
    alignment_completed_section_count: int = Field(default=0, ge=0)
    alignment_total_section_count: int = Field(default=0, ge=0)
    alignment_submit_outcome_unknown_section_ids: list[str] = Field(default_factory=list)
    timing_map_status: str | None
    timing_source: str | None
    current_output_problem: VoiceReadinessIssueModel | None = None
    sections: list[VoiceSectionWorkspaceModel]
    segments: list[VoiceSegmentWorkspaceModel]
    approval_eligible: bool
    available_actions: list[str]


class VoiceOperationIntegrityModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["4.0"]
    operation_id: str
    job_id: str
    project_id: str
    operation_type: Literal["generate_all", "generate_missing", "regenerate_section"]
    status: Literal["queued", "running", "waiting_for_provider", "assembling", "finalizing", "completed", "partial", "failed", "superseded", "cancelled"]
    script_artifact_id: str
    architecture_version: Literal["voice_generation_providers_v1"] = VOICE_ARCHITECTURE_VERSION
    expected_narration_audio_artifact_id: str | None
    expected_narration_word_timing_map_artifact_id: str | None
    voice_preset: Literal["economy", "quality_cloned_voice"] | None
    tts_profile_id: str
    voice_id: str
    provider_identity_sha256: str
    generation_settings_snapshot_sha256: str
    pacing_identity_sha256: str = Field(min_length=64, max_length=64)
    segmentation_version: Literal["narration_segmentation_v2"]
    segmentation_input_sha256: str
    segment_count: int = Field(ge=1)


class VoiceValidationReportModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    valid: bool
    statuses: dict[str, Literal["pass", "fail", "warning"]]
    warnings: list[str]
    errors: list[str]


class NarrationAudioMetadataModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_type: Literal["narration_audio"]
    schema_version: Literal["3.0"]
    project_id: str
    source_script_revision_id: str
    source_voice_operation_id: str
    tts_profile_id: str
    voice_id: str
    speaking_rate: float = Field(ge=0.5, le=2.0)
    provider_identity_sha256: str = Field(min_length=64, max_length=64)
    generation_settings_snapshot_sha256: str = Field(min_length=64, max_length=64)
    raw_synthesis_identity_sha256: str = Field(min_length=64, max_length=64)
    segmentation_version: Literal["narration_segmentation_v2"]
    pacing_policy_version: str
    pacing_identity_sha256: str = Field(min_length=64, max_length=64)
    script_section_pacing: dict[str, Any]
    ordered_segment_artifact_ids: list[str] = Field(min_length=1)
    ordered_source_segment_artifact_ids: list[str] = Field(min_length=1)
    codec: Literal["pcm_s16le"]
    sample_rate_hz: Literal[48000]
    channels: Literal[1]
    sample_width_bytes: Literal[2]
    sample_count: int = Field(ge=1)
    duration_seconds: float = Field(gt=0)
    sha256: str = Field(min_length=64, max_length=64)
