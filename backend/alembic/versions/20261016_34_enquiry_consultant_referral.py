"""Link consultant-referred enquiries to a registered consultant."""
from alembic import op
import sqlalchemy as sa

revision = "20261016_34"
down_revision = "20261016_33"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("enquiries") as batch:
        batch.add_column(sa.Column("referral_consultant_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_enquiries_referral_consultant_id_users", "users", ["referral_consultant_id"], ["id"])
    op.create_index("ix_enquiries_referral_consultant_id", "enquiries", ["referral_consultant_id"])


def downgrade():
    op.drop_index("ix_enquiries_referral_consultant_id", table_name="enquiries")
    with op.batch_alter_table("enquiries") as batch:
        batch.drop_constraint("fk_enquiries_referral_consultant_id_users", type_="foreignkey")
        batch.drop_column("referral_consultant_id")
