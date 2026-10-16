"""P2 authoring: versioned ChannelDNA, source-bound prompt reservations and review candidates."""
from alembic import op
import sqlalchemy as sa
revision="002_history_authoring_p2"
down_revision="001_history_initial"
branch_labels=None
depends_on=None

def upgrade():
    op.create_table("p2_channel_dna",
        sa.Column("channel_id",sa.String(32),sa.ForeignKey("channels.channel_id",ondelete="RESTRICT"),primary_key=True),
        sa.Column("revision",sa.Integer,nullable=False),
        sa.Column("json_text",sa.Text,nullable=False),
        sa.Column("content_hash",sa.String(64),nullable=False),
        sa.Column("updated_at",sa.String(40),nullable=False))
    op.create_table("p2_channel_dna_history",
        sa.Column("channel_id",sa.String(32),sa.ForeignKey("channels.channel_id",ondelete="RESTRICT"),primary_key=True),
        sa.Column("revision",sa.Integer,primary_key=True),
        sa.Column("json_text",sa.Text,nullable=False),
        sa.Column("content_hash",sa.String(64),nullable=False))
    op.create_table("p2_prompts",
        sa.Column("reservation_id",sa.String(32),primary_key=True),
        sa.Column("project_id",sa.String(32),sa.ForeignKey("projects.project_id",ondelete="RESTRICT"),nullable=False),
        sa.Column("stage_key",sa.String(24),nullable=False),
        sa.Column("source_hash",sa.String(64),nullable=False),
        sa.Column("input_json",sa.Text,nullable=False),
        sa.Column("prompt_text",sa.Text,nullable=False),
        sa.Column("ids_json",sa.Text,nullable=False),
        sa.Column("status",sa.String(16),nullable=False),
        sa.Column("created_at",sa.String(40),nullable=False),
        sa.Column("expires_at",sa.String(40),nullable=False),
        sa.CheckConstraint("stage_key IN ('topic','script_analysis')"),
        sa.CheckConstraint("status IN ('pending','imported','consumed','superseded')"))
    op.create_index("ix_p2_prompts_owner","p2_prompts",["project_id","stage_key","created_at"])
    op.create_table("p2_candidates",
        sa.Column("candidate_id",sa.String(32),primary_key=True),
        sa.Column("reservation_id",sa.String(32),sa.ForeignKey("p2_prompts.reservation_id",ondelete="RESTRICT"),nullable=False,unique=True),
        sa.Column("project_id",sa.String(32),sa.ForeignKey("projects.project_id",ondelete="RESTRICT"),nullable=False),
        sa.Column("stage_key",sa.String(24),nullable=False),
        sa.Column("source_hash",sa.String(64),nullable=False),
        sa.Column("content_bytes",sa.LargeBinary,nullable=False),
        sa.Column("content_hash",sa.String(64),nullable=False),
        sa.Column("status",sa.String(16),nullable=False),
        sa.Column("created_at",sa.String(40),nullable=False),
        sa.CheckConstraint("status IN ('candidate','approved')"))
    op.create_index("ix_p2_candidates_owner","p2_candidates",["project_id","stage_key","created_at"])
    op.create_table("p2_approvals",
        sa.Column("artifact_id",sa.String(32),sa.ForeignKey("artifacts.artifact_id",ondelete="RESTRICT"),primary_key=True),
        sa.Column("project_id",sa.String(32),sa.ForeignKey("projects.project_id",ondelete="RESTRICT"),nullable=False),
        sa.Column("stage_key",sa.String(24),nullable=False),
        sa.Column("source_hash",sa.String(64),nullable=False),
        sa.Column("parent_artifact_id",sa.String(32),nullable=True),
        sa.Column("channel_dna_hash",sa.String(64),nullable=False),
        sa.Column("approved_at",sa.String(40),nullable=False))
    op.create_index("ix_p2_approvals_owner","p2_approvals",["project_id","stage_key"])

def downgrade():
    for name in ("p2_approvals","p2_candidates","p2_prompts","p2_channel_dna_history","p2_channel_dna"):
        op.drop_table(name)
