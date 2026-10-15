"""persist immutable media provider poll claim expiry

Revision ID: 009_media_poll_claim_expiry
Revises: 008_media_poll_claim
Create Date: 2026-07-09
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "009_media_poll_claim_expiry"
down_revision = "008_media_poll_claim"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("media_tasks") as batch_op:
        batch_op.add_column(sa.Column("poll_claim_expires_at", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("media_tasks") as batch_op:
        batch_op.drop_column("poll_claim_expires_at")
