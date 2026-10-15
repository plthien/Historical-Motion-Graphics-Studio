from __future__ import annotations

from app.db.models import VoiceAlignmentTask, VoiceGenerationOperation, VoiceSegmentTask


_OPERATION_FIELDS = {
    "next_reconcile_at",
    "reconcile_owner",
    "reconcile_token",
    "reconcile_lease_expires_at",
    "cancel_requested_at",
    "cancelled_at",
    "state_revision",
    "architecture_version",
    "expected_narration_word_timing_map_artifact_id",
    "voice_preset",
}

_TASK_FIELDS = {
    "next_reconcile_at",
    "submit_attempt_token",
    "submit_started_at",
    "execution_attempt_token",
    "execution_started_at",
    "retry_count",
    "max_attempts",
    "last_retry_at",
    "provider_handle_json",
    "state_revision",
}


def test_voice_operation_orm_matches_migration_028_durable_columns() -> None:
    columns = set(VoiceGenerationOperation.__table__.columns.keys())
    assert _OPERATION_FIELDS <= columns
    assert "scene_plan_artifact_id" not in columns
    assert "expected_narration_timing_map_artifact_id" not in columns
    assert VoiceGenerationOperation.__table__.c.state_revision.server_default is not None


def test_voice_segment_task_orm_matches_migration_028_durable_columns() -> None:
    columns = set(VoiceSegmentTask.__table__.columns.keys())
    assert _TASK_FIELDS <= columns
    assert VoiceSegmentTask.__table__.c.retry_count.server_default is not None
    assert VoiceSegmentTask.__table__.c.max_attempts.server_default is not None
    assert VoiceSegmentTask.__table__.c.state_revision.server_default is not None


def test_voice_orm_provider_handle_is_json_text_not_provider_specific_columns() -> None:
    task_columns = set(VoiceSegmentTask.__table__.columns.keys())
    assert "provider_handle_json" in task_columns
    assert not any(name.startswith("kokoro_") for name in task_columns)


def test_voice_alignment_task_orm_is_dedicated_table() -> None:
    columns = set(VoiceAlignmentTask.__table__.columns.keys())
    assert {
        "voice_alignment_task_id",
        "operation_id",
        "section_audio_artifact_id",
        "spoken_transcript",
        "spoken_transcript_sha256",
        "provider",
        "alignment_policy_version",
        "status",
        "submit_attempt_token",
        "execution_attempt_token",
        "result_json",
        "loss_telemetry_json",
    } <= columns
    assert "provider_task_id" not in columns
