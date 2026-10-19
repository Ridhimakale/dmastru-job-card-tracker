"""Add Admin review state for consultant activities."""
from alembic import op
import sqlalchemy as sa

revision = "20261016_41"
down_revision = "20261016_40"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("job_activities")}
    if "review_status" not in columns:
        op.add_column("job_activities", sa.Column("review_status", sa.String(length=24), nullable=False, server_default="NOT_SUBMITTED"))
        op.create_index("ix_job_activities_review_status", "job_activities", ["review_status"])
    if "review_remarks" not in columns:
        op.add_column("job_activities", sa.Column("review_remarks", sa.Text(), nullable=True))
    if "reviewed_by" not in columns:
        op.add_column("job_activities", sa.Column("reviewed_by", sa.Integer(), nullable=True))
        with op.batch_alter_table("job_activities") as batch:
            batch.create_foreign_key("fk_job_activities_reviewed_by", "users", ["reviewed_by"], ["id"])
        op.create_index("ix_job_activities_reviewed_by", "job_activities", ["reviewed_by"])
    if "reviewed_at" not in columns:
        op.add_column("job_activities", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(sa.text("UPDATE job_activities SET review_status='SUBMITTED' WHERE status='COMPLETED'"))


def downgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("job_activities")}
    if "reviewed_at" in columns:
        op.drop_column("job_activities", "reviewed_at")
    if "reviewed_by" in columns:
        op.drop_index("ix_job_activities_reviewed_by", table_name="job_activities")
        with op.batch_alter_table("job_activities") as batch:
            batch.drop_constraint("fk_job_activities_reviewed_by", type_="foreignkey")
            batch.drop_column("reviewed_by")
    if "review_remarks" in columns:
        op.drop_column("job_activities", "review_remarks")
    if "review_status" in columns:
        op.drop_index("ix_job_activities_review_status", table_name="job_activities")
        op.drop_column("job_activities", "review_status")
