"""Link enquiry discussion evidence to Google Docs and Sheets."""
from alembic import op
import sqlalchemy as sa

revision = "20261020_59"
down_revision = "20261020_58"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("enquiry_documents", sa.Column("google_file_id", sa.String(length=255), nullable=True))
    op.add_column("enquiry_documents", sa.Column("google_file_url", sa.String(length=1000), nullable=True))
    op.add_column("enquiry_documents", sa.Column("google_synced_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_enquiry_documents_google_file_id", "enquiry_documents", ["google_file_id"])


def downgrade():
    op.drop_index("ix_enquiry_documents_google_file_id", table_name="enquiry_documents")
    op.drop_column("enquiry_documents", "google_synced_at")
    op.drop_column("enquiry_documents", "google_file_url")
    op.drop_column("enquiry_documents", "google_file_id")
