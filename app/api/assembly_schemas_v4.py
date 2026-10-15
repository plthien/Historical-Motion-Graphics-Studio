from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field


class TimebaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    units_per_second: Literal[1_000_000]
    unit: Literal["microsecond"]


class NormalizedCropModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    width: float = Field(gt=0.0, le=1.0)
    height: float = Field(gt=0.0, le=1.0)


class MotionKeyframeModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    offset: float = Field(ge=0.0, le=1.0)
    crop: NormalizedCropModel


class VisualInstructionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    compiler_version: Literal["assembly_visual_motion_v1"]
    fit_mode: Literal["cover"]
    anchor_x: float = Field(ge=0.0, le=1.0)
    anchor_y: float = Field(ge=0.0, le=1.0)
    start_crop: NormalizedCropModel
    end_crop: NormalizedCropModel
    motion_type: str
    motion_keyframes: list[MotionKeyframeModel]
    hold_mode: Literal["hold"]
    background_fill: Literal["technical_black"]
    source_camera_movement: str
    source_framing: str
    source_camera_angle: str


class TransitionInstructionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    compiler_version: Literal["assembly_transition_v1"]
    transition_type: Literal["cut", "crossfade"]
    duration_time_units: int = Field(ge=0)
    previous_visual_artifact_id: str | None
    next_visual_artifact_id: str | None
    transition_intent: str | None
    source_visual_hint: str | None
    destination_visual_hint: str | None


class SubtitleCueModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cue_id: str
    cue_order: int = Field(ge=1)
    source_start: int = Field(ge=0)
    source_end: int = Field(gt=0)
    exact_text: str = Field(min_length=1)
    start_time_units: int = Field(ge=0)
    end_time_units: int = Field(gt=0)
    duration_time_units: int = Field(gt=0)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    duration_seconds: float = Field(gt=0)
    line_1: str = Field(min_length=1)
    line_2: str | None
    word_ids: list[str] = Field(min_length=1)
    segment_ids: list[str]
    scene_ids: list[str]
    beat_ids: list[str]


class AssemblyBeatModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scene_id: str
    scene_order: int = Field(ge=1)
    beat_id: str
    beat_order: int = Field(ge=1)
    local_beat_order: int = Field(ge=1)
    continuity_group_id: str
    source_start: int = Field(ge=0)
    source_end: int = Field(gt=0)
    start_time_units: int = Field(ge=0)
    end_time_units: int = Field(gt=0)
    duration_time_units: int = Field(gt=0)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    duration_seconds: float = Field(gt=0)
    planned_duration_seconds: float = Field(gt=0)
    duration_delta_seconds: float
    selected_visual_artifact_id: str
    selected_visual_mime_type: str
    selected_visual_sha256: str
    selected_visual_width: int | None
    selected_visual_height: int | None
    visual_instruction: VisualInstructionModel
    transition_instruction: TransitionInstructionModel
    subtitle_cue_ids: list[str]
    source_scene_asset_entry_sha256: str
    source_narration_beat_timing_sha256: str


class AssemblySceneModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scene_id: str
    scene_order: int = Field(ge=1)
    source_start: int = Field(ge=0)
    source_end: int = Field(gt=0)
    start_time_units: int = Field(ge=0)
    end_time_units: int = Field(gt=0)
    duration_time_units: int = Field(gt=0)
    beat_ids: list[str] = Field(min_length=1)


class NarrationTrackModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_id: str
    mime_type: str
    sha256: str
    sample_rate_hz: Literal[48000]
    channels: Literal[1]
    sample_count: int = Field(ge=1)
    duration_time_units: int = Field(gt=0)
    duration_seconds: float = Field(gt=0)
    start_time_units: Literal[0]
    end_time_units: int = Field(gt=0)
    gain_db: Literal[0]


class BackgroundAudioBindingModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_id: str
    start_time_units: int = Field(ge=0)
    end_time_units: int = Field(gt=0)
    source_start_time_units: int = Field(ge=0)
    gain_db: float = Field(ge=-60, le=12)
    loop_mode: Literal["none", "loop"]
    fade_in_time_units: int = Field(ge=0)
    fade_out_time_units: int = Field(ge=0)


class SFXBindingModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sfx_id: str
    artifact_id: str
    anchor_type: Literal["absolute", "scene_start", "beat_start", "beat_end"]
    scene_id: str | None = None
    beat_id: str | None = None
    start_time_units: int = Field(ge=0)
    source_start_time_units: int = Field(ge=0)
    duration_time_units: int = Field(gt=0)
    gain_db: float = Field(ge=-60, le=12)


class BackgroundAudioTrackModel(BackgroundAudioBindingModel):
    validator_version: Literal["canonical_pcm_wav_artifact_bytes_v1"]
    mime_type: Literal["audio/wav"]
    codec: Literal["pcm_s16le"]
    container: Literal["wav"]
    sample_rate_hz: Literal[48000]
    channels: Literal[1]
    sample_width_bytes: Literal[2]
    sample_count: int = Field(ge=1)
    duration_seconds: float = Field(gt=0)
    byte_count: int = Field(gt=0)
    sha256: str = Field(min_length=64, max_length=64)


class SFXTrackModel(SFXBindingModel):
    validator_version: Literal["canonical_pcm_wav_artifact_bytes_v1"]
    mime_type: Literal["audio/wav"]
    codec: Literal["pcm_s16le"]
    container: Literal["wav"]
    sample_rate_hz: Literal[48000]
    channels: Literal[1]
    sample_width_bytes: Literal[2]
    sample_count: int = Field(ge=1)
    duration_seconds: float = Field(gt=0)
    byte_count: int = Field(gt=0)
    sha256: str = Field(min_length=64, max_length=64)


class AssemblyQCModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lineage_status: str
    scene_coverage_status: str
    beat_coverage_status: str
    visual_asset_status: str
    visual_timeline_status: str
    transition_status: str
    narration_track_status: str
    duration_status: str
    subtitle_text_coverage_status: str
    subtitle_timing_status: str
    srt_serialization_status: str
    vtt_serialization_status: str
    optional_audio_status: str
    publication_status: str
    warnings: list[str]
    errors: list[str]


class AssemblyPlanModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_type: Literal["assembly_plan"]
    schema_version: Literal["3.0"]
    project_id: str
    assembly_plan_revision_id: str
    parent_assembly_plan_revision_id: str | None
    revision_operation: Literal["build", "rebuild", "approval"]
    status: Literal["draft_ready", "approved"]
    source_script_artifact_id: str
    source_scene_plan_artifact_id: str
    source_scene_asset_manifest_artifact_id: str
    source_narration_audio_artifact_id: str
    source_narration_word_timing_map_artifact_id: str
    source_beat_timing_map_artifact_id: str
    source_subtitle_srt_artifact_id: str
    source_subtitle_vtt_artifact_id: str
    compiler_version: Literal["assembly_plan_compiler_v1"]
    compiler_input_sha256: str
    timeline_timebase: TimebaseModel
    total_duration_seconds: float = Field(gt=0)
    total_duration_time_units: int = Field(gt=0)
    visual_track: dict[str, Any]
    narration_track: NarrationTrackModel
    background_audio_track: BackgroundAudioTrackModel | None
    sfx_track: list[SFXTrackModel]
    subtitle_track: dict[str, Any]
    scenes: list[AssemblySceneModel] = Field(min_length=1)
    beats: list[AssemblyBeatModel] = Field(min_length=1)
    subtitle_cues: list[SubtitleCueModel] = Field(min_length=1)
    backend_qc: AssemblyQCModel
    created_at: str


class AssemblyBuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_assembly_plan_artifact_id: str | None = None
    expected_subtitle_srt_artifact_id: str | None = None
    expected_subtitle_vtt_artifact_id: str | None = None
    expected_scene_plan_artifact_id: str
    expected_scene_asset_manifest_artifact_id: str
    expected_narration_audio_artifact_id: str
    expected_narration_word_timing_map_artifact_id: str
    expected_beat_timing_map_artifact_id: str
    background_music: BackgroundAudioBindingModel | None = None
    sound_effects: list[SFXBindingModel] = []


class AssemblyApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_assembly_plan_artifact_id: str
    expected_subtitle_srt_artifact_id: str
    expected_subtitle_vtt_artifact_id: str


class AssemblyWorkspaceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str
    readiness: dict[str, Any]
    current_assembly_plan_artifact_id: str | None
    assembly_status: str | None
    current_subtitle_srt_artifact_id: str | None
    current_subtitle_vtt_artifact_id: str | None
    source_identities: dict[str, str | None]
    total_duration_seconds: float | None
    scene_count: int
    beat_count: int
    subtitle_cue_count: int
    visual_timeline_summary: dict[str, Any]
    narration_track_summary: dict[str, Any]
    optional_audio_summary: dict[str, Any]
    subtitle_summary: dict[str, Any]
    backend_qc: dict[str, Any]
    scenes: list[dict[str, Any]]
    beats: list[dict[str, Any]]
    subtitle_cues: list[dict[str, Any]]
    build_eligible: bool
    approval_eligible: bool
    available_actions: list[str]
    errors: list[dict[str, Any]]
