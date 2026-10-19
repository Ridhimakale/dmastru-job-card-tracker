"""Require Management approval before converted enquiries enter onboarding."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261017_43"
down_revision = "20261017_42"
branch_labels = None
depends_on = None


def upgrade():
    # The local development database may already contain columns from a prior
    # bootstrap attempt while its Alembic revision still points at 42. Avoid
    # adding those columns a second time during application startup.
    existing = {column["name"] for column in inspect(op.get_bind()).get_columns("enquiries")}
    with op.batch_alter_table("enquiries") as batch:
        if "management_approval_status" not in existing:
            batch.add_column(sa.Column("management_approval_status", sa.String(length=24), nullable=False, server_default="NOT_REQUIRED"))
        if "management_approval_comment" not in existing:
            batch.add_column(sa.Column("management_approval_comment", sa.Text(), nullable=True))
        if "management_approved_by" not in existing:
            batch.add_column(sa.Column("management_approved_by", sa.Integer(), nullable=True))
        if "management_approved_at" not in existing:
            batch.add_column(sa.Column("management_approved_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_foreign_key("fk_enquiries_management_approved_by_users", "users", ["management_approved_by"], ["id"])
        batch.create_index("ix_enquiries_management_approval_status", ["management_approval_status"])
        batch.create_check_constraint("ck_enquiries_management_approval_status", "management_approval_status IN ('NOT_REQUIRED','PENDING','APPROVED','REJECTED')")
    # Keep already-converted enquiries available to Admin after the new gate is introduced.
    op.execute("UPDATE enquiries SET management_approval_status='APPROVED' WHERE status='CONVERTED'")


def downgrade():
    with op.batch_alter_table("enquiries") as batch:
        batch.drop_constraint("ck_enquiries_management_approval_status", type_="check")
        batch.drop_index("ix_enquiries_management_approval_status")
        batch.drop_constraint("fk_enquiries_management_approved_by_users", type_="foreignkey")
        batch.drop_column("management_approved_at")
        batch.drop_column("management_approved_by")
        batch.drop_column("management_approval_comment")
        batch.drop_column("management_approval_status")
