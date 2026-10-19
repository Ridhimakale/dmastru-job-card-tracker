"""Track consultant activity deliverable submissions and review decisions."""
from alembic import op
import sqlalchemy as sa

revision = "20261016_40"
down_revision = "20261016_39"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("job_activity_attachments")}
    if "status" not in columns:
        op.add_column("job_activity_attachments", sa.Column("status", sa.String(length=24), nullable=False, server_default="DRAFT"))
        op.create_index("ix_job_activity_attachments_status", "job_activity_attachments", ["status"])
    if "review_remarks" not in columns:
        op.add_column("job_activity_attachments", sa.Column("review_remarks", sa.Text(), nullable=True))
    if "reviewed_by" not in columns:
        op.add_column("job_activity_attachments", sa.Column("reviewed_by", sa.Integer(), nullable=True))
        with op.batch_alter_table("job_activity_attachments") as batch:
            batch.create_foreign_key("fk_job_activity_attachments_reviewed_by", "users", ["reviewed_by"], ["id"])
        op.create_index("ix_job_activity_attachments_reviewed_by", "job_activity_attachments", ["reviewed_by"])
    if "reviewed_at" not in columns:
        op.add_column("job_activity_attachments", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))

    op.execute(sa.text("""
        UPDATE job_activity_attachments
        SET status = 'SUBMITTED'
        WHERE status = 'DRAFT'
          AND activity_id IN (SELECT id FROM job_activities WHERE status = 'COMPLETED')
    """))


def downgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("job_activity_attachments")}
    if "reviewed_at" in columns:
        op.drop_column("job_activity_attachments", "reviewed_at")
    if "reviewed_by" in columns:
        op.drop_index("ix_job_activity_attachments_reviewed_by", table_name="job_activity_attachments")
        with op.batch_alter_table("job_activity_attachments") as batch:
            batch.drop_constraint("fk_job_activity_attachments_reviewed_by", type_="foreignkey")
            batch.drop_column("reviewed_by")
    if "review_remarks" in columns:
        op.drop_column("job_activity_attachments", "review_remarks")
    if "status" in columns:
        op.drop_index("ix_job_activity_attachments_status", table_name="job_activity_attachments")
        op.drop_column("job_activity_attachments", "status")
