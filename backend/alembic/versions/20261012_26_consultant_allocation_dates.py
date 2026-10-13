"""Store planned consultant allocation dates."""
from alembic import op
import sqlalchemy as sa
revision="20261012_26"
down_revision="20261012_25"
branch_labels=None
depends_on=None

def upgrade():
    op.add_column("job_card_assignments",sa.Column("allocation_start_date",sa.Date(),nullable=True))
    op.add_column("job_card_assignments",sa.Column("allocation_end_date",sa.Date(),nullable=True))
    op.create_index("ix_job_card_assignments_allocation_start_date","job_card_assignments",["allocation_start_date"])
    op.create_index("ix_job_card_assignments_allocation_end_date","job_card_assignments",["allocation_end_date"])

def downgrade():
    op.drop_index("ix_job_card_assignments_allocation_end_date",table_name="job_card_assignments")
    op.drop_index("ix_job_card_assignments_allocation_start_date",table_name="job_card_assignments")
    op.drop_column("job_card_assignments","allocation_end_date")
    op.drop_column("job_card_assignments","allocation_start_date")
