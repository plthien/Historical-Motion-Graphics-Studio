"""Add Scene Asset beat claims.

Revision ID: 022_scene_asset_beat_claims
Revises: 021_external_authoring_reservations
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "022_scene_asset_beat_claims"
down_revision = "021_external_authoring_reservations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scene_asset_beat_claims",
        sa.Column("claim_id", sa.String(), primary_key=True),
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.project_id"), nullable=False),
        sa.Column("beat_id", sa.String(), nullable=False),
        sa.Column("operation_id", sa.String(), sa.ForeignKey("scene_asset_operations.operation_id"), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.UniqueConstraint("project_id", "beat_id", name="ux_scene_asset_beat_claims_project_beat"),
    )
    op.create_index("ix_scene_asset_beat_claims_project_id", "scene_asset_beat_claims", ["project_id"])
    op.create_index("ix_scene_asset_beat_claims_beat_id", "scene_asset_beat_claims", ["beat_id"])
    op.create_index("ix_scene_asset_beat_claims_operation_id", "scene_asset_beat_claims", ["operation_id"])
    op.create_index("ix_scene_asset_beat_claims_created_at", "scene_asset_beat_claims", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_scene_asset_beat_claims_created_at", table_name="scene_asset_beat_claims")
    op.drop_index("ix_scene_asset_beat_claims_operation_id", table_name="scene_asset_beat_claims")
    op.drop_index("ix_scene_asset_beat_claims_beat_id", table_name="scene_asset_beat_claims")
    op.drop_index("ix_scene_asset_beat_claims_project_id", table_name="scene_asset_beat_claims")
    op.drop_table("scene_asset_beat_claims")