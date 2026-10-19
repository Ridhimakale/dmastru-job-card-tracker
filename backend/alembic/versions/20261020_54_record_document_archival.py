"""Add archival fields for versioned record documents."""
from alembic import op
import sqlalchemy as sa

revision = "20261020_54"
down_revision = "20261020_53"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("record_documents") as batch_op:
        batch_op.add_column(sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("deleted_by", sa.Integer(), nullable=True))
        batch_op.create_foreign_key("fk_record_documents_deleted_by_users", "users", ["deleted_by"], ["id"])


def downgrade():
    with op.batch_alter_table("record_documents") as batch_op:
        batch_op.drop_constraint("fk_record_documents_deleted_by_users", type_="foreignkey")
        batch_op.drop_column("deleted_by")
        batch_op.drop_column("deleted_at")
