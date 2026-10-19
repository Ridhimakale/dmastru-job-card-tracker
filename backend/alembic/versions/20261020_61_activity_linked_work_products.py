"""Link activity work products to the activity they evidence."""
from alembic import op
import sqlalchemy as sa

revision = "20261020_61"
down_revision = "20261020_60"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("record_documents")}
    if "activity_id" not in columns:
        op.add_column("record_documents", sa.Column("activity_id", sa.Integer(), sa.ForeignKey("job_activities.id", ondelete="SET NULL"), nullable=True))
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("record_documents")}
    if "ix_record_documents_activity_id" not in indexes:
        op.create_index("ix_record_documents_activity_id", "record_documents", ["activity_id"])


def downgrade():
    op.drop_index("ix_record_documents_activity_id", table_name="record_documents")
    op.drop_column("record_documents", "activity_id")
