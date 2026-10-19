"""Track breached enquiry response and follow-up SLAs."""
from alembic import op
import sqlalchemy as sa

revision = "20261016_30"
down_revision = "20261012_29"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("enquiry_followups", sa.Column("next_follow_up_type", sa.String(32), nullable=True))
    op.create_index("ix_enquiry_followups_next_follow_up_type", "enquiry_followups", ["next_follow_up_type"])
    op.create_table(
        "enquiry_sla_breaches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enquiry_id", sa.Uuid(), sa.ForeignKey("enquiries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("follow_up_id", sa.Integer(), sa.ForeignKey("enquiry_followups.id", ondelete="CASCADE"), nullable=True),
        sa.Column("breach_type", sa.String(32), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("breached_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="OPEN"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("breach_type IN ('REQUEST_RESPONSE','FOLLOW_UP')", name="ck_enquiry_sla_breach_type"),
        sa.CheckConstraint("status IN ('OPEN','RESOLVED')", name="ck_enquiry_sla_breach_status"),
    )
    for column in ("enquiry_id", "follow_up_id", "breach_type", "due_at", "breached_at", "status", "created_at"):
        op.create_index(f"ix_enquiry_sla_breaches_{column}", "enquiry_sla_breaches", [column])
    op.create_index("uq_enquiry_sla_followup_type", "enquiry_sla_breaches", ["follow_up_id", "breach_type"], unique=True, sqlite_where=sa.text("follow_up_id IS NOT NULL"), postgresql_where=sa.text("follow_up_id IS NOT NULL"))
    op.create_index("uq_enquiry_sla_initial_response", "enquiry_sla_breaches", ["enquiry_id", "breach_type"], unique=True, sqlite_where=sa.text("follow_up_id IS NULL"), postgresql_where=sa.text("follow_up_id IS NULL"))

def downgrade():
    op.drop_index("uq_enquiry_sla_initial_response", table_name="enquiry_sla_breaches")
    op.drop_index("uq_enquiry_sla_followup_type", table_name="enquiry_sla_breaches")
    for column in ("created_at", "status", "breached_at", "due_at", "breach_type", "follow_up_id", "enquiry_id"):
        op.drop_index(f"ix_enquiry_sla_breaches_{column}", table_name="enquiry_sla_breaches")
    op.drop_table("enquiry_sla_breaches")
    op.drop_index("ix_enquiry_followups_next_follow_up_type", table_name="enquiry_followups")
    op.drop_column("enquiry_followups", "next_follow_up_type")
