"""persisted single-owner media provider poll claim

Revision ID: 008_media_poll_claim
Revises: 007_media_poll_revision
Create Date: 2026-07-08
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "008_media_poll_claim"
down_revision = "007_media_poll_revision"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("media_tasks") as batch_op:
        batch_op.add_column(sa.Column("poll_claim_token", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("poll_claimed_at", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("media_tasks") as batch_op:
        batch_op.drop_column("poll_claimed_at")
        batch_op.drop_column("poll_claim_token")
