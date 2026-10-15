"""media provider poll generation token

Revision ID: 007_media_poll_revision
Revises: 006_media_provider_submitted_at
Create Date: 2026-07-08
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "007_media_poll_revision"
down_revision = "006_media_provider_submitted_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("media_tasks") as batch_op:
        batch_op.add_column(sa.Column("poll_revision", sa.Integer(), nullable=False, server_default=sa.text("0")))


def downgrade() -> None:
    with op.batch_alter_table("media_tasks") as batch_op:
        batch_op.drop_column("poll_revision")
