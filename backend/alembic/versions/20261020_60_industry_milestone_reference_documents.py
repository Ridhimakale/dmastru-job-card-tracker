"""Scope consultant reference documents by industry and milestone."""
from alembic import op
import sqlalchemy as sa

revision = "20261020_60"
down_revision = "20261020_59"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("zed_reference_documents", sa.Column("industry", sa.String(length=120), nullable=True))
    op.add_column("zed_reference_documents", sa.Column("milestone_code", sa.String(length=24), nullable=False, server_default="M1"))
    op.create_index("ix_zed_reference_documents_industry", "zed_reference_documents", ["industry"])
    op.create_index("ix_zed_reference_documents_milestone_code", "zed_reference_documents", ["milestone_code"])


def downgrade():
    op.drop_index("ix_zed_reference_documents_milestone_code", table_name="zed_reference_documents")
    op.drop_index("ix_zed_reference_documents_industry", table_name="zed_reference_documents")
    op.drop_column("zed_reference_documents", "milestone_code")
    op.drop_column("zed_reference_documents", "industry")
