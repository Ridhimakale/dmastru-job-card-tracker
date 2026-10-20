"""Configure milestone visit activity checklists by service."""
from alembic import op
import sqlalchemy as sa

revision = "20261020_62"
down_revision = "20261020_61"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("job_activities", sa.Column("document_format", sa.String(length=24), nullable=False, server_default="EITHER"))
    op.create_table(
        "visit_activity_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("service_name", sa.String(length=120), nullable=False),
        sa.Column("milestone_code", sa.String(length=24), nullable=False),
        sa.Column("visit_number", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("activity_name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("document_format", sa.String(length=24), nullable=False, server_default="EITHER"),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("service_name", "milestone_code", "visit_number", "position", name="uq_visit_activity_template_position"),
    )
    op.create_index("ix_visit_activity_templates_service_name", "visit_activity_templates", ["service_name"])
    op.create_index("ix_visit_activity_templates_milestone_code", "visit_activity_templates", ["milestone_code"])
    op.create_index("ix_visit_activity_templates_visit_number", "visit_activity_templates", ["visit_number"])
    op.create_index("ix_visit_activity_templates_updated_by", "visit_activity_templates", ["updated_by"])
    op.create_index("ix_visit_activity_template_lookup", "visit_activity_templates", ["service_name", "milestone_code", "visit_number"])


def downgrade():
    op.drop_index("ix_visit_activity_template_lookup", table_name="visit_activity_templates")
    op.drop_index("ix_visit_activity_templates_updated_by", table_name="visit_activity_templates")
    op.drop_index("ix_visit_activity_templates_visit_number", table_name="visit_activity_templates")
    op.drop_index("ix_visit_activity_templates_milestone_code", table_name="visit_activity_templates")
    op.drop_index("ix_visit_activity_templates_service_name", table_name="visit_activity_templates")
    op.drop_table("visit_activity_templates")
    op.drop_column("job_activities", "document_format")
