"""Store the manday duration used by each Job Card."""
from alembic import op
import sqlalchemy as sa

revision = "20261005_09"
down_revision = "20260826_08"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("job_cards") as batch:
        batch.add_column(sa.Column("hours_per_manday", sa.Numeric(6, 2), nullable=False, server_default="8"))
        batch.alter_column("required_hours", existing_type=sa.Numeric(10, 2), type_=sa.Numeric(10, 4), existing_nullable=False)


def downgrade():
    with op.batch_alter_table("job_cards") as batch:
        batch.alter_column("required_hours", existing_type=sa.Numeric(10, 4), type_=sa.Numeric(10, 2), existing_nullable=False)
        batch.drop_column("hours_per_manday")
