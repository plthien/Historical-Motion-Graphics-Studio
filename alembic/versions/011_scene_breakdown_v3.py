"""Add the current Project Scene Plan pointer.
Revision ID: 011_scene_breakdown_v3
Revises: 010_style_bible_draft_foundation
"""
from alembic import op
import sqlalchemy as sa
revision="011_scene_breakdown_v3"; down_revision="010_style_bible_draft_foundation"; branch_labels=None; depends_on=None
def _check():
 bind=op.get_bind()
 if bind.dialect.name=="sqlite":
  rows=list(bind.exec_driver_sql("PRAGMA foreign_key_check").fetchall())
  if rows: raise RuntimeError(f"SQLite foreign key violations after Phase 9 schema change: {rows!r}")
def upgrade():
 bind=op.get_bind()
 if bind.dialect.name=="sqlite": bind.exec_driver_sql("ALTER TABLE projects ADD COLUMN scene_plan_artifact_id VARCHAR REFERENCES artifacts (artifact_id)")
 else:
  op.add_column("projects",sa.Column("scene_plan_artifact_id",sa.String(),nullable=True)); op.create_foreign_key("fk_projects_scene_plan_artifact","projects","artifacts",["scene_plan_artifact_id"],["artifact_id"])
 _check()
def downgrade():
 bind=op.get_bind()
 if bind.dialect.name=="sqlite": bind.exec_driver_sql("ALTER TABLE projects DROP COLUMN scene_plan_artifact_id")
 else: op.drop_constraint("fk_projects_scene_plan_artifact","projects",type_="foreignkey"); op.drop_column("projects","scene_plan_artifact_id")
 _check()
