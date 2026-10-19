"""Remove the session idle-timeout activity field."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261018_47"
down_revision = "20261018_46"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("auth_sessions")}
    if "last_activity_at" in columns:
        with op.batch_alter_table("auth_sessions") as batch:
            batch.drop_column("last_activity_at")


def downgrade():
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("auth_sessions")}
    if "last_activity_at" not in columns:
        with op.batch_alter_table("auth_sessions") as batch:
            batch.add_column(sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True))
