"""Capture indicative commercial value on enquiries."""
from alembic import op
import sqlalchemy as sa

revision="20261012_24"
down_revision="20261010_23"
branch_labels=None
depends_on=None

def upgrade():
    op.add_column("enquiries",sa.Column("expected_commercial_amount",sa.Numeric(12,2),nullable=True))

def downgrade():
    op.drop_column("enquiries","expected_commercial_amount")
