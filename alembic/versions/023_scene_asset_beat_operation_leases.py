"""Add Scene Asset beat operation leases.

Revision ID: 023_scene_asset_beat_operation_leases
Revises: 022_scene_asset_beat_claims
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "023_scene_asset_beat_operation_leases"
down_revision = "022_scene_asset_beat_claims"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scene_asset_beat_operation_leases",
        sa.Column("lease_id", sa.String(), primary_key=True),
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.project_id"), nullable=False),
        sa.Column("beat_id", sa.String(), nullable=False),
        sa.Column("owner_kind", sa.String(), nullable=False),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.UniqueConstraint("project_id", "beat_id", name="ux_scene_asset_beat_operation_leases_project_beat"),
    )
    op.create_index("ix_scene_asset_beat_operation_leases_project_id", "scene_asset_beat_operation_leases", ["project_id"])
    op.create_index("ix_scene_asset_beat_operation_leases_beat_id", "scene_asset_beat_operation_leases", ["beat_id"])
    op.create_index("ix_scene_asset_beat_operation_leases_owner_kind", "scene_asset_beat_operation_leases", ["owner_kind"])
    op.create_index("ix_scene_asset_beat_operation_leases_owner_id", "scene_asset_beat_operation_leases", ["owner_id"])
    op.create_index("ix_scene_asset_beat_operation_leases_created_at", "scene_asset_beat_operation_leases", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_scene_asset_beat_operation_leases_created_at", table_name="scene_asset_beat_operation_leases")
    op.drop_index("ix_scene_asset_beat_operation_leases_owner_id", table_name="scene_asset_beat_operation_leases")
    op.drop_index("ix_scene_asset_beat_operation_leases_owner_kind", table_name="scene_asset_beat_operation_leases")
    op.drop_index("ix_scene_asset_beat_operation_leases_beat_id", table_name="scene_asset_beat_operation_leases")
    op.drop_index("ix_scene_asset_beat_operation_leases_project_id", table_name="scene_asset_beat_operation_leases")
    op.drop_table("scene_asset_beat_operation_leases")