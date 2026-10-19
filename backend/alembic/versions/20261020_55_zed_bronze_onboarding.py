"""Track free ZED Bronze certification and Silver/Gold marketing referrals."""
from alembic import op
import sqlalchemy as sa

revision = "20261020_55"
down_revision = "20261020_54"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("enquiry_onboardings") as batch:
        batch.add_column(sa.Column("zed_bronze_certification_status", sa.String(length=24), nullable=False, server_default="NOT_REQUIRED"))
        batch.add_column(sa.Column("zed_bronze_certificate_number", sa.String(length=120), nullable=True))
        batch.add_column(sa.Column("zed_bronze_report_document_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("zed_bronze_confirmed_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("zed_bronze_confirmed_by", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("zed_silver_gold_interest", sa.String(length=8), nullable=True))
        batch.add_column(sa.Column("zed_marketing_plan_status", sa.String(length=16), nullable=False, server_default="NOT_REQUIRED"))
        batch.add_column(sa.Column("zed_marketing_plan_services", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("zed_marketing_plan_notes", sa.Text(), nullable=True))
        batch.add_column(sa.Column("zed_marketing_plan_due_date", sa.Date(), nullable=True))
        batch.add_column(sa.Column("zed_marketing_planned_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("zed_marketing_planned_by", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_enquiry_onboardings_zed_bronze_report_enquiry_documents", "enquiry_documents", ["zed_bronze_report_document_id"], ["id"])
        batch.create_foreign_key("fk_enquiry_onboardings_zed_bronze_confirmed_by_users", "users", ["zed_bronze_confirmed_by"], ["id"])
        batch.create_foreign_key("fk_enquiry_onboardings_zed_marketing_planned_by_users", "users", ["zed_marketing_planned_by"], ["id"])
        batch.create_index("ix_enquiry_onboardings_zed_bronze_certification_status", ["zed_bronze_certification_status"])
        batch.create_index("ix_enquiry_onboardings_zed_marketing_plan_status", ["zed_marketing_plan_status"])
    op.execute(sa.text("UPDATE enquiry_onboardings SET zed_bronze_certification_status='PENDING' WHERE lower(service_name) LIKE '%zed bronze%' AND job_card_id IS NULL"))


def downgrade():
    with op.batch_alter_table("enquiry_onboardings") as batch:
        batch.drop_index("ix_enquiry_onboardings_zed_marketing_plan_status")
        batch.drop_index("ix_enquiry_onboardings_zed_bronze_certification_status")
        batch.drop_constraint("fk_enquiry_onboardings_zed_marketing_planned_by_users", type_="foreignkey")
        batch.drop_constraint("fk_enquiry_onboardings_zed_bronze_confirmed_by_users", type_="foreignkey")
        batch.drop_constraint("fk_enquiry_onboardings_zed_bronze_report_enquiry_documents", type_="foreignkey")
        batch.drop_column("zed_marketing_planned_by")
        batch.drop_column("zed_marketing_planned_at")
        batch.drop_column("zed_marketing_plan_due_date")
        batch.drop_column("zed_marketing_plan_notes")
        batch.drop_column("zed_marketing_plan_services")
        batch.drop_column("zed_marketing_plan_status")
        batch.drop_column("zed_silver_gold_interest")
        batch.drop_column("zed_bronze_confirmed_by")
        batch.drop_column("zed_bronze_confirmed_at")
        batch.drop_column("zed_bronze_report_document_id")
        batch.drop_column("zed_bronze_certificate_number")
        batch.drop_column("zed_bronze_certification_status")
