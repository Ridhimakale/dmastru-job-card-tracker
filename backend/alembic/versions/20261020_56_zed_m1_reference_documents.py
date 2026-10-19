"""Add Admin-managed ZED Silver M1 visit reference documents."""
from alembic import op
import sqlalchemy as sa

revision = "20261020_56"
down_revision = "20261020_55"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "zed_reference_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("visit_number", sa.Integer(), nullable=False),
        sa.Column("activity_name", sa.String(length=240), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False, unique=True),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("mime_type", sa.String(length=120), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("uploaded_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_zed_reference_documents_visit_number", "zed_reference_documents", ["visit_number"])
    op.create_index("ix_zed_reference_documents_activity_name", "zed_reference_documents", ["activity_name"])
    op.create_index("ix_zed_reference_documents_active", "zed_reference_documents", ["active"])
    op.create_index("ix_zed_reference_documents_uploaded_by", "zed_reference_documents", ["uploaded_by"])
    op.create_index("ix_zed_reference_documents_uploaded_at", "zed_reference_documents", ["uploaded_at"])


def downgrade():
    op.drop_table("zed_reference_documents")
