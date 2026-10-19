"""Link consultant visit reports to their required planner entry."""
from alembic import op
import sqlalchemy as sa


revision = "20261020_51"
down_revision = "20261020_50"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("visits") as batch_op:
        batch_op.add_column(sa.Column("planned_visit_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_visits_planned_visit_id",
            "planned_visits",
            ["planned_visit_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index("ix_visits_planned_visit_id", "visits", ["planned_visit_id"])


def downgrade():
    op.drop_index("ix_visits_planned_visit_id", table_name="visits")
    with op.batch_alter_table("visits") as batch_op:
        batch_op.drop_constraint("fk_visits_planned_visit_id", type_="foreignkey")
        batch_op.drop_column("planned_visit_id")
