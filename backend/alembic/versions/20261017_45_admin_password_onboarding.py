"""Require Admin-created users to replace their initial password."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261017_45"
down_revision = "20261017_44"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("users")}
    if "password_change_required" not in columns:
        with op.batch_alter_table("users") as batch:
            batch.add_column(sa.Column("password_change_required", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("users")}
    if "password_change_required" in columns:
        with op.batch_alter_table("users") as batch:
            batch.drop_column("password_change_required")
