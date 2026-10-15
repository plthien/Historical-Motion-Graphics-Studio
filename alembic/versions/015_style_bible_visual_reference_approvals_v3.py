"""Phase 8D visual reference approvals.

Revision ID: 015_style_bible_visual_reference_approvals_v3
Revises: 014_style_bible_visual_references_v3
Create Date: 2026-07-16
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "015_style_bible_visual_reference_approvals_v3"
down_revision = "014_style_bible_visual_references_v3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql(
            "ALTER TABLE projects ADD COLUMN "
            "style_bible_visual_reference_approval_set_artifact_id "
            "VARCHAR REFERENCES artifacts(artifact_id)"
        )
    else:
        op.add_column(
            "projects",
            sa.Column(
                "style_bible_visual_reference_approval_set_artifact_id",
                sa.String(),
                nullable=True,
            ),
        )
        op.create_foreign_key(
            "fk_projects_style_bible_visual_reference_approval_set_artifact_id_artifacts",
            "projects",
            "artifacts",
            ["style_bible_visual_reference_approval_set_artifact_id"],
            ["artifact_id"],
        )
    op.create_index(
        "ix_projects_style_bible_visual_reference_approval_set_artifact_id",
        "projects",
        ["style_bible_visual_reference_approval_set_artifact_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_projects_style_bible_visual_reference_approval_set_artifact_id",
        table_name="projects",
    )
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql(
            "ALTER TABLE projects DROP COLUMN "
            "style_bible_visual_reference_approval_set_artifact_id"
        )
    else:
        op.drop_constraint(
            "fk_projects_style_bible_visual_reference_approval_set_artifact_id_artifacts",
            "projects",
            type_="foreignkey",
        )
        op.drop_column(
            "projects",
            "style_bible_visual_reference_approval_set_artifact_id",
        )
