"""Assign a stable sequence to planned milestone visits."""
from alembic import op
import sqlalchemy as sa

revision = "20261020_58"
down_revision = "20261020_57"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("planned_visits", sa.Column("visit_number", sa.Integer(), nullable=True))
    op.create_index("ix_planned_visits_visit_number", "planned_visits", ["visit_number"])


def downgrade():
    op.drop_index("ix_planned_visits_visit_number", table_name="planned_visits")
    op.drop_column("planned_visits", "visit_number")
