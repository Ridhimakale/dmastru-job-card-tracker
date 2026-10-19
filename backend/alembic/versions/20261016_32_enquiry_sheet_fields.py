"""Add the Marketing enquiry register fields."""
from alembic import op
import sqlalchemy as sa

revision = "20261016_32"
down_revision = "20261016_31"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("enquiries", sa.Column("enquiry_for", sa.JSON(), nullable=True))
    op.add_column("enquiries", sa.Column("zed_id", sa.String(length=120), nullable=True))
    op.add_column("enquiries", sa.Column("zed_pass_encrypted", sa.String(length=1000), nullable=True))
    op.add_column("enquiries", sa.Column("feedback", sa.Text(), nullable=True))
    op.create_index("ix_enquiries_zed_id", "enquiries", ["zed_id"])


def downgrade():
    op.drop_index("ix_enquiries_zed_id", table_name="enquiries")
    op.drop_column("enquiries", "feedback")
    op.drop_column("enquiries", "zed_pass_encrypted")
    op.drop_column("enquiries", "zed_id")
    op.drop_column("enquiries", "enquiry_for")
