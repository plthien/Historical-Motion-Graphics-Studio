"""Add the current Project Style Bible pointer.

Revision ID: 010_style_bible_draft_foundation
Revises: 009_media_poll_claim_expiry
Create Date: 2026-07-09
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "010_style_bible_draft_foundation"
down_revision = "009_media_poll_claim_expiry"
branch_labels = None
depends_on = None


def _assert_foreign_key_integrity() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        return
    violations = list(bind.exec_driver_sql("PRAGMA foreign_key_check").fetchall())
    if violations:
        raise RuntimeError(f"SQLite foreign key violations after Phase 8A schema change: {violations!r}")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql(
            "ALTER TABLE projects ADD COLUMN style_bible_artifact_id VARCHAR "
            "REFERENCES artifacts (artifact_id)"
        )
    else:
        op.add_column("projects", sa.Column("style_bible_artifact_id", sa.String(), nullable=True))
        op.create_foreign_key(
            "fk_projects_style_bible_artifact",
            "projects",
            "artifacts",
            ["style_bible_artifact_id"],
            ["artifact_id"],
        )
    _assert_foreign_key_integrity()


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql("ALTER TABLE projects DROP COLUMN style_bible_artifact_id")
    else:
        op.drop_constraint("fk_projects_style_bible_artifact", "projects", type_="foreignkey")
        op.drop_column("projects", "style_bible_artifact_id")
    _assert_foreign_key_integrity()
