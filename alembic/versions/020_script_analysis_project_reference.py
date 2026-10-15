"""Add the independent Script Analysis project pointer.

Revision ID: 020_script_analysis_project_reference
Revises: 019_ffmpeg_render_v3
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "020_script_analysis_project_reference"
down_revision = "019_ffmpeg_render_v3"
branch_labels = None
depends_on = None


def _project_columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns("projects")}


def _assert_foreign_key_integrity() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        return
    violations = list(bind.exec_driver_sql("PRAGMA foreign_key_check").fetchall())
    if violations:
        raise RuntimeError(
            f"SQLite foreign key violations after Script Analysis schema change: {violations!r}"
        )


def upgrade() -> None:
    if "script_analysis_artifact_id" in _project_columns():
        return
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql(
            "ALTER TABLE projects ADD COLUMN script_analysis_artifact_id VARCHAR "
            "REFERENCES artifacts (artifact_id)"
        )
    else:
        op.add_column(
            "projects",
            sa.Column("script_analysis_artifact_id", sa.String(), nullable=True),
        )
        op.create_foreign_key(
            "fk_projects_script_analysis_artifact",
            "projects",
            "artifacts",
            ["script_analysis_artifact_id"],
            ["artifact_id"],
        )
    _assert_foreign_key_integrity()


def downgrade() -> None:
    if "script_analysis_artifact_id" not in _project_columns():
        return
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql(
            "ALTER TABLE projects DROP COLUMN script_analysis_artifact_id"
        )
    else:
        op.drop_constraint(
            "fk_projects_script_analysis_artifact",
            "projects",
            type_="foreignkey",
        )
        op.drop_column("projects", "script_analysis_artifact_id")
    _assert_foreign_key_integrity()
