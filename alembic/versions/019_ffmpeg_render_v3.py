"""Phase 14 local FFmpeg render.

Revision ID: 019_ffmpeg_render_v3
Revises: 018_assembly_plan_subtitles_v3
Create Date: 2026-07-20
"""
from __future__ import annotations
from alembic import op
import sqlalchemy as sa

revision = "019_ffmpeg_render_v3"
down_revision = "018_assembly_plan_subtitles_v3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql("ALTER TABLE projects ADD COLUMN final_video_artifact_id VARCHAR REFERENCES artifacts(artifact_id)")
    else:
        op.add_column("projects", sa.Column("final_video_artifact_id", sa.String(), nullable=True))
        op.create_foreign_key("fk_projects_final_video_artifact_id_artifacts", "projects", "artifacts", ["final_video_artifact_id"], ["artifact_id"])
    op.create_index("ix_projects_final_video_artifact_id", "projects", ["final_video_artifact_id"], unique=False)
    op.create_table(
        "render_operations",
        sa.Column("render_operation_id", sa.String(), primary_key=True),
        sa.Column("job_id", sa.String(), sa.ForeignKey("jobs.job_id"), nullable=False, unique=True),
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.project_id"), nullable=False),
        sa.Column("operation_type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("source_assembly_plan_artifact_id", sa.String(), nullable=False),
        sa.Column("source_subtitle_srt_artifact_id", sa.String(), nullable=False),
        sa.Column("source_subtitle_vtt_artifact_id", sa.String(), nullable=False),
        sa.Column("expected_final_video_artifact_id", sa.String(), nullable=True),
        sa.Column("render_profile_id", sa.String(), nullable=False),
        sa.Column("render_profile_snapshot_json", sa.Text(), nullable=False),
        sa.Column("render_profile_snapshot_sha256", sa.String(), nullable=False),
        sa.Column("ffmpeg_path_identity", sa.String(), nullable=False),
        sa.Column("ffmpeg_version", sa.Text(), nullable=False),
        sa.Column("ffmpeg_capabilities_sha256", sa.String(), nullable=False),
        sa.Column("ffprobe_path_identity", sa.String(), nullable=False),
        sa.Column("ffprobe_version", sa.Text(), nullable=False),
        sa.Column("command_graph_json", sa.Text(), nullable=True),
        sa.Column("command_graph_sha256", sa.String(), nullable=True),
        sa.Column("frame_quantization_json", sa.Text(), nullable=True),
        sa.Column("frame_quantization_sha256", sa.String(), nullable=True),
        sa.Column("attempt_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("attempt_token", sa.String(), nullable=True),
        sa.Column("attempt_relative_output_path", sa.String(), nullable=True),
        sa.Column("process_pid", sa.Integer(), nullable=True),
        sa.Column("process_started_at", sa.String(), nullable=True),
        sa.Column("worker_instance_id", sa.String(), nullable=True),
        sa.Column("progress_time_units", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("progress_frame", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("progress_percent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("recovery_enqueued", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("cancellation_requested", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("request_json", sa.Text(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_details_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.Column("started_at", sa.String(), nullable=True),
        sa.Column("completed_at", sa.String(), nullable=True),
        sa.UniqueConstraint("job_id", name="ux_render_operations_job_id"),
    )
    for name in (
        "job_id", "project_id", "operation_type", "status", "source_assembly_plan_artifact_id",
        "source_subtitle_srt_artifact_id", "source_subtitle_vtt_artifact_id", "expected_final_video_artifact_id",
        "render_profile_id", "created_at", "completed_at",
    ):
        op.create_index(f"ix_render_operations_{name}", "render_operations", [name], unique=False)


def downgrade() -> None:
    op.drop_table("render_operations")
    op.drop_index("ix_projects_final_video_artifact_id", table_name="projects")
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql("ALTER TABLE projects DROP COLUMN final_video_artifact_id")
    else:
        op.drop_constraint("fk_projects_final_video_artifact_id_artifacts", "projects", type_="foreignkey")
        op.drop_column("projects", "final_video_artifact_id")
