from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from app.core.ids import ARTIFACT_ID_PATTERN
from app.modules.v4.voice_first_policy import ACTIVE_VOICE_PRESETS


NonEmptyString = Annotated[str, StringConstraints(min_length=1)]


class SettingsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    settings: dict[str, Any]


class UpdateSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    settings: dict[str, Any]


class MediaProviderSettingsModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["glabs_webhook"]
    enabled: bool
    base_url: str
    api_key_configured: bool
    api_key: str | None = None
    default_image_model: Literal["nano_banana_2", "nano_banana_pro"]
    default_image_aspect_ratio: Literal["1:1", "3:4", "4:3", "9:16", "16:9"]
    default_upscale: list[Literal["2K", "4K"]] = []
    poll_interval_seconds: int
    max_parallel_tasks: int
    enabled_media_kinds: list[Literal["image"]]


class MediaProviderReadinessModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str
    issues: list[dict[str, Any]] = []
    provider: str | None = None
    enabled_media_kinds: list[str] = []


class MediaProviderSettingsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    settings: MediaProviderSettingsModel
    readiness: MediaProviderReadinessModel


class UpdateMediaProviderSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    settings: MediaProviderSettingsModel


class TestMediaProviderConnectionDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["glabs_webhook"]
    health_reachable: bool
    authentication_accepted: bool


class TestMediaProviderConnectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ready"]
    message: str
    details: TestMediaProviderConnectionDetails


class JobModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str
    job_type: str
    status: str
    progress: int
    artifact_type: str | None = None
    target_type: str | None = None
    target_id: str | None = None
    created_at: str
    updated_at: str
    error_code: str | None = None
    error_message: str | None = None


class JobListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[JobModel]
    page: int
    page_size: int
    total: int


class ArtifactModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_id: str
    logical_id: str
    version_no: int
    is_current: bool
    artifact_type: str
    artifact_role: str | None = None
    status: str
    created_at: str
    path: str | None = None


class ArtifactListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[ArtifactModel]
    page: int
    page_size: int
    total: int


class ArtifactDownloadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_id: str
    download_url: str
    expires_at: str | None = None
    sha256: str | None = None


class ChannelContextModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    language: str
    niche: str
    audience: str


class VisualIdentityModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    medium: list[str]
    linework: list[str]
    rendering: list[str]
    palette: list[str]
    shape_language: list[str]
    texture: list[str]
    editorial_language: list[str]

    @field_validator("medium", "linework", "rendering", "palette", "shape_language", "texture", "editorial_language")
    @classmethod
    def validate_line_array(cls, values: list[str]) -> list[str]:
        seen: set[str] = set()
        for value in values:
            if not value.strip():
                raise ValueError("visual style array items must not be blank")
            if value in seen:
                raise ValueError("visual style array items must be unique")
            seen.add(value)
        return values


class NarrationIdentityModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    voice: list[str]
    delivery: list[str]
    explanation_style: list[str]
    tone_boundaries: list[str]
    avoid: list[str]

    @field_validator("voice", "delivery", "explanation_style", "tone_boundaries", "avoid")
    @classmethod
    def validate_line_array(cls, values: list[str]) -> list[str]:
        seen: set[str] = set()
        for value in values:
            if not value.strip():
                raise ValueError("narration identity array items must not be blank")
            if value in seen:
                raise ValueError("narration identity array items must be unique")
            seen.add(value)
        return values


class EntityStyleModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    appearance: list[str]
    avoid: list[str]

    @field_validator("appearance", "avoid")
    @classmethod
    def validate_line_array(cls, values: list[str]) -> list[str]:
        seen: set[str] = set()
        for value in values:
            if not value.strip():
                raise ValueError("entity style array items must not be blank")
            if value in seen:
                raise ValueError("entity style array items must be unique")
            seen.add(value)
        return values


class EntityStylesModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    character: EntityStyleModel
    location: EntityStyleModel
    prop: EntityStyleModel
    recurring_visual_subject: EntityStyleModel


class VisualStyleSnapshotModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    visual_identity: VisualIdentityModel
    entity_styles: EntityStylesModel
    global_negative_constraints: list[str]

    @field_validator("global_negative_constraints")
    @classmethod
    def validate_global_negatives(cls, values: list[str]) -> list[str]:
        seen: set[str] = set()
        for value in values:
            if not value.strip():
                raise ValueError("global negative constraints must not contain blank items")
            if value in seen:
                raise ValueError("global negative constraints must be unique")
            seen.add(value)
        return values


class PacingHoldRangeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    min: float = Field(gt=0)
    max: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_order(self) -> "PacingHoldRangeModel":
        if self.max <= self.min:
            raise ValueError("pacing hold max must be greater than min")
        return self


class PacingDensityProfileModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    preferred_hold_seconds: PacingHoldRangeModel


class PacingModeProfileModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    high: PacingDensityProfileModel
    medium: PacingDensityProfileModel
    low: PacingDensityProfileModel


class ChannelPacingProfilesModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    fast: PacingModeProfileModel
    medium: PacingModeProfileModel
    slow: PacingModeProfileModel


class NarrationWpmRangeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    min: int = Field(strict=True, ge=80, le=300)
    max: int = Field(strict=True, ge=80, le=300)

    @model_validator(mode="after")
    def validate_range(self) -> "NarrationWpmRangeModel":
        if self.min >= self.max:
            raise ValueError("narration WPM range must satisfy min < max")
        return self


class ChannelDnaV3Model(VisualStyleSnapshotModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["3.0"]
    channel: ChannelContextModel
    narration_wpm: NarrationWpmRangeModel
    pacing_profiles: ChannelPacingProfilesModel
    narration_identity: NarrationIdentityModel


class ChannelModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    channel_id: str
    name: str
    language: str
    niche: str
    audience: str
    has_channel_dna: bool
    channel_context: ChannelContextModel | None = None
    created_at: str
    updated_at: str


class ChannelListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[ChannelModel]
    page: int
    page_size: int
    total: int


class CreateChannelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    language: str
    niche: str
    audience: str


class ChannelDnaValidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    channel_dna: dict[str, Any]


class ChannelDnaValidateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    valid: bool
    channel_dna: dict[str, Any]
    channel_context: ChannelContextModel


class ChannelDnaReadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    channel_id: str
    has_channel_dna: bool
    channel_dna: ChannelDnaV3Model | None = None
    updated_at: str | None = None


class ChannelVoiceCalibrationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stability: float = Field(ge=0.0, le=1.0)
    similarity_boost: float = Field(ge=0.0, le=1.0)
    use_speaker_boost: bool


class ChannelVoiceProfileModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1]
    provider: Literal["elevenlabs"]
    voice_id: str = Field(min_length=1)
    voice_kind: Literal["professional"]
    default_preset: Literal["economy", "quality_cloned_voice"]
    calibration: dict[Literal["economy", "quality_cloned_voice"], ChannelVoiceCalibrationModel]

    @model_validator(mode="after")
    def validate_active_preset_calibration(self) -> "ChannelVoiceProfileModel":
        expected = set(ACTIVE_VOICE_PRESETS)
        if set(self.calibration) != expected:
            raise ValueError("calibration must contain exactly the active Voice presets")
        return self


class ChannelVoiceProfileReadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    channel_id: str
    voice_profile: ChannelVoiceProfileModel | None = None
    updated_at: str | None = None


class ChannelVoiceProfilePutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    voice_profile: ChannelVoiceProfileModel | None = None


class ProjectModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str
    channel_id: str
    name: str
    description: str | None = None
    channel_context: ChannelContextModel
    channel_dna_narration_wpm: NarrationWpmRangeModel
    topic_ideas_artifact_id: str | None = None
    selected_topic_ideas_artifact_id: str | None = None
    selected_topic_idea_id: str | None = None
    script_artifact_id: str | None = None
    script_analysis_artifact_id: str | None = None
    style_bible_artifact_id: str | None = None
    scene_plan_artifact_id: str | None = None
    visual_plan_artifact_id: str | None = None
    prompt_bundle_artifact_id: str | None = None
    style_bible_visual_reference_set_artifact_id: str | None = None
    style_bible_visual_reference_approval_set_artifact_id: str | None = None
    scene_asset_manifest_artifact_id: str | None = None
    narration_audio_artifact_id: str | None = None
    narration_word_timing_map_artifact_id: str | None = None
    beat_timing_map_artifact_id: str | None = None
    assembly_plan_artifact_id: str | None = None
    subtitle_srt_artifact_id: str | None = None
    subtitle_vtt_artifact_id: str | None = None
    final_video_artifact_id: str | None = None
    created_at: str
    updated_at: str


class ProjectListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[ProjectModel]
    page: int
    page_size: int
    total: int


class CreateProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    channel_id: str
    name: str = Field(min_length=1)
    description: str | None = None


class GenerateTopicIdeasUserInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seed_topic: str
    topic_count: int = Field(default=10, ge=1, le=50)
    focus_keywords: list[str] = Field(default_factory=list)
    focus: Literal["highest_ctr", "most_evergreen", "easiest_to_make", "balanced", "most_unique"] = "balanced"
    avoid_topics: list[str] = Field(default_factory=list)


class GenerateProjectTopicIdeasRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_input: GenerateTopicIdeasUserInput


class TopicIdeaModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idea_id: str
    title: str
    core_question: str
    viewer_hook: str
    topic_angle: str
    tone: str
    ctr_score: int
    difficulty: Literal["easy", "medium", "hard"]
    content_tags: list[str]
    risk_notes: list[str]
    pov_style: str = Field(min_length=1)
    avoid: list[str]
    recommended_pacing_mode: Literal["fast", "medium", "slow"]
    pacing_mode_reason: str = Field(min_length=1, max_length=500)


class TopicIdeasChannelContextModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    language: str


class TopicIdeasArtifactModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_type: Literal["topic_ideas"]
    schema_version: Literal["1.0"]
    channel_context: TopicIdeasChannelContextModel
    source_user_input: GenerateTopicIdeasUserInput
    ideas: list[TopicIdeaModel]


class TopicIdeasArtifactResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact: TopicIdeasArtifactModel
    artifact_id: str


class TopicIdeasReadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    generated: bool
    artifact_id: str | None = None
    artifact: TopicIdeasArtifactModel | None = None


class GenerateScriptUserInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_duration_minutes: float = Field(ge=1)
    pov_style: str = Field(min_length=1)
    avoid: list[str]
    section_count_mode: Literal["llm_decides", "backend_plan"]
    pacing_mode: Literal["fast", "medium", "slow"]
    narration_wpm: NarrationWpmRangeModel


class SelectTopicRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topic_ideas_artifact_id: str = Field(min_length=1)
    idea_id: str = Field(pattern=r"^idea_[0-9]{3}$")


class GenerateProjectScriptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_input: GenerateScriptUserInput
    selected_topic_idea_id: str | None = None


class ScriptArtifactResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact: dict[str, Any]
    artifact_id: str


class ScriptReadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    generated: bool
    artifact_id: str | None = None
    artifact: dict[str, Any] | None = None


MediaTaskStatusLiteral = Literal["queued", "submitted", "provider_pending", "provider_running", "downloading", "completed", "failed", "canceled", "download_failed"]
MediaTaskReadEntityTypeLiteral = Literal["character", "location", "prop", "recurring_visual_subject", "beat"]


class MediaImageGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purpose: Literal["style_bible_reference_image"]
    project_id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_-]+$")
    style_bible_revision_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]+$")
    entity_type: Literal["character", "location", "prop", "recurring_visual_subject"]
    entity_id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_-]+$")
    candidate_count: int = Field(strict=True, ge=1, le=5)
    prompt: str = Field(min_length=1)
    negative_prompt: str = ""
    model: Literal["nano_banana_2", "nano_banana_pro"]
    aspect_ratio: Literal["1:1", "3:4", "4:3", "9:16", "16:9"]
    upscale: list[Literal["2K", "4K"]] = []


class MediaTaskSubmissionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    media_task_id: str
    status: Literal["queued", "submitted", "failed"]
    entity_type: Literal["character", "location", "prop", "recurring_visual_subject"]
    entity_id: str
    candidate_index: int


class MediaImageGenerateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    batch_id: str
    tasks: list[MediaTaskSubmissionModel]
    summary_url: str


class MediaTaskStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    media_task_id: str
    batch_id: str
    external_provider: str
    external_task_id: str | None = None
    status: MediaTaskStatusLiteral
    provider_status: Literal["pending", "running", "completed", "failed"] | None = None
    provider_submitted_at: str | None = None
    entity_type: MediaTaskReadEntityTypeLiteral
    entity_id: str
    candidate_index: int
    created_at: str
    updated_at: str
    poll_count: int
    next_poll_after_seconds: int


class MediaTaskResultItemModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_id: str
    image_id: str
    storage_uri: str
    file_url: str
    mime_type: str
    source_result_url: str
    downloaded_at: str


class MediaTaskResultResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    media_task_id: str
    status: MediaTaskStatusLiteral
    message: str | None = None
    results: list[MediaTaskResultItemModel] | None = Field(default=None, max_length=4)
    error_code: str | None = None
    provider_error_code: int | None = None
    error: str | None = None
    error_detail: str | None = None

    @field_validator("provider_error_code", mode="before")
    @classmethod
    def validate_provider_error_code(cls, value: object) -> int | None:
        if value is None:
            return None
        if type(value) is not int or (value != 0 and not 400 <= value <= 599):
            raise ValueError("provider_error_code must be 0, an integer from 400 to 599, or null")
        return value


class MediaTaskCardModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    media_task_id: str
    batch_id: str
    entity_type: MediaTaskReadEntityTypeLiteral
    entity_id: str
    candidate_index: int
    status: MediaTaskStatusLiteral
    provider_status: Literal["pending", "running", "completed", "failed"] | None = None
    provider_submitted_at: str | None = None
    thumbnail_url: str | None = None
    result_url: str
    prompt_preview: str
    model: str
    aspect_ratio: str
    selected: bool
    locked: bool
    created_at: str
    completed_at: str | None = None


class MediaTaskCardListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tasks: list[MediaTaskCardModel]


class MediaTaskSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    batch_id: str
    purpose: Literal["style_bible_visual_reference", "scene_asset_generation"]
    total: int
    queued: int
    submitted: int
    provider_pending: int
    provider_running: int
    downloading: int
    completed: int
    failed: int
    canceled: int
    download_failed: int
    selected: int
    locked: int
    updated_at: str


class MediaActiveBatchModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    batch_id: str
    purpose: str
    total: int
    active_task_count: int
    created_at: str
    updated_at: str


class MediaActiveBatchesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str
    active_batches: list[MediaActiveBatchModel]
    latest_batch_id: str | None = None
