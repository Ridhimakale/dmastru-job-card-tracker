"""Add exact one-day deadlines to visit deliverable tasks."""
from alembic import op
import sqlalchemy as sa


revision = "20261020_52"
down_revision = "20261020_51"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("job_activities") as batch_op:
        batch_op.add_column(sa.Column("sla_due_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_job_activities_sla_due_at", "job_activities", ["sla_due_at"])


def downgrade():
    op.drop_index("ix_job_activities_sla_due_at", table_name="job_activities")
    with op.batch_alter_table("job_activities") as batch_op:
        batch_op.drop_column("sla_due_at")
