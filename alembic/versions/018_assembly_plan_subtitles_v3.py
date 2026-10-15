"""Phase 13 assembly plan and subtitles.

Revision ID: 018_assembly_plan_subtitles_v3
Revises: 017_voice_generation_v3
Create Date: 2026-07-19
"""
from __future__ import annotations
from alembic import op
import sqlalchemy as sa

revision = "018_assembly_plan_subtitles_v3"
down_revision = "017_voice_generation_v3"
branch_labels = None
depends_on = None


def _add_pointer(name: str) -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql(f"ALTER TABLE projects ADD COLUMN {name} VARCHAR REFERENCES artifacts(artifact_id)")
    else:
        op.add_column("projects", sa.Column(name, sa.String(), nullable=True))
        op.create_foreign_key(f"fk_projects_{name}_artifacts", "projects", "artifacts", [name], ["artifact_id"])
    op.create_index(f"ix_projects_{name}", "projects", [name], unique=False)


def upgrade() -> None:
    for name in ("assembly_plan_artifact_id", "subtitle_srt_artifact_id", "subtitle_vtt_artifact_id"):
        _add_pointer(name)


def downgrade() -> None:
    for name in reversed(("assembly_plan_artifact_id", "subtitle_srt_artifact_id", "subtitle_vtt_artifact_id")):
        op.drop_index(f"ix_projects_{name}", table_name="projects")
        bind = op.get_bind()
        if bind.dialect.name == "sqlite":
            bind.exec_driver_sql(f"ALTER TABLE projects DROP COLUMN {name}")
        else:
            op.drop_constraint(f"fk_projects_{name}_artifacts", "projects", type_="foreignkey")
            op.drop_column("projects", name)
