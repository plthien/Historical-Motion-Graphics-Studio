"""add durable SQLite ownership fields for Render

Revision ID: 030_render_durable_sqlite
Revises: 029_voice_first_elevenlabs
Create Date: 2026-09-07

Task 1 adds only persisted scheduling and fenced reconcile ownership. Render
execution remains RQ-owned until the later hard-cutover task.
"""

from alembic import op
import sqlalchemy as sa


revision = "030_render_durable_sqlite"
down_revision = "029_voice_first_elevenlabs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("render_operations") as batch:
        batch.add_column(sa.Column("next_reconcile_at", sa.String(), nullable=True))
        batch.add_column(sa.Column("reconcile_owner", sa.String(), nullable=True))
        batch.add_column(sa.Column("reconcile_token", sa.String(), nullable=True))
        batch.add_column(sa.Column("reconcile_lease_expires_at", sa.String(), nullable=True))
        batch.add_column(sa.Column("state_revision", sa.Integer(), nullable=False, server_default="0"))

    op.create_index(
        "ix_render_operations_due",
        "render_operations",
        ["status", "next_reconcile_at"],
        unique=False,
    )
    op.create_index(
        "ix_render_operations_reconcile_lease",
        "render_operations",
        ["reconcile_lease_expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_render_operations_reconcile_lease", table_name="render_operations")
    op.drop_index("ix_render_operations_due", table_name="render_operations")
    with op.batch_alter_table("render_operations") as batch:
        batch.drop_column("state_revision")
        batch.drop_column("reconcile_lease_expires_at")
        batch.drop_column("reconcile_token")
        batch.drop_column("reconcile_owner")
        batch.drop_column("next_reconcile_at")
