"""Add milestone review and Unit Representative confirmation history."""
from alembic import op
import sqlalchemy as sa

revision="20261010_21"
down_revision="20261010_20"
branch_labels=None
depends_on=None

def upgrade():
    op.create_table(
        "milestone_review_history",
        sa.Column("id",sa.Integer(),primary_key=True),
        sa.Column("milestone_id",sa.Integer(),sa.ForeignKey("milestones.id",ondelete="CASCADE"),nullable=False),
        sa.Column("cycle",sa.Integer(),nullable=False),
        sa.Column("stage",sa.String(32),nullable=False),
        sa.Column("decision",sa.String(24),nullable=False),
        sa.Column("comment",sa.Text(),nullable=True),
        sa.Column("decided_by",sa.Integer(),sa.ForeignKey("users.id"),nullable=False),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
    )
    for column in ("milestone_id","cycle","stage","decision","decided_by","created_at"):
        op.create_index(f"ix_milestone_review_history_{column}","milestone_review_history",[column])
    op.create_index("ix_milestone_review_cycle_stage","milestone_review_history",["milestone_id","cycle","stage"])

def downgrade():
    op.drop_table("milestone_review_history")
