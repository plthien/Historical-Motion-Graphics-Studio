from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class RationalFrameRateModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    numerator: int = Field(ge=1, le=240000)
    denominator: int = Field(ge=1, le=1001)


class RenderProfileModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    display_name: str = Field(min_length=1, max_length=128)
    enabled: bool
    width: int = Field(ge=320, le=7680)
    height: int = Field(ge=240, le=4320)
    frame_rate: RationalFrameRateModel
    video_codec: Literal["libx264"]
    video_crf: int = Field(ge=0, le=51)
    video_preset: Literal["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"]
    pixel_format: Literal["yuv420p"]
    audio_codec: Literal["aac"]
    audio_bitrate_kbps: int = Field(ge=64, le=512)
    audio_sample_rate_hz: Literal[48000]
    audio_channels: Literal[2]
    subtitle_mode: Literal["embedded_mov_text", "sidecar_only"]
    faststart: Literal[True]
    deterministic_encoding: bool
    encoder_threads: int = Field(default=1, ge=1, le=16)
    clipping_policy: Literal["fail_on_detected_clipping"] = "fail_on_detected_clipping"

    @model_validator(mode="after")
    def validate_dimensions_and_fps(self) -> "RenderProfileModel":
        if self.width % 2 or self.height % 2:
            raise ValueError("Render width and height must be even.")
        fps = self.frame_rate.numerator / self.frame_rate.denominator
        if fps < 1 or fps > 120:
            raise ValueError("Frame rate must be between 1 and 120 fps.")
        return self


class RenderSettingsModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    active_profile_id: str = Field(min_length=1)
    profiles: list[RenderProfileModel] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_profile_ownership(self) -> "RenderSettingsModel":
        ids = [item.profile_id for item in self.profiles]
        if len(ids) != len(set(ids)):
            raise ValueError("Render profile identifiers must be unique.")
        if self.active_profile_id not in ids:
            raise ValueError("active_profile_id must reference an existing profile.")
        return self


class RenderReadinessIssueModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    error_code: str
    message: str
    capability: str | None = None


class RenderReadinessModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ready", "not_ready", "disabled"]
    profile_id: str | None = None
    ffmpeg_version: str | None = None
    ffprobe_version: str | None = None
    capabilities_sha256: str | None = None
    issues: list[RenderReadinessIssueModel]


class RenderSettingsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    settings: RenderSettingsModel
    readiness: RenderReadinessModel


class UpdateRenderSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    settings: RenderSettingsModel


class RenderJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    render_profile_id: str
    expected_assembly_plan_artifact_id: str
    expected_subtitle_srt_artifact_id: str
    expected_subtitle_vtt_artifact_id: str
    expected_final_video_artifact_id: str | None = None
    operation_type: Literal["render", "rerender", "retry"]


class RenderJobSubmissionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str
    render_operation_id: str
    job_id: str
    status: str
    render_profile_id: str


class RenderJobStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str
    render_operation_id: str
    job_id: str
    operation_type: str
    status: str
    progress: int = Field(ge=0, le=100)
    progress_frame: int = Field(ge=0)
    progress_time_units: int = Field(ge=0)
    attempt_no: int = Field(ge=1)
    retryable: bool
    error_code: str | None = None
    error_message: str | None = None


class RenderActiveOperationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    render_operation_id: str
    job_id: str
    operation_type: str
    status: str
    progress: int
    attempt_no: int


class FinalVideoMetadataModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_id: str
    sha256: str
    size_bytes: int = Field(gt=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    duration_seconds: float = Field(gt=0)
    video_codec: Literal["h264"]
    pixel_format: Literal["yuv420p"]
    frame_rate: RationalFrameRateModel
    frame_count: int = Field(gt=0)
    audio_codec: Literal["aac"]
    audio_sample_rate_hz: Literal[48000]
    audio_channels: Literal[2]
    subtitle_mode: Literal["embedded_mov_text", "sidecar_only"]
    subtitle_stream_count: int = Field(ge=0, le=1)
    faststart: bool
    decode_valid: bool


class RenderWorkspaceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str
    readiness: RenderReadinessModel
    current_assembly_plan_artifact_id: str | None
    current_subtitle_srt_artifact_id: str | None
    current_subtitle_vtt_artifact_id: str | None
    selected_render_profile_id: str
    active_operation: RenderActiveOperationModel | None
    current_final_video_artifact_id: str | None
    video_url: str | None
    vtt_url: str | None
    final_video: FinalVideoMetadataModel | None
    validation_summary: dict[str, Any] | None
    available_actions: list[str]
    errors: list[dict[str, Any]]

class RenderFrameIntervalModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scene_id: str
    beat_id: str
    beat_order: int = Field(ge=1)
    start_frame: int = Field(ge=0)
    end_frame: int = Field(gt=0)
    frame_count: int = Field(ge=1)
    source_start_time_units: int = Field(ge=0)
    source_end_time_units: int = Field(gt=0)
    boundary_error_time_units: int = Field(ge=0)


class RenderFrameQuantizationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["3.0"]
    quantizer_version: Literal["render_frame_quantizer_v1"]
    frame_rate: RationalFrameRateModel
    source_total_duration_time_units: int = Field(gt=0)
    total_frame_count: int = Field(gt=0)
    quantized_duration_time_units: int = Field(gt=0)
    total_drift_time_units: int
    max_boundary_error_time_units: int = Field(ge=0)
    intervals: list[RenderFrameIntervalModel] = Field(min_length=1)
    frame_quantization_sha256: str = Field(min_length=64, max_length=64)


class RenderCommandGraphModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["3.0"]
    compiler_version: Literal["ffmpeg_render_graph_v2"]
    profile: RenderProfileModel
    ordered_input_descriptors: list[dict[str, Any]] = Field(min_length=3)
    visual_nodes: list[dict[str, Any]] = Field(min_length=1)
    audio_nodes: dict[str, Any]
    subtitle_mapping: dict[str, Any]
    concat: dict[str, Any]
    output: dict[str, Any]
    command_graph_sha256: str = Field(min_length=64, max_length=64)


class RenderOperationIntegrityModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["2.0"]
    render_operation_id: str
    job_id: str
    project_id: str
    operation_type: Literal["render", "rerender", "retry"]
    status: Literal["queued", "running", "rendering", "probing", "validating", "finalizing", "completed", "failed", "cancelled", "superseded"]
    source_assembly_plan_artifact_id: str
    source_subtitle_srt_artifact_id: str
    source_subtitle_vtt_artifact_id: str
    expected_final_video_artifact_id: str | None
    render_profile_id: str
    render_profile_snapshot_sha256: str = Field(min_length=64, max_length=64)
    ffmpeg_capabilities_sha256: str = Field(min_length=64, max_length=64)
    command_graph_sha256: str = Field(min_length=64, max_length=64)
    frame_quantization_sha256: str = Field(min_length=64, max_length=64)
    attempt_no: int = Field(ge=1)
    progress_percent: int = Field(ge=0, le=100)


class RenderValidationReportModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["2.0"]
    valid: Literal[True]
    stream_metadata: dict[str, Any]
    durations: dict[str, float]
    faststart: Literal[True]
    decode_valid: Literal[True]
    sha256: str = Field(min_length=64, max_length=64)
    byte_count: int = Field(gt=0)
