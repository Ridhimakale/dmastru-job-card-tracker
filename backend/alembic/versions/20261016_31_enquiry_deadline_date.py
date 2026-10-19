"""Store the response deadline selected by Marketing on each enquiry."""
from alembic import op
import sqlalchemy as sa

revision = "20261016_31"
down_revision = "20261016_30"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("enquiries", sa.Column("deadline_date", sa.Date(), nullable=True))
    op.create_index("ix_enquiries_deadline_date", "enquiries", ["deadline_date"])

def downgrade():
    op.drop_index("ix_enquiries_deadline_date", table_name="enquiries")
    op.drop_column("enquiries", "deadline_date")
