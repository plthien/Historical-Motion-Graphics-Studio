from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, CheckConstraint, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.enums import ArtifactRole, ArtifactStatus, ArtifactType, JobStatus
from app.db.types import JSONText


def enum_column(enum_cls: type, name: str) -> Enum:
    return Enum(
        enum_cls,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda enum_values: [item.value for item in enum_values],
        name=name,
    )


class Channel(Base):
    __tablename__ = "channels"

    channel_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    language: Mapped[str] = mapped_column(String, nullable=False)
    niche: Mapped[str] = mapped_column(Text, nullable=False)
    audience: Mapped[str] = mapped_column(Text, nullable=False)
    channel_dna_json: Mapped[dict[str, Any] | None] = mapped_column(JSONText(), nullable=True)
    channel_context_json: Mapped[dict[str, Any] | None] = mapped_column(JSONText(), nullable=True)
    voice_profile_json: Mapped[dict[str, Any] | None] = mapped_column(JSONText(), nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False, index=True)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)

    projects: Mapped[list["Project"]] = relationship(back_populates="channel", cascade="all, delete-orphan")


class Project(Base):
    __tablename__ = "projects"

    project_id: Mapped[str] = mapped_column(String, primary_key=True)
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.channel_id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    channel_context_json: Mapped[dict[str, Any]] = mapped_column(JSONText(), nullable=False)
    channel_dna_json: Mapped[dict[str, Any]] = mapped_column(JSONText(), nullable=False)
    topic_ideas_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.artifact_id"), nullable=True)
    selected_topic_ideas_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.artifact_id"), nullable=True)
    selected_topic_idea_id: Mapped[str | None] = mapped_column(String, nullable=True)
    script_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.artifact_id"), nullable=True)
    script_analysis_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.artifact_id"), nullable=True)
    style_bible_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.artifact_id"), nullable=True)
    scene_plan_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.artifact_id"), nullable=True)
    visual_plan_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.artifact_id"), nullable=True)
    prompt_bundle_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.artifact_id"), nullable=True)
    style_bible_visual_reference_set_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.artifact_id"), nullable=True)
    style_bible_visual_reference_approval_set_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.artifact_id"), nullable=True)
    scene_asset_manifest_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.artifact_id"), nullable=True)
    narration_audio_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.artifact_id"), nullable=True)
    narration_word_timing_map_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.artifact_id"), nullable=True)
    beat_timing_map_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.artifact_id"), nullable=True)
    assembly_plan_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.artifact_id"), nullable=True)
    subtitle_srt_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.artifact_id"), nullable=True)
    subtitle_vtt_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.artifact_id"), nullable=True)
    final_video_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.artifact_id"), nullable=True, index=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False, index=True)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)

    channel: Mapped[Channel] = relationship(back_populates="projects")


class ExternalAuthoringReservation(Base):
    __tablename__ = "external_authoring_reservations"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'consumed')", name="ck_external_authoring_reservations_status"),
        UniqueConstraint("paired_artifact_id", name="ux_external_authoring_reservations_paired_artifact_id"),
    )

    primary_artifact_id: Mapped[str] = mapped_column(String, primary_key=True)
    paired_artifact_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id"), nullable=False, index=True)
    stage: Mapped[str] = mapped_column(String, nullable=False, index=True)
    source_json: Mapped[dict[str, Any]] = mapped_column(JSONText(), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, index=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False, index=True)
    consumed_at: Mapped[str | None] = mapped_column(String, nullable=True, index=True)


class Job(Base):
    """Generic platform job row for V3."""

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint("progress >= 0 AND progress <= 100", name="ck_jobs_progress_range"),
    )

    job_id: Mapped[str] = mapped_column(String, primary_key=True)
    job_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    status: Mapped[JobStatus] = mapped_column(enum_column(JobStatus, "job_status_enum"), nullable=False, index=True)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    artifact_type: Mapped[str | None] = mapped_column(String, nullable=True)
    target_type: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    target_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    retryable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("0"))
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_details_json: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSONText(), nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False, index=True)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[str | None] = mapped_column(String, nullable=True)
    finished_at: Mapped[str | None] = mapped_column(String, nullable=True)
    heartbeat_at: Mapped[str | None] = mapped_column(String, nullable=True)
    completed_at: Mapped[str | None] = mapped_column(String, nullable=True)

    artifacts: Mapped[list["Artifact"]] = relationship(back_populates="source_job", foreign_keys="Artifact.source_job_id")


class Artifact(Base):
    """Generic platform artifact row for V3."""

    __tablename__ = "artifacts"
    __table_args__ = (UniqueConstraint("logical_id", "version_no", name="ux_artifacts_logical_version"),)

    artifact_id: Mapped[str] = mapped_column(String, primary_key=True)
    logical_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("0"), index=True)
    superseded_by_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.artifact_id"), nullable=True)
    artifact_type: Mapped[ArtifactType] = mapped_column(enum_column(ArtifactType, "artifact_type_enum"), nullable=False, index=True)
    artifact_role: Mapped[ArtifactRole | None] = mapped_column(enum_column(ArtifactRole, "artifact_role_enum"), nullable=True)
    path: Mapped[str] = mapped_column(String, nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    provider_result_url: Mapped[str | None] = mapped_column(String, nullable=True)
    provider_source: Mapped[str | None] = mapped_column(String, nullable=True)
    source_job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.job_id"), nullable=True, index=True)
    status: Mapped[ArtifactStatus] = mapped_column(enum_column(ArtifactStatus, "artifact_status_enum"), nullable=False, index=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False, index=True)
    deleted_at: Mapped[str | None] = mapped_column(String, nullable=True)

    source_job: Mapped[Job | None] = relationship(back_populates="artifacts", foreign_keys=[source_job_id])


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value_json: Mapped[dict[str, Any] | list[Any] | str | int | float | bool | None] = mapped_column(JSONText(), nullable=True)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class MediaBatch(Base):
    __tablename__ = "media_batches"

    batch_id: Mapped[str] = mapped_column(String, primary_key=True)
    purpose: Mapped[str] = mapped_column(String, nullable=False, index=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.project_id"), nullable=True, index=True)
    style_bible_revision_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    domain_revision_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    created_at: Mapped[str] = mapped_column(String, nullable=False, index=True)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class MediaTask(Base):
    __tablename__ = "media_tasks"

    media_task_id: Mapped[str] = mapped_column(String, primary_key=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("media_batches.batch_id"), nullable=False, index=True)
    purpose: Mapped[str] = mapped_column(String, nullable=False, index=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.project_id"), nullable=True, index=True)
    style_bible_revision_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    domain_revision_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    entity_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    entity_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    candidate_index: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, index=True)
    external_provider: Mapped[str] = mapped_column(String, nullable=False, index=True)
    external_task_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    provider_status: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    provider_submitted_at: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    provider_error_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    negative_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str] = mapped_column(String, nullable=False)
    aspect_ratio: Mapped[str] = mapped_column(String, nullable=False)
    upscale_json: Mapped[list[str]] = mapped_column(JSONText(), nullable=False)
    reference_images_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONText(), nullable=True)
    storage_namespace: Mapped[str | None] = mapped_column(String, nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSONText(), nullable=True)
    result_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONText(), nullable=False)
    storage_uri: Mapped[str | None] = mapped_column(String, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String, nullable=True)
    source_result_url: Mapped[str | None] = mapped_column(String, nullable=True)
    selected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("0"))
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("0"))
    created_at: Mapped[str] = mapped_column(String, nullable=False, index=True)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
    completed_at: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    poll_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    poll_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    poll_claim_token: Mapped[str | None] = mapped_column(String, nullable=True)
    poll_claimed_at: Mapped[str | None] = mapped_column(String, nullable=True)
    poll_claim_expires_at: Mapped[str | None] = mapped_column(String, nullable=True)

class StyleBibleVisualOperation(Base):
    __tablename__ = "style_bible_visual_operations"
    __table_args__ = (
        UniqueConstraint("job_id", name="ux_style_bible_visual_operations_job_id"),
    )

    operation_id: Mapped[str] = mapped_column(String, primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.job_id"), nullable=False, unique=True, index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id"), nullable=False, index=True)
    operation_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String, nullable=False, index=True)
    script_artifact_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    script_analysis_artifact_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    style_bible_artifact_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    expected_visual_reference_set_artifact_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    media_batch_id: Mapped[str] = mapped_column(ForeignKey("media_batches.batch_id"), nullable=False, index=True)
    request_json: Mapped[dict[str, Any]] = mapped_column(JSONText(), nullable=False)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSONText(), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_details_json: Mapped[dict[str, Any] | None] = mapped_column(JSONText(), nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False, index=True)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
    completed_at: Mapped[str | None] = mapped_column(String, nullable=True, index=True)


class SceneAssetOperation(Base):
    __tablename__ = "scene_asset_operations"
    __table_args__ = (UniqueConstraint("job_id", name="ux_scene_asset_operations_job_id"),)

    operation_id: Mapped[str] = mapped_column(String, primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.job_id"), nullable=False, unique=True, index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id"), nullable=False, index=True)
    operation_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String, nullable=False, index=True)
    script_artifact_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    script_analysis_artifact_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    style_bible_artifact_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    scene_plan_artifact_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    visual_plan_artifact_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    prompt_bundle_artifact_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    visual_reference_approval_set_artifact_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    expected_scene_asset_manifest_artifact_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    media_batch_id: Mapped[str] = mapped_column(ForeignKey("media_batches.batch_id"), nullable=False, index=True)
    request_json: Mapped[dict[str, Any]] = mapped_column(JSONText(), nullable=False)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSONText(), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_details_json: Mapped[dict[str, Any] | None] = mapped_column(JSONText(), nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False, index=True)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
    completed_at: Mapped[str | None] = mapped_column(String, nullable=True, index=True)


class SceneAssetBeatClaim(Base):
    __tablename__ = "scene_asset_beat_claims"
    __table_args__ = (
        UniqueConstraint("project_id", "beat_id", name="ux_scene_asset_beat_claims_project_beat"),
    )

    claim_id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id"), nullable=False, index=True)
    beat_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    operation_id: Mapped[str] = mapped_column(ForeignKey("scene_asset_operations.operation_id"), nullable=False, index=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False, index=True)

class SceneAssetBeatOperationLease(Base):
    __tablename__ = "scene_asset_beat_operation_leases"
    __table_args__ = (
        UniqueConstraint("project_id", "beat_id", name="ux_scene_asset_beat_operation_leases_project_beat"),
    )

    lease_id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id"), nullable=False, index=True)
    beat_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    owner_kind: Mapped[str] = mapped_column(String, nullable=False, index=True)
    owner_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False, index=True)

class SceneAssetCandidateReview(Base):
    __tablename__ = "scene_asset_candidate_reviews"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "scene_id", "beat_id", "candidate_artifact_id",
            name="ux_scene_asset_candidate_reviews_owner",
        ),
    )

    review_id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id"), nullable=False, index=True)
    scene_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    beat_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    candidate_artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.artifact_id"), nullable=False, index=True)
    decision: Mapped[str] = mapped_column(String, nullable=False, index=True)
    is_current_draft: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("0"))
    is_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("0"))
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class VoiceGenerationOperation(Base):
    __tablename__ = "voice_generation_operations"
    __table_args__ = (UniqueConstraint("job_id", name="ux_voice_generation_operations_job_id"),)

    operation_id: Mapped[str] = mapped_column(String, primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.job_id"), nullable=False, unique=True, index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id"), nullable=False, index=True)
    operation_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String, nullable=False, index=True)
    script_artifact_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    architecture_version: Mapped[str] = mapped_column(String, nullable=False, index=True)
    expected_narration_audio_artifact_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    expected_narration_word_timing_map_artifact_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    voice_preset: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    tts_profile_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    voice_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    language: Mapped[str] = mapped_column(String, nullable=False)
    speaking_rate: Mapped[float] = mapped_column(Float, nullable=False)
    provider_identity_json: Mapped[dict[str, Any]] = mapped_column(JSONText(), nullable=False)
    provider_identity_sha256: Mapped[str] = mapped_column(String, nullable=False)
    generation_settings_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSONText(), nullable=False)
    generation_settings_snapshot_sha256: Mapped[str] = mapped_column(String, nullable=False)
    segmentation_version: Mapped[str] = mapped_column(String, nullable=False)
    segmentation_input_sha256: Mapped[str] = mapped_column(String, nullable=False)
    segment_count: Mapped[int] = mapped_column(Integer, nullable=False)
    request_json: Mapped[dict[str, Any]] = mapped_column(JSONText(), nullable=False)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSONText(), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_details_json: Mapped[dict[str, Any] | None] = mapped_column(JSONText(), nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False, index=True)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
    completed_at: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    next_reconcile_at: Mapped[str | None] = mapped_column(String, nullable=True)
    reconcile_owner: Mapped[str | None] = mapped_column(String, nullable=True)
    reconcile_token: Mapped[str | None] = mapped_column(String, nullable=True)
    reconcile_lease_expires_at: Mapped[str | None] = mapped_column(String, nullable=True)
    cancel_requested_at: Mapped[str | None] = mapped_column(String, nullable=True)
    cancelled_at: Mapped[str | None] = mapped_column(String, nullable=True)
    state_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))


class VoiceAlignmentTask(Base):
    __tablename__ = "voice_alignment_tasks"
    __table_args__ = (
        UniqueConstraint("operation_id", "section_id", "attempt_no", name="ux_voice_alignment_tasks_attempt"),
    )

    voice_alignment_task_id: Mapped[str] = mapped_column(String, primary_key=True)
    operation_id: Mapped[str] = mapped_column(ForeignKey("voice_generation_operations.operation_id"), nullable=False, index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.job_id"), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id"), nullable=False, index=True)
    section_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    section_order: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    section_audio_artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.artifact_id"), nullable=False, index=True)
    section_audio_sha256: Mapped[str] = mapped_column(String, nullable=False)
    section_audio_sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    sample_rate_hz: Mapped[int] = mapped_column(Integer, nullable=False)
    spoken_transcript: Mapped[str] = mapped_column(Text, nullable=False)
    spoken_transcript_sha256: Mapped[str] = mapped_column(String, nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False, index=True)
    provider_policy_version: Mapped[str] = mapped_column(String, nullable=False)
    alignment_policy_version: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, index=True)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    submit_attempt_token: Mapped[str | None] = mapped_column(String, nullable=True)
    submit_started_at: Mapped[str | None] = mapped_column(String, nullable=True)
    execution_attempt_token: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    execution_started_at: Mapped[str | None] = mapped_column(String, nullable=True)
    provider_submitted_at: Mapped[str | None] = mapped_column(String, nullable=True)
    provider_status: Mapped[str | None] = mapped_column(String, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3, server_default=text("3"))
    last_retry_at: Mapped[str | None] = mapped_column(String, nullable=True)
    next_reconcile_at: Mapped[str | None] = mapped_column(String, nullable=True)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSONText(), nullable=True)
    loss_telemetry_json: Mapped[dict[str, Any] | None] = mapped_column(JSONText(), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_details_json: Mapped[dict[str, Any] | None] = mapped_column(JSONText(), nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False, index=True)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
    completed_at: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    state_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))


class RenderOperation(Base):
    __tablename__ = "render_operations"
    __table_args__ = (UniqueConstraint("job_id", name="ux_render_operations_job_id"),)

    render_operation_id: Mapped[str] = mapped_column(String, primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.job_id"), nullable=False, unique=True, index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id"), nullable=False, index=True)
    operation_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String, nullable=False, index=True)
    source_assembly_plan_artifact_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    source_subtitle_srt_artifact_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    source_subtitle_vtt_artifact_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    expected_final_video_artifact_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    render_profile_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    render_profile_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSONText(), nullable=False)
    render_profile_snapshot_sha256: Mapped[str] = mapped_column(String, nullable=False)
    ffmpeg_path_identity: Mapped[str] = mapped_column(String, nullable=False)
    ffmpeg_version: Mapped[str] = mapped_column(Text, nullable=False)
    ffmpeg_capabilities_sha256: Mapped[str] = mapped_column(String, nullable=False)
    ffprobe_path_identity: Mapped[str] = mapped_column(String, nullable=False)
    ffprobe_version: Mapped[str] = mapped_column(Text, nullable=False)
    command_graph_json: Mapped[dict[str, Any] | None] = mapped_column(JSONText(), nullable=True)
    command_graph_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    frame_quantization_json: Mapped[dict[str, Any] | None] = mapped_column(JSONText(), nullable=True)
    frame_quantization_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    attempt_token: Mapped[str | None] = mapped_column(String, nullable=True)
    attempt_relative_output_path: Mapped[str | None] = mapped_column(String, nullable=True)
    process_pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    process_started_at: Mapped[str | None] = mapped_column(String, nullable=True)
    worker_instance_id: Mapped[str | None] = mapped_column(String, nullable=True)
    progress_time_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    progress_frame: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    progress_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    recovery_enqueued: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("0"))
    cancellation_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("0"))
    request_json: Mapped[dict[str, Any]] = mapped_column(JSONText(), nullable=False)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSONText(), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_details_json: Mapped[dict[str, Any] | None] = mapped_column(JSONText(), nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False, index=True)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[str | None] = mapped_column(String, nullable=True)
    completed_at: Mapped[str | None] = mapped_column(String, nullable=True, index=True)


class VoiceSegmentTask(Base):
    __tablename__ = "voice_segment_tasks"
    __table_args__ = (UniqueConstraint("operation_id", "segment_id", "attempt_no", name="ux_voice_segment_tasks_attempt"),)

    voice_segment_task_id: Mapped[str] = mapped_column(String, primary_key=True)
    operation_id: Mapped[str] = mapped_column(ForeignKey("voice_generation_operations.operation_id"), nullable=False, index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.job_id"), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id"), nullable=False, index=True)
    segment_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    segment_order: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    section_id: Mapped[str] = mapped_column(String, nullable=False)
    source_start: Mapped[int] = mapped_column(Integer, nullable=False)
    source_end: Mapped[int] = mapped_column(Integer, nullable=False)
    text_sha256: Mapped[str] = mapped_column(String, nullable=False)
    exact_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, index=True)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    provider_task_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    provider_status: Mapped[str | None] = mapped_column(String, nullable=True)
    provider_submitted_at: Mapped[str | None] = mapped_column(String, nullable=True)
    voice_id: Mapped[str] = mapped_column(String, nullable=False)
    language: Mapped[str] = mapped_column(String, nullable=False)
    speaking_rate: Mapped[float] = mapped_column(Float, nullable=False)
    sample_rate_hz: Mapped[int] = mapped_column(Integer, nullable=False)
    output_format: Mapped[str] = mapped_column(String, nullable=False)
    provider_timing_json: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSONText(), nullable=True)
    provider_result_metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSONText(), nullable=True)
    segment_audio_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.artifact_id"), nullable=True, index=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    sample_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_details_json: Mapped[dict[str, Any] | None] = mapped_column(JSONText(), nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False, index=True)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
    completed_at: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    next_reconcile_at: Mapped[str | None] = mapped_column(String, nullable=True)
    submit_attempt_token: Mapped[str | None] = mapped_column(String, nullable=True)
    submit_started_at: Mapped[str | None] = mapped_column(String, nullable=True)
    execution_attempt_token: Mapped[str | None] = mapped_column(String, nullable=True)
    execution_started_at: Mapped[str | None] = mapped_column(String, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3, server_default=text("3"))
    last_retry_at: Mapped[str | None] = mapped_column(String, nullable=True)
    provider_handle_json: Mapped[dict[str, Any] | None] = mapped_column(JSONText(), nullable=True)
    state_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
