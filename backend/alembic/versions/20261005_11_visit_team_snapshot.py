"""Store Job Card consultant assignments with each visit."""
from alembic import op
import sqlalchemy as sa

revision = "20261005_11"
down_revision = "20261005_10"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("visits") as batch:
        batch.add_column(sa.Column("consultant_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("co_consultant_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("subject_expert_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_visits_consultant_id_users", "users", ["consultant_id"], ["id"])
        batch.create_foreign_key("fk_visits_co_consultant_id_users", "users", ["co_consultant_id"], ["id"])
        batch.create_foreign_key("fk_visits_subject_expert_id_users", "users", ["subject_expert_id"], ["id"])


def downgrade():
    with op.batch_alter_table("visits") as batch:
        batch.drop_constraint("fk_visits_subject_expert_id_users", type_="foreignkey")
        batch.drop_constraint("fk_visits_co_consultant_id_users", type_="foreignkey")
        batch.drop_constraint("fk_visits_consultant_id_users", type_="foreignkey")
        batch.drop_column("subject_expert_id")
        batch.drop_column("co_consultant_id")
        batch.drop_column("consultant_id")
