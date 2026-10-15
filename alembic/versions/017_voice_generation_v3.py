"""Phase 12 voice generation.

Revision ID: 017_voice_generation_v3
Revises: 016_scene_asset_generation_v3
Create Date: 2026-07-19
"""
from __future__ import annotations
from alembic import op
import sqlalchemy as sa

revision = "017_voice_generation_v3"
down_revision = "016_scene_asset_generation_v3"
branch_labels = None
depends_on = None


def _add_project_pointer(name: str) -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql(f"ALTER TABLE projects ADD COLUMN {name} VARCHAR REFERENCES artifacts(artifact_id)")
    else:
        op.add_column("projects", sa.Column(name, sa.String(), nullable=True))
        op.create_foreign_key(f"fk_projects_{name}_artifacts", "projects", "artifacts", [name], ["artifact_id"])
    op.create_index(f"ix_projects_{name}", "projects", [name], unique=False)


def upgrade() -> None:
    _add_project_pointer("narration_audio_artifact_id")
    _add_project_pointer("narration_timing_map_artifact_id")
    op.create_table(
        "voice_generation_operations",
        sa.Column("operation_id", sa.String(), primary_key=True),
        sa.Column("job_id", sa.String(), sa.ForeignKey("jobs.job_id"), nullable=False),
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.project_id"), nullable=False),
        sa.Column("operation_type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("script_artifact_id", sa.String(), nullable=False),
        sa.Column("scene_plan_artifact_id", sa.String(), nullable=False),
        sa.Column("expected_narration_audio_artifact_id", sa.String(), nullable=True),
        sa.Column("expected_narration_timing_map_artifact_id", sa.String(), nullable=True),
        sa.Column("tts_profile_id", sa.String(), nullable=False),
        sa.Column("voice_id", sa.String(), nullable=False),
        sa.Column("language", sa.String(), nullable=False),
        sa.Column("speaking_rate", sa.Float(), nullable=False),
        sa.Column("provider_identity_json", sa.Text(), nullable=False),
        sa.Column("provider_identity_sha256", sa.String(), nullable=False),
        sa.Column("generation_settings_snapshot_json", sa.Text(), nullable=False),
        sa.Column("generation_settings_snapshot_sha256", sa.String(), nullable=False),
        sa.Column("segmentation_version", sa.String(), nullable=False),
        sa.Column("segmentation_input_sha256", sa.String(), nullable=False),
        sa.Column("segment_count", sa.Integer(), nullable=False),
        sa.Column("request_json", sa.Text(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_details_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.Column("completed_at", sa.String(), nullable=True),
        sa.UniqueConstraint("job_id", name="ux_voice_generation_operations_job_id"),
    )
    for name in (
        "job_id", "project_id", "operation_type", "status", "script_artifact_id", "scene_plan_artifact_id",
        "expected_narration_audio_artifact_id", "expected_narration_timing_map_artifact_id", "tts_profile_id",
        "voice_id", "created_at", "completed_at",
    ):
        op.create_index(f"ix_voice_generation_operations_{name}", "voice_generation_operations", [name], unique=(name == "job_id"))

    op.create_table(
        "voice_segment_tasks",
        sa.Column("voice_segment_task_id", sa.String(), primary_key=True),
        sa.Column("operation_id", sa.String(), sa.ForeignKey("voice_generation_operations.operation_id"), nullable=False),
        sa.Column("job_id", sa.String(), sa.ForeignKey("jobs.job_id"), nullable=False),
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.project_id"), nullable=False),
        sa.Column("segment_id", sa.String(), nullable=False),
        sa.Column("segment_order", sa.Integer(), nullable=False),
        sa.Column("section_id", sa.String(), nullable=False),
        sa.Column("source_start", sa.Integer(), nullable=False),
        sa.Column("source_end", sa.Integer(), nullable=False),
        sa.Column("text_sha256", sa.String(), nullable=False),
        sa.Column("exact_text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("provider_task_id", sa.String(), nullable=True),
        sa.Column("provider_status", sa.String(), nullable=True),
        sa.Column("provider_submitted_at", sa.String(), nullable=True),
        sa.Column("voice_id", sa.String(), nullable=False),
        sa.Column("language", sa.String(), nullable=False),
        sa.Column("speaking_rate", sa.Float(), nullable=False),
        sa.Column("sample_rate_hz", sa.Integer(), nullable=False),
        sa.Column("output_format", sa.String(), nullable=False),
        sa.Column("provider_timing_json", sa.Text(), nullable=True),
        sa.Column("provider_result_metadata_json", sa.Text(), nullable=True),
        sa.Column("segment_audio_artifact_id", sa.String(), sa.ForeignKey("artifacts.artifact_id"), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("sample_count", sa.Integer(), nullable=True),
        sa.Column("sha256", sa.String(), nullable=True),
        sa.Column("mime_type", sa.String(), nullable=True),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_details_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.Column("completed_at", sa.String(), nullable=True),
        sa.UniqueConstraint("operation_id", "segment_id", "attempt_no", name="ux_voice_segment_tasks_attempt"),
    )
    for name in (
        "operation_id", "job_id", "project_id", "segment_id", "segment_order", "status", "provider_task_id",
        "segment_audio_artifact_id", "created_at", "completed_at",
    ):
        op.create_index(f"ix_voice_segment_tasks_{name}", "voice_segment_tasks", [name], unique=False)


def downgrade() -> None:
    for name in ("completed_at", "created_at", "segment_audio_artifact_id", "provider_task_id", "status", "segment_order", "segment_id", "project_id", "job_id", "operation_id"):
        op.drop_index(f"ix_voice_segment_tasks_{name}", table_name="voice_segment_tasks")
    op.drop_table("voice_segment_tasks")
    for name in ("completed_at", "created_at", "voice_id", "tts_profile_id", "expected_narration_timing_map_artifact_id", "expected_narration_audio_artifact_id", "scene_plan_artifact_id", "script_artifact_id", "status", "operation_type", "project_id", "job_id"):
        op.drop_index(f"ix_voice_generation_operations_{name}", table_name="voice_generation_operations")
    op.drop_table("voice_generation_operations")
    for name in ("narration_timing_map_artifact_id", "narration_audio_artifact_id"):
        op.drop_index(f"ix_projects_{name}", table_name="projects")
        bind = op.get_bind()
        if bind.dialect.name == "sqlite":
            bind.exec_driver_sql(f"ALTER TABLE projects DROP COLUMN {name}")
        else:
            op.drop_constraint(f"fk_projects_{name}_artifacts", "projects", type_="foreignkey")
            op.drop_column("projects", name)
