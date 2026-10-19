"""Add Google Workspace account links and portal document references."""
from alembic import op
import sqlalchemy as sa

revision = "20261020_53"
down_revision = "20261020_52"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "google_workspace_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("google_email", sa.String(length=320), nullable=False),
        sa.Column("refresh_token_encrypted", sa.String(length=2048), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_google_workspace_accounts_user_id", "google_workspace_accounts", ["user_id"], unique=True)
    with op.batch_alter_table("record_documents") as batch_op:
        batch_op.add_column(sa.Column("editor_provider", sa.String(length=24), nullable=True))
        batch_op.add_column(sa.Column("google_file_id", sa.String(length=160), nullable=True))
        batch_op.add_column(sa.Column("google_file_url", sa.String(length=1000), nullable=True))
    op.create_index("ix_record_documents_google_file_id", "record_documents", ["google_file_id"])


def downgrade():
    op.drop_index("ix_record_documents_google_file_id", table_name="record_documents")
    with op.batch_alter_table("record_documents") as batch_op:
        batch_op.drop_column("google_file_url")
        batch_op.drop_column("google_file_id")
        batch_op.drop_column("editor_provider")
    op.drop_index("ix_google_workspace_accounts_user_id", table_name="google_workspace_accounts")
    op.drop_table("google_workspace_accounts")
