from __future__ import annotations

from enum import StrEnum


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STALE_FAILED = "stale_failed"


class ArtifactType(StrEnum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    SRT = "srt"
    VTT = "vtt"
    JSON = "json"
    THUMBNAIL = "thumbnail"
    EXPORT_ZIP = "export_zip"
    SUBTITLE_SRT = "subtitle_srt"
    SUBTITLE_VTT = "subtitle_vtt"
    PLAN_JSON = "plan_json"


class ArtifactRole(StrEnum):
    CHANNEL_DNA = "channel_dna"
    TOPIC_IDEAS = "topic_ideas"
    SCRIPT = "script"
    PROJECT_STYLE_BIBLE = "project_style_bible"
    PROMPT_DUMP = "prompt_dump"
    OUTPUT_DUMP = "output_dump"
    MANIFEST_JSON = "manifest_json"
    METADATA_FILE = "metadata_file"
    EXPORT_ZIP = "export_zip"
    SUBTITLE_SRT = "subtitle_srt"
    SUBTITLE_VTT = "subtitle_vtt"
    PLAN_JSON = "plan_json"
    FINAL_VIDEO = "final_video"


class ArtifactStatus(StrEnum):
    CREATING = "creating"
    READY = "ready"
    FAILED = "failed"
    DELETED = "deleted"
