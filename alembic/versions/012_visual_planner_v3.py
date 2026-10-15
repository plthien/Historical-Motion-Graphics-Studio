"""Add the current Project Visual Plan pointer.

Revision ID: 012_visual_planner_v3
Revises: 011_scene_breakdown_v3
"""

from alembic import op
import sqlalchemy as sa

revision = "012_visual_planner_v3"
down_revision = "011_scene_breakdown_v3"
branch_labels = None
depends_on = None


def _check_foreign_keys() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        return
    rows = list(bind.exec_driver_sql("PRAGMA foreign_key_check").fetchall())
    if rows:
        raise RuntimeError(f"SQLite foreign key violations after Phase 10B schema change: {rows!r}")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql(
            "ALTER TABLE projects ADD COLUMN visual_plan_artifact_id "
            "VARCHAR REFERENCES artifacts (artifact_id)"
        )
    else:
        op.add_column(
            "projects",
            sa.Column("visual_plan_artifact_id", sa.String(), nullable=True),
        )
        op.create_foreign_key(
            "fk_projects_visual_plan_artifact",
            "projects",
            "artifacts",
            ["visual_plan_artifact_id"],
            ["artifact_id"],
        )
    _check_foreign_keys()


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql("ALTER TABLE projects DROP COLUMN visual_plan_artifact_id")
    else:
        op.drop_constraint(
            "fk_projects_visual_plan_artifact",
            "projects",
            type_="foreignkey",
        )
        op.drop_column("projects", "visual_plan_artifact_id")
    _check_foreign_keys()
