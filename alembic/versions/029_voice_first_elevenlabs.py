"""voice-first ElevenLabs PVC foundation

Revision ID: 029_voice_first_elevenlabs
Revises: 028_durable_voice_workflow
Create Date: 2026-08-27
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "029_voice_first_elevenlabs"
down_revision = "028_durable_voice_workflow"
branch_labels = None
depends_on = None


ACTIVE_VOICE_OPERATION_STATUSES = (
    "queued",
    "running",
    "waiting_for_provider",
    "assembling",
    "finalizing",
)


def _refuse_active_voice_operations() -> None:
    bind = op.get_bind()
    placeholders = ", ".join(f":status_{index}" for index, _ in enumerate(ACTIVE_VOICE_OPERATION_STATUSES))
    params = {f"status_{index}": status for index, status in enumerate(ACTIVE_VOICE_OPERATION_STATUSES)}
    active_count = bind.execute(
        sa.text(f"SELECT COUNT(*) FROM voice_generation_operations WHERE status IN ({placeholders})"),
        params,
    ).scalar()
    if int(active_count or 0) > 0:
        raise RuntimeError("029_voice_first_elevenlabs refuses to run while a Voice operation is active.")


def _set_sqlite_foreign_keys(enabled: bool) -> None:
    bind = op.get_bind()
    bind.exec_driver_sql(f"PRAGMA foreign_keys={'ON' if enabled else 'OFF'}")
    current = int(bind.exec_driver_sql("PRAGMA foreign_keys").scalar() or 0)
    expected = 1 if enabled else 0
    if current != expected:
        raise RuntimeError(
            f"029_voice_first_elevenlabs could not set SQLite foreign_keys={expected}; got {current}."
        )


def _project_batch_rebuild(*, downgrade: bool = False) -> None:
    with op.batch_alter_table("projects", recreate="always") as batch:
        if downgrade:
            batch.drop_column("beat_timing_map_artifact_id")
            batch.drop_column("narration_word_timing_map_artifact_id")
            batch.add_column(sa.Column("narration_timing_map_artifact_id", sa.String(), nullable=True))
        else:
            batch.drop_column("narration_timing_map_artifact_id")
            batch.add_column(sa.Column("narration_word_timing_map_artifact_id", sa.String(), nullable=True))
            batch.add_column(sa.Column("beat_timing_map_artifact_id", sa.String(), nullable=True))


def _rebuild_projects_with_sqlite_fk_guard(*, downgrade: bool = False) -> None:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        _project_batch_rebuild(downgrade=downgrade)
        return

    violations: list[object] = []
    # PRAGMA foreign_keys cannot be changed while SQLite is inside a transaction.
    # Alembic's autocommit block is the supported migration boundary for this
    # move-and-copy parent-table rebuild and avoids mutating SQLAlchemy's
    # transaction state manually. The setting is connection-local and restored
    # before normal migration execution resumes.
    with op.get_context().autocommit_block():
        _set_sqlite_foreign_keys(False)
        try:
            _project_batch_rebuild(downgrade=downgrade)
            violations = list(bind.exec_driver_sql("PRAGMA foreign_key_check").fetchall())
        finally:
            _set_sqlite_foreign_keys(True)

    if violations:
        preview = ", ".join(str(row) for row in violations[:10])
        direction = "downgrade" if downgrade else "upgrade"
        raise RuntimeError(
            f"029_voice_first_elevenlabs {direction} detected foreign-key violations after rebuilding projects: "
            f"{preview}"
        )


def upgrade() -> None:
    _refuse_active_voice_operations()

    op.add_column("channels", sa.Column("voice_profile_json", sa.Text(), nullable=True))

    op.execute("DELETE FROM voice_segment_tasks")
    op.execute("DELETE FROM voice_generation_operations")

    op.drop_index("ix_projects_narration_timing_map_artifact_id", table_name="projects")
    # SQLite native DROP COLUMN validates surviving trigger SQL against the new
    # table definition. The pre-cutover project invalidation trigger mentions
    # narration_timing_map_artifact_id, so native DROP COLUMN is not sufficient.
    # Recreate the table while preserving all non-Voice project data and IDs.
    _rebuild_projects_with_sqlite_fk_guard()

    op.create_index("ix_projects_narration_word_timing_map_artifact_id", "projects", ["narration_word_timing_map_artifact_id"], unique=False)
    op.create_index("ix_projects_beat_timing_map_artifact_id", "projects", ["beat_timing_map_artifact_id"], unique=False)

    op.execute(
        """
        UPDATE projects
        SET narration_audio_artifact_id = NULL,
            narration_word_timing_map_artifact_id = NULL,
            beat_timing_map_artifact_id = NULL,
            scene_plan_artifact_id = NULL,
            visual_plan_artifact_id = NULL,
            prompt_bundle_artifact_id = NULL,
            scene_asset_manifest_artifact_id = NULL,
            assembly_plan_artifact_id = NULL,
            subtitle_srt_artifact_id = NULL,
            subtitle_vtt_artifact_id = NULL,
            final_video_artifact_id = NULL
        """
    )

    op.drop_index("ix_voice_generation_operations_scene_plan_artifact_id", table_name="voice_generation_operations")
    op.drop_index("ix_voice_generation_operations_expected_narration_timing_map_artifact_id", table_name="voice_generation_operations")
    with op.batch_alter_table("voice_generation_operations") as batch:
        batch.drop_column("scene_plan_artifact_id")
        batch.drop_column("expected_narration_timing_map_artifact_id")
        batch.add_column(sa.Column("architecture_version", sa.String(), nullable=False, server_default="voice_first_elevenlabs_v1"))
        batch.add_column(sa.Column("expected_narration_word_timing_map_artifact_id", sa.String(), nullable=True))
        batch.add_column(sa.Column("voice_preset", sa.String(), nullable=False, server_default="quality_cloned_voice"))

    op.create_index("ix_voice_generation_operations_architecture_version", "voice_generation_operations", ["architecture_version"], unique=False)
    op.create_index(
        "ix_voice_generation_operations_expected_narration_word_timing_map_artifact_id",
        "voice_generation_operations",
        ["expected_narration_word_timing_map_artifact_id"],
        unique=False,
    )
    op.create_index("ix_voice_generation_operations_voice_preset", "voice_generation_operations", ["voice_preset"], unique=False)

    op.create_table(
        "voice_alignment_tasks",
        sa.Column("voice_alignment_task_id", sa.String(), primary_key=True),
        sa.Column("operation_id", sa.String(), sa.ForeignKey("voice_generation_operations.operation_id"), nullable=False),
        sa.Column("job_id", sa.String(), sa.ForeignKey("jobs.job_id"), nullable=False),
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.project_id"), nullable=False),
        sa.Column("section_id", sa.String(), nullable=False),
        sa.Column("section_order", sa.Integer(), nullable=False),
        sa.Column("section_audio_artifact_id", sa.String(), sa.ForeignKey("artifacts.artifact_id"), nullable=False),
        sa.Column("section_audio_sha256", sa.String(), nullable=False),
        sa.Column("section_audio_sample_count", sa.Integer(), nullable=False),
        sa.Column("sample_rate_hz", sa.Integer(), nullable=False),
        sa.Column("spoken_transcript", sa.Text(), nullable=False),
        sa.Column("spoken_transcript_sha256", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("provider_policy_version", sa.String(), nullable=False),
        sa.Column("alignment_policy_version", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("submit_attempt_token", sa.String(), nullable=True),
        sa.Column("submit_started_at", sa.String(), nullable=True),
        sa.Column("execution_attempt_token", sa.String(), nullable=True),
        sa.Column("execution_started_at", sa.String(), nullable=True),
        sa.Column("provider_submitted_at", sa.String(), nullable=True),
        sa.Column("provider_status", sa.String(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("last_retry_at", sa.String(), nullable=True),
        sa.Column("next_reconcile_at", sa.String(), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("loss_telemetry_json", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_details_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.Column("completed_at", sa.String(), nullable=True),
        sa.Column("state_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint(
            "status IN ('queued', 'submitting', 'completed', 'failed', 'submit_outcome_unknown', 'cancelled')",
            name="ck_voice_alignment_tasks_status",
        ),
        sa.UniqueConstraint("operation_id", "section_id", "attempt_no", name="ux_voice_alignment_tasks_attempt"),
    )
    for name in (
        "operation_id",
        "job_id",
        "project_id",
        "section_id",
        "section_order",
        "section_audio_artifact_id",
        "provider",
        "status",
        "execution_attempt_token",
        "created_at",
        "completed_at",
    ):
        op.create_index(f"ix_voice_alignment_tasks_{name}", "voice_alignment_tasks", [name], unique=False)
    op.create_index("ix_voice_alignment_tasks_due", "voice_alignment_tasks", ["operation_id", "status", "next_reconcile_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_voice_alignment_tasks_due", table_name="voice_alignment_tasks")
    for name in (
        "completed_at",
        "created_at",
        "execution_attempt_token",
        "status",
        "provider",
        "section_audio_artifact_id",
        "section_order",
        "section_id",
        "project_id",
        "job_id",
        "operation_id",
    ):
        op.drop_index(f"ix_voice_alignment_tasks_{name}", table_name="voice_alignment_tasks")
    op.drop_table("voice_alignment_tasks")

    op.drop_index("ix_voice_generation_operations_voice_preset", table_name="voice_generation_operations")
    op.drop_index("ix_voice_generation_operations_expected_narration_word_timing_map_artifact_id", table_name="voice_generation_operations")
    op.drop_index("ix_voice_generation_operations_architecture_version", table_name="voice_generation_operations")
    with op.batch_alter_table("voice_generation_operations") as batch:
        batch.drop_column("voice_preset")
        batch.drop_column("expected_narration_word_timing_map_artifact_id")
        batch.drop_column("architecture_version")
        batch.add_column(sa.Column("expected_narration_timing_map_artifact_id", sa.String(), nullable=True))
        batch.add_column(sa.Column("scene_plan_artifact_id", sa.String(), nullable=False, server_default="legacy_scene_plan_required"))
    op.create_index("ix_voice_generation_operations_expected_narration_timing_map_artifact_id", "voice_generation_operations", ["expected_narration_timing_map_artifact_id"], unique=False)
    op.create_index("ix_voice_generation_operations_scene_plan_artifact_id", "voice_generation_operations", ["scene_plan_artifact_id"], unique=False)

    op.drop_index("ix_projects_beat_timing_map_artifact_id", table_name="projects")
    op.drop_index("ix_projects_narration_word_timing_map_artifact_id", table_name="projects")
    _rebuild_projects_with_sqlite_fk_guard(downgrade=True)

    with op.batch_alter_table("channels") as batch:
        batch.drop_column("voice_profile_json")
