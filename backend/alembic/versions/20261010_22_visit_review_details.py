"""Add visit schedule/details and immutable review history."""
from alembic import op
import sqlalchemy as sa

revision="20261010_22"
down_revision="20261010_21"
branch_labels=None
depends_on=None

def upgrade():
    op.add_column("visits",sa.Column("start_time",sa.Time(),nullable=True))
    op.add_column("visits",sa.Column("end_time",sa.Time(),nullable=True))
    op.add_column("visits",sa.Column("location",sa.String(240),nullable=True))
    op.add_column("visits",sa.Column("findings",sa.Text(),nullable=True))
    op.create_table(
        "visit_review_history",
        sa.Column("id",sa.Integer(),primary_key=True),
        sa.Column("visit_id",sa.Integer(),sa.ForeignKey("visits.id",ondelete="CASCADE"),nullable=False),
        sa.Column("cycle",sa.Integer(),nullable=False),
        sa.Column("decision",sa.String(24),nullable=False),
        sa.Column("comment",sa.Text(),nullable=True),
        sa.Column("decided_by",sa.Integer(),sa.ForeignKey("users.id"),nullable=False),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
    )
    for column in ("visit_id","cycle","decision","decided_by","created_at"):
        op.create_index(f"ix_visit_review_history_{column}","visit_review_history",[column])
    op.create_index("ix_visit_review_cycle","visit_review_history",["visit_id","cycle"])

def downgrade():
    op.drop_table("visit_review_history")
    op.drop_column("visits","findings")
    op.drop_column("visits","location")
    op.drop_column("visits","end_time")
    op.drop_column("visits","start_time")
