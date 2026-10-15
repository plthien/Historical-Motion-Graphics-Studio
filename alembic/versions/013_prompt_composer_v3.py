"""Phase 10D Prompt Composer runtime.

Revision ID: 013_prompt_composer_v3
Revises: 012_visual_planner_v3
Create Date: 2026-07-13
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "013_prompt_composer_v3"
down_revision = "012_visual_planner_v3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql(
            "ALTER TABLE projects ADD COLUMN prompt_bundle_artifact_id "
            "VARCHAR REFERENCES artifacts (artifact_id)"
        )
    else:
        op.add_column("projects", sa.Column("prompt_bundle_artifact_id", sa.String(), nullable=True))
        op.create_foreign_key(
            "fk_projects_prompt_bundle_artifact_id_artifacts",
            "projects",
            "artifacts",
            ["prompt_bundle_artifact_id"],
            ["artifact_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql("ALTER TABLE projects DROP COLUMN prompt_bundle_artifact_id")
    else:
        op.drop_constraint("fk_projects_prompt_bundle_artifact_id_artifacts", "projects", type_="foreignkey")
        op.drop_column("projects", "prompt_bundle_artifact_id")
