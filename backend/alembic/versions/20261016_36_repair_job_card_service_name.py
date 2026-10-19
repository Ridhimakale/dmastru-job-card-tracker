"""Repair databases stamped past the Job Card service name change."""
from alembic import op
import sqlalchemy as sa

revision = "20261016_36"
down_revision = "20261016_35"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("job_cards")}
    if "service_name" not in columns:
        op.add_column("job_cards", sa.Column("service_name", sa.String(length=250), nullable=True))


def downgrade():
    # Keep any service names that may have been saved after this repair.
    pass
