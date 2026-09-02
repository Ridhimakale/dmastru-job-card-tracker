"""Add milestone payment and portal-report dates."""
from alembic import op
import sqlalchemy as sa

revision="20260826_05"
down_revision="20260826_04"
branch_labels=None
depends_on=None

def upgrade():
    with op.batch_alter_table("milestones") as batch:
        batch.add_column(sa.Column("payment_date",sa.Date(),nullable=True))
        batch.add_column(sa.Column("portal_report_submission_date",sa.Date(),nullable=True))

def downgrade():
    with op.batch_alter_table("milestones") as batch:
        batch.drop_column("portal_report_submission_date")
        batch.drop_column("payment_date")
