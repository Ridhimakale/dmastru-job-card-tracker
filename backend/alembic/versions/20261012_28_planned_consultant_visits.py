"""Add independent consultant visit planner bookings."""
from alembic import op
import sqlalchemy as sa

revision = "20261012_28"
down_revision = "20261012_27"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        "planned_visits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_card_id", sa.Integer(), sa.ForeignKey("job_cards.id", ondelete="CASCADE"), nullable=False),
        sa.Column("milestone_id", sa.Integer(), sa.ForeignKey("milestones.id", ondelete="CASCADE"), nullable=False),
        sa.Column("consultant_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("visit_date", sa.Date(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("expected_duration_minutes", sa.Integer(), nullable=False),
        sa.Column("location", sa.String(240), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="PLANNED"),
        sa.Column("change_reason", sa.Text()),
        sa.Column("actual_date", sa.Date()),
        sa.Column("actual_start_time", sa.Time()),
        sa.Column("actual_duration_minutes", sa.Integer()),
        sa.Column("outcome", sa.Text()),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("job_card_id", "milestone_id", "consultant_id", "visit_date", "status"):
        op.create_index(f"ix_planned_visits_{column}", "planned_visits", [column])
    op.create_index("ix_planned_visits_consultant_date", "planned_visits", ["consultant_id", "visit_date"])

def downgrade():
    op.drop_table("planned_visits")
