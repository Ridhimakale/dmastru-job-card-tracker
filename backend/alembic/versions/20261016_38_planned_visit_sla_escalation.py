"""Add visit follow-up files and escalation timestamps."""
from alembic import op
import sqlalchemy as sa

revision = "20261016_38"
down_revision = "20261016_37"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("planned_visits")}
    additions = {
        "attachment_name": sa.Column("attachment_name", sa.String(length=255), nullable=True),
        "attachment_mime_type": sa.Column("attachment_mime_type", sa.String(length=120), nullable=True),
        "attachment_key": sa.Column("attachment_key", sa.String(length=64), nullable=True),
        "admin_escalated_at": sa.Column("admin_escalated_at", sa.DateTime(timezone=True), nullable=True),
        "management_escalated_at": sa.Column("management_escalated_at", sa.DateTime(timezone=True), nullable=True),
        "admin_email_attempted_at": sa.Column("admin_email_attempted_at", sa.DateTime(timezone=True), nullable=True),
        "admin_email_sent_at": sa.Column("admin_email_sent_at", sa.DateTime(timezone=True), nullable=True),
        "management_email_attempted_at": sa.Column("management_email_attempted_at", sa.DateTime(timezone=True), nullable=True),
        "management_email_sent_at": sa.Column("management_email_sent_at", sa.DateTime(timezone=True), nullable=True),
    }
    for name, column in additions.items():
        if name not in columns:
            op.add_column("planned_visits", column)


def downgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("planned_visits")}
    for name in ("management_email_sent_at", "management_email_attempted_at", "admin_email_sent_at", "admin_email_attempted_at", "management_escalated_at", "admin_escalated_at", "attachment_key", "attachment_mime_type", "attachment_name"):
        if name in columns:
            op.drop_column("planned_visits", name)
