"""Separate client QCI milestone payments from D'Mastru invoices to QCI."""
from alembic import op
import sqlalchemy as sa

revision = "20261019_49"
down_revision = "20261018_48"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        "client_milestone_payments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("milestone_id", sa.Integer(), sa.ForeignKey("milestones.id", ondelete="CASCADE"), nullable=False),
        sa.Column("external_reference", sa.String(length=120), nullable=True),
        sa.Column("remarks", sa.Text(), nullable=True),
        sa.Column("confirmed_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_client_milestone_payments_milestone_id", "client_milestone_payments", ["milestone_id"], unique=True)
    op.create_index("ix_client_milestone_payments_confirmed_by", "client_milestone_payments", ["confirmed_by"])
    op.create_index("ix_client_milestone_payments_confirmed_at", "client_milestone_payments", ["confirmed_at"])
    # Keep milestones that the previous workflow had already unlocked available.
    op.execute(sa.text("""
        INSERT INTO client_milestone_payments (milestone_id, remarks, confirmed_by, confirmed_at)
        SELECT m.id, 'Legacy payment confirmation retained during QCI payment-flow migration.', p.created_by, p.updated_at
        FROM milestones m
        JOIN payments p ON p.milestone_id = m.id AND p.status = 'PAID'
        WHERE NOT EXISTS (SELECT 1 FROM client_milestone_payments c WHERE c.milestone_id = m.id)
    """))
    op.execute(sa.text("""
        UPDATE workflow_actions
        SET status = 'CANCELLED', remarks = 'Replaced by separate client-to-QCI payment confirmation and D’Mastru-to-QCI invoice tracking.'
        WHERE template_code IN ('INITIAL_MILESTONE_PAYMENT', 'MILESTONE_NEXT_PAYMENT')
          AND status IN ('OPEN', 'IN_PROGRESS', 'WAITING', 'ON_HOLD')
    """))

def downgrade():
    op.drop_index("ix_client_milestone_payments_confirmed_at", table_name="client_milestone_payments")
    op.drop_index("ix_client_milestone_payments_confirmed_by", table_name="client_milestone_payments")
    op.drop_index("ix_client_milestone_payments_milestone_id", table_name="client_milestone_payments")
    op.drop_table("client_milestone_payments")
