"""Repair the follow-up method column when revision 30 was stamped without it."""
from alembic import op
import sqlalchemy as sa

revision = "20261016_33"
down_revision = "20261016_32"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("enquiry_followups")}
    if "next_follow_up_type" not in columns:
        op.add_column("enquiry_followups", sa.Column("next_follow_up_type", sa.String(length=32), nullable=True))

    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("enquiry_followups")}
    if "ix_enquiry_followups_next_follow_up_type" not in indexes:
        op.create_index("ix_enquiry_followups_next_follow_up_type", "enquiry_followups", ["next_follow_up_type"])


def downgrade():
    inspector = sa.inspect(op.get_bind())
    indexes = {index["name"] for index in inspector.get_indexes("enquiry_followups")}
    if "ix_enquiry_followups_next_follow_up_type" in indexes:
        op.drop_index("ix_enquiry_followups_next_follow_up_type", table_name="enquiry_followups")
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("enquiry_followups")}
    if "next_follow_up_type" in columns:
        op.drop_column("enquiry_followups", "next_follow_up_type")
