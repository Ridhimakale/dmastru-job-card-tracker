"""Add the Admin client briefing and confirmation gate before onboarding."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261018_48"
down_revision = "20261018_47"
branch_labels = None
depends_on = None


def upgrade():
    inspector = inspect(op.get_bind())
    enquiry_columns = {column["name"] for column in inspector.get_columns("enquiries")}
    with op.batch_alter_table("enquiries") as batch:
        additions = [
            sa.Column("client_meeting_type", sa.String(length=16), nullable=True),
            sa.Column("client_meeting_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("client_meeting_completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("client_briefing_service_name", sa.String(length=250), nullable=True),
            sa.Column("client_briefing_package_code", sa.String(length=64), nullable=True),
            sa.Column("client_briefing_planned_mandays", sa.Numeric(10, 2), nullable=True),
            sa.Column("client_briefing_timeline_start", sa.Date(), nullable=True),
            sa.Column("client_briefing_timeline_end", sa.Date(), nullable=True),
            sa.Column("client_briefing_scope", sa.Text(), nullable=True),
            sa.Column("client_confirmation_status", sa.String(length=24), nullable=False, server_default="NOT_SCHEDULED"),
            sa.Column("client_confirmation_note", sa.Text(), nullable=True),
            sa.Column("client_confirmation_recorded_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("client_confirmation_recorded_by", sa.Integer(), nullable=True),
        ]
        for column in additions:
            if column.name not in enquiry_columns:
                batch.add_column(column)
        batch.create_foreign_key("fk_enquiries_client_confirmation_recorded_by_users", "users", ["client_confirmation_recorded_by"], ["id"])
        batch.create_index("ix_enquiries_client_confirmation_status", ["client_confirmation_status"])
        batch.create_check_constraint("ck_enquiries_client_confirmation_status", "client_confirmation_status IN ('NOT_SCHEDULED','SCHEDULED','CONFIRMED','CHANGES_REQUESTED','DECLINED')")

    onboarding_columns = {column["name"] for column in inspect(op.get_bind()).get_columns("enquiry_onboardings")}
    with op.batch_alter_table("enquiry_onboardings") as batch:
        if "planned_mandays" not in onboarding_columns:
            batch.add_column(sa.Column("planned_mandays", sa.Numeric(10, 2), nullable=True))
        if "project_timeline_start" not in onboarding_columns:
            batch.add_column(sa.Column("project_timeline_start", sa.Date(), nullable=True))
        if "project_timeline_end" not in onboarding_columns:
            batch.add_column(sa.Column("project_timeline_end", sa.Date(), nullable=True))


def downgrade():
    with op.batch_alter_table("enquiry_onboardings") as batch:
        batch.drop_column("project_timeline_end")
        batch.drop_column("project_timeline_start")
        batch.drop_column("planned_mandays")
    with op.batch_alter_table("enquiries") as batch:
        batch.drop_constraint("ck_enquiries_client_confirmation_status", type_="check")
        batch.drop_index("ix_enquiries_client_confirmation_status")
        batch.drop_constraint("fk_enquiries_client_confirmation_recorded_by_users", type_="foreignkey")
        batch.drop_column("client_confirmation_recorded_by")
        batch.drop_column("client_confirmation_recorded_at")
        batch.drop_column("client_confirmation_note")
        batch.drop_column("client_confirmation_status")
        batch.drop_column("client_briefing_scope")
        batch.drop_column("client_briefing_timeline_end")
        batch.drop_column("client_briefing_timeline_start")
        batch.drop_column("client_briefing_planned_mandays")
        batch.drop_column("client_briefing_package_code")
        batch.drop_column("client_briefing_service_name")
        batch.drop_column("client_meeting_completed_at")
        batch.drop_column("client_meeting_at")
        batch.drop_column("client_meeting_type")
