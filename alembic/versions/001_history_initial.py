"""Fresh History schema; no old YTB migration parent."""
from alembic import op
import sqlalchemy as sa
revision="001_history_initial"
down_revision=None
branch_labels=None
depends_on=None

def upgrade():
    op.create_table("history_meta",
       sa.Column("id",sa.Integer,primary_key=True),
       sa.Column("schema_key",sa.String(64),nullable=False),
       sa.Column("schema_version",sa.Integer,nullable=False),
       sa.CheckConstraint("id = 1",name="ck_history_meta_singleton"))
    op.execute("INSERT INTO history_meta (id,schema_key,schema_version) VALUES (1,'historical-motion-graphics-studio',1)")
    op.create_table("channels",
       sa.Column("channel_id",sa.String(32),primary_key=True),
       sa.Column("name",sa.String(100),nullable=False,unique=True),
       sa.Column("language",sa.String(32),nullable=False),
       sa.Column("niche",sa.String(120),nullable=False),
       sa.Column("history_dna_json",sa.Text,nullable=False),
       sa.Column("revision",sa.Integer,nullable=False),
       sa.Column("created_at",sa.String(40),nullable=False))
    op.create_table("projects",
       sa.Column("project_id",sa.String(32),primary_key=True),
       sa.Column("channel_id",sa.String(32),sa.ForeignKey("channels.channel_id",ondelete="RESTRICT"),nullable=False),
       sa.Column("name",sa.String(120),nullable=False),
       sa.Column("history_context_json",sa.Text,nullable=False),
       sa.Column("created_at",sa.String(40),nullable=False),
       sa.UniqueConstraint("channel_id","name",name="uq_history_project_name"))
    op.create_index("ix_projects_channel","projects",["channel_id"])
    allowed="'topic','script_analysis','voice','assets','visual_director','motion_timeline','final_video'"
    op.create_table("artifacts",
       sa.Column("artifact_id",sa.String(32),primary_key=True),
       sa.Column("project_id",sa.String(32),sa.ForeignKey("projects.project_id",ondelete="RESTRICT"),nullable=False),
       sa.Column("kind",sa.String(24),nullable=False),
       sa.Column("content_hash",sa.String(64),nullable=False),
       sa.Column("storage_uri",sa.String(240),nullable=False),
       sa.Column("status",sa.String(16),nullable=False),
       sa.Column("version",sa.Integer,nullable=False),
       sa.Column("created_at",sa.String(40),nullable=False),
       sa.CheckConstraint(f"kind IN ({allowed})",name="ck_history_artifact_kind"),
       sa.CheckConstraint("status IN ('candidate','approved')",name="ck_history_artifact_status"),
       sa.CheckConstraint("version >= 1",name="ck_history_artifact_version"),
       sa.UniqueConstraint("project_id","kind","version",name="uq_history_artifact_version"))
    op.create_index("ix_history_artifacts_project","artifacts",["project_id"])
    op.create_table("stage_heads",
       sa.Column("project_id",sa.String(32),sa.ForeignKey("projects.project_id",ondelete="RESTRICT"),primary_key=True),
       sa.Column("stage_key",sa.String(24),primary_key=True),
       sa.Column("artifact_id",sa.String(32),sa.ForeignKey("artifacts.artifact_id",ondelete="RESTRICT"),nullable=False),
       sa.Column("revision",sa.Integer,nullable=False),
       sa.Column("updated_at",sa.String(40),nullable=False),
       sa.CheckConstraint(f"stage_key IN ({allowed})",name="ck_history_stage_key"),
       sa.CheckConstraint("revision >= 1",name="ck_history_stage_revision"))
    op.create_table("authoring_reservations",
       sa.Column("reservation_id",sa.String(32),primary_key=True),
       sa.Column("project_id",sa.String(32),sa.ForeignKey("projects.project_id",ondelete="RESTRICT"),nullable=False),
       sa.Column("stage_key",sa.String(24),nullable=False),
       sa.Column("parent_fingerprint",sa.String(64),nullable=False),
       sa.Column("status",sa.String(16),nullable=False),
       sa.Column("created_at",sa.String(40),nullable=False),
       sa.Column("expires_at",sa.String(40),nullable=False),
       sa.CheckConstraint(f"stage_key IN ({allowed})",name="ck_history_reservation_stage"),
       sa.CheckConstraint("status IN ('pending','consumed','expired')",name="ck_history_reservation_status"))
    op.create_index("ix_history_reservations_project","authoring_reservations",["project_id","stage_key"])

def downgrade():
    for table in ("authoring_reservations","stage_heads","artifacts","projects","channels","history_meta"):
        op.drop_table(table)
