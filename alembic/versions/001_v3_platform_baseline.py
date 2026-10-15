"""v3 platform baseline

Revision ID: 001_v3_platform_baseline
Revises: 
Create Date: 2026-07-02
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "001_v3_platform_baseline"
down_revision = None
branch_labels = None
depends_on = None


job_status_values = ("queued", "running", "completed", "failed", "cancelled", "stale_failed")
artifact_type_values = ("image", "video", "audio", "srt", "json", "thumbnail", "export_zip")
artifact_role_values = (
    "channel_dna",
    "topic_ideas",
    "script",
    "prompt_dump",
    "output_dump",
    "manifest_json",
    "metadata_file",
    "export_zip",
)
artifact_status_values = ("creating", "ready", "failed", "deleted")


def upgrade() -> None:
    op.create_table(
        "settings",
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("value_json", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.String(), nullable=False),
    )

    op.create_table(
        "jobs",
        sa.Column("job_id", sa.String(), primary_key=True),
        sa.Column("job_type", sa.String(), nullable=False),
        sa.Column("status", sa.Enum(*job_status_values, name="job_status_enum", native_enum=False), nullable=False),
        sa.Column("progress", sa.Integer(), server_default="0", nullable=False),
        sa.Column("artifact_type", sa.String(), nullable=True),
        sa.Column("target_type", sa.String(), nullable=True),
        sa.Column("target_id", sa.String(), nullable=True),
        sa.Column("attempt", sa.Integer(), server_default="1", nullable=False),
        sa.Column("retryable", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_details_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.Column("started_at", sa.String(), nullable=True),
        sa.Column("finished_at", sa.String(), nullable=True),
        sa.Column("heartbeat_at", sa.String(), nullable=True),
        sa.Column("completed_at", sa.String(), nullable=True),
        sa.CheckConstraint("progress >= 0 AND progress <= 100", name="ck_jobs_progress_range"),
    )
    op.create_index("ix_jobs_job_type", "jobs", ["job_type"])
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_target_type", "jobs", ["target_type"])
    op.create_index("ix_jobs_target_id", "jobs", ["target_id"])
    op.create_index("ix_jobs_created_at", "jobs", ["created_at"])

    op.create_table(
        "artifacts",
        sa.Column("artifact_id", sa.String(), primary_key=True),
        sa.Column("logical_id", sa.String(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("is_current", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("superseded_by_artifact_id", sa.String(), nullable=True),
        sa.Column("artifact_type", sa.Enum(*artifact_type_values, name="artifact_type_enum", native_enum=False), nullable=False),
        sa.Column("artifact_role", sa.Enum(*artifact_role_values, name="artifact_role_enum", native_enum=False), nullable=True),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=True),
        sa.Column("sha256", sa.String(), nullable=True),
        sa.Column("mime_type", sa.String(), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("duration_sec", sa.Float(), nullable=True),
        sa.Column("provider_result_url", sa.String(), nullable=True),
        sa.Column("provider_source", sa.String(), nullable=True),
        sa.Column("source_job_id", sa.String(), nullable=True),
        sa.Column("status", sa.Enum(*artifact_status_values, name="artifact_status_enum", native_enum=False), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("deleted_at", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["source_job_id"], ["jobs.job_id"]),
        sa.ForeignKeyConstraint(["superseded_by_artifact_id"], ["artifacts.artifact_id"]),
        sa.UniqueConstraint("logical_id", "version_no", name="ux_artifacts_logical_version"),
    )
    op.create_index("ix_artifacts_logical_id", "artifacts", ["logical_id"])
    op.create_index("ix_artifacts_is_current", "artifacts", ["is_current"])
    op.create_index("ix_artifacts_artifact_type", "artifacts", ["artifact_type"])
    op.create_index("ix_artifacts_status", "artifacts", ["status"])
    op.create_index("ix_artifacts_source_job_id", "artifacts", ["source_job_id"])
    op.create_index("ix_artifacts_created_at", "artifacts", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_artifacts_created_at", table_name="artifacts")
    op.drop_index("ix_artifacts_source_job_id", table_name="artifacts")
    op.drop_index("ix_artifacts_status", table_name="artifacts")
    op.drop_index("ix_artifacts_artifact_type", table_name="artifacts")
    op.drop_index("ix_artifacts_is_current", table_name="artifacts")
    op.drop_index("ix_artifacts_logical_id", table_name="artifacts")
    op.drop_table("artifacts")
    op.drop_index("ix_jobs_created_at", table_name="jobs")
    op.drop_index("ix_jobs_target_id", table_name="jobs")
    op.drop_index("ix_jobs_target_type", table_name="jobs")
    op.drop_index("ix_jobs_status", table_name="jobs")
    op.drop_index("ix_jobs_job_type", table_name="jobs")
    op.drop_table("jobs")
    op.drop_table("settings")
