"""v3 product foundation

Revision ID: 002_v3_product_foundation
Revises: 001_v3_platform_baseline
Create Date: 2026-07-03
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "002_v3_product_foundation"
down_revision = "001_v3_platform_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "channels",
        sa.Column("channel_id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("language", sa.String(), nullable=False),
        sa.Column("niche", sa.Text(), nullable=False),
        sa.Column("audience", sa.Text(), nullable=False),
        sa.Column("channel_dna_json", sa.JSON(), nullable=True),
        sa.Column("channel_context_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
    )
    op.create_index("ix_channels_name", "channels", ["name"])
    op.create_index("ix_channels_created_at", "channels", ["created_at"])

    op.create_table(
        "projects",
        sa.Column("project_id", sa.String(), primary_key=True),
        sa.Column("channel_id", sa.String(), sa.ForeignKey("channels.channel_id"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("channel_context_json", sa.JSON(), nullable=False),
        sa.Column("channel_dna_json", sa.JSON(), nullable=False),
        sa.Column("topic_ideas_artifact_id", sa.String(), sa.ForeignKey("artifacts.artifact_id"), nullable=True),
        sa.Column("script_artifact_id", sa.String(), sa.ForeignKey("artifacts.artifact_id"), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
    )
    op.create_index("ix_projects_channel_id", "projects", ["channel_id"])
    op.create_index("ix_projects_name", "projects", ["name"])
    op.create_index("ix_projects_created_at", "projects", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_projects_created_at", table_name="projects")
    op.drop_index("ix_projects_name", table_name="projects")
    op.drop_index("ix_projects_channel_id", table_name="projects")
    op.drop_table("projects")
    op.drop_index("ix_channels_created_at", table_name="channels")
    op.drop_index("ix_channels_name", table_name="channels")
    op.drop_table("channels")
