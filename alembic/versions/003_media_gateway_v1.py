"""media gateway v1

Revision ID: 003_media_gateway_v1
Revises: 002_v3_product_foundation
Create Date: 2026-07-03
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "003_media_gateway_v1"
down_revision = "002_v3_product_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "media_batches",
        sa.Column("batch_id", sa.String(), primary_key=True),
        sa.Column("purpose", sa.String(), nullable=False),
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.project_id"), nullable=True),
        sa.Column("style_bible_revision_id", sa.String(), nullable=True),
        sa.Column("total", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
    )
    op.create_index("ix_media_batches_purpose", "media_batches", ["purpose"])
    op.create_index("ix_media_batches_project_id", "media_batches", ["project_id"])
    op.create_index("ix_media_batches_style_bible_revision_id", "media_batches", ["style_bible_revision_id"])
    op.create_index("ix_media_batches_created_at", "media_batches", ["created_at"])

    op.create_table(
        "media_tasks",
        sa.Column("media_task_id", sa.String(), primary_key=True),
        sa.Column("batch_id", sa.String(), sa.ForeignKey("media_batches.batch_id"), nullable=False),
        sa.Column("purpose", sa.String(), nullable=False),
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.project_id"), nullable=True),
        sa.Column("style_bible_revision_id", sa.String(), nullable=True),
        sa.Column("entity_type", sa.String(), nullable=False),
        sa.Column("entity_id", sa.String(), nullable=False),
        sa.Column("candidate_index", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("external_provider", sa.String(), nullable=False),
        sa.Column("external_task_id", sa.String(), nullable=True),
        sa.Column("provider_status", sa.String(), nullable=True),
        sa.Column("provider_error_code", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("negative_prompt", sa.Text(), nullable=True),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("aspect_ratio", sa.String(), nullable=False),
        sa.Column("upscale_json", sa.JSON(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("storage_uri", sa.String(), nullable=True),
        sa.Column("mime_type", sa.String(), nullable=True),
        sa.Column("source_result_url", sa.String(), nullable=True),
        sa.Column("selected", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("locked", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.Column("completed_at", sa.String(), nullable=True),
        sa.Column("poll_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_index("ix_media_tasks_batch_id", "media_tasks", ["batch_id"])
    op.create_index("ix_media_tasks_purpose", "media_tasks", ["purpose"])
    op.create_index("ix_media_tasks_project_id", "media_tasks", ["project_id"])
    op.create_index("ix_media_tasks_style_bible_revision_id", "media_tasks", ["style_bible_revision_id"])
    op.create_index("ix_media_tasks_entity_type", "media_tasks", ["entity_type"])
    op.create_index("ix_media_tasks_entity_id", "media_tasks", ["entity_id"])
    op.create_index("ix_media_tasks_status", "media_tasks", ["status"])
    op.create_index("ix_media_tasks_external_provider", "media_tasks", ["external_provider"])
    op.create_index("ix_media_tasks_external_task_id", "media_tasks", ["external_task_id"])
    op.create_index("ix_media_tasks_provider_status", "media_tasks", ["provider_status"])
    op.create_index("ix_media_tasks_error_code", "media_tasks", ["error_code"])
    op.create_index("ix_media_tasks_created_at", "media_tasks", ["created_at"])
    op.create_index("ix_media_tasks_completed_at", "media_tasks", ["completed_at"])


def downgrade() -> None:
    op.drop_index("ix_media_tasks_completed_at", table_name="media_tasks")
    op.drop_index("ix_media_tasks_created_at", table_name="media_tasks")
    op.drop_index("ix_media_tasks_error_code", table_name="media_tasks")
    op.drop_index("ix_media_tasks_provider_status", table_name="media_tasks")
    op.drop_index("ix_media_tasks_external_task_id", table_name="media_tasks")
    op.drop_index("ix_media_tasks_external_provider", table_name="media_tasks")
    op.drop_index("ix_media_tasks_status", table_name="media_tasks")
    op.drop_index("ix_media_tasks_entity_id", table_name="media_tasks")
    op.drop_index("ix_media_tasks_entity_type", table_name="media_tasks")
    op.drop_index("ix_media_tasks_style_bible_revision_id", table_name="media_tasks")
    op.drop_index("ix_media_tasks_project_id", table_name="media_tasks")
    op.drop_index("ix_media_tasks_purpose", table_name="media_tasks")
    op.drop_index("ix_media_tasks_batch_id", table_name="media_tasks")
    op.drop_table("media_tasks")
    op.drop_index("ix_media_batches_created_at", table_name="media_batches")
    op.drop_index("ix_media_batches_style_bible_revision_id", table_name="media_batches")
    op.drop_index("ix_media_batches_project_id", table_name="media_batches")
    op.drop_index("ix_media_batches_purpose", table_name="media_batches")
    op.drop_table("media_batches")
