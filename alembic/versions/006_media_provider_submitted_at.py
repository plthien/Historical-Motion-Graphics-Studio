"""media provider submitted timestamp

Revision ID: 006_media_provider_submitted_at
Revises: 005_project_selected_topic
Create Date: 2026-07-08
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "006_media_provider_submitted_at"
down_revision = "005_project_selected_topic"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("media_tasks") as batch_op:
        batch_op.add_column(sa.Column("provider_submitted_at", sa.String(), nullable=True))
        batch_op.create_index("ix_media_tasks_provider_submitted_at", ["provider_submitted_at"])


def downgrade() -> None:
    with op.batch_alter_table("media_tasks") as batch_op:
        batch_op.drop_index("ix_media_tasks_provider_submitted_at")
        batch_op.drop_column("provider_submitted_at")
