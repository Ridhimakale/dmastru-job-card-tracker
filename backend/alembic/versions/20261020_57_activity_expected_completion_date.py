"""Track consultant expected completion dates for visit checklist activities."""
from alembic import op
import sqlalchemy as sa

revision = "20261020_57"
down_revision = "20261020_56"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("job_activities", sa.Column("expected_completion_date", sa.Date(), nullable=True))
    op.create_index("ix_job_activities_expected_completion_date", "job_activities", ["expected_completion_date"])


def downgrade():
    op.drop_index("ix_job_activities_expected_completion_date", table_name="job_activities")
    op.drop_column("job_activities", "expected_completion_date")
