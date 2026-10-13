"""Add auditable project closure, certificate, case study and feedback records."""
from alembic import op
import sqlalchemy as sa

revision="20261006_14"
down_revision="20261006_13"
branch_labels=None
depends_on=None

def upgrade():
    op.create_table(
        "project_closures",
        sa.Column("id",sa.Integer(),primary_key=True),
        sa.Column("job_card_id",sa.Integer(),sa.ForeignKey("job_cards.id",ondelete="CASCADE"),nullable=False),
        sa.Column("client_confirmation",sa.Text()),sa.Column("confirmed_at",sa.DateTime(timezone=True)),
        sa.Column("problem",sa.Text()),sa.Column("requirement",sa.Text()),sa.Column("solution",sa.Text()),
        sa.Column("implementation",sa.Text()),sa.Column("benefits",sa.Text()),sa.Column("results",sa.Text()),
        sa.Column("feedback_rating",sa.Integer()),sa.Column("feedback",sa.Text()),sa.Column("testimonial",sa.Text()),
        sa.Column("publication_permission",sa.Boolean(),nullable=False,server_default=sa.false()),
        sa.Column("certificate_number",sa.String(32),unique=True),sa.Column("certificate_key",sa.String(255)),
        sa.Column("closed_at",sa.DateTime(timezone=True)),sa.Column("prepared_by",sa.Integer(),sa.ForeignKey("users.id"),nullable=False),
        sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint("job_card_id",name="uq_project_closures_job_card"),
        sa.CheckConstraint("feedback_rating IS NULL OR feedback_rating BETWEEN 1 AND 5",name="ck_project_closure_rating"),
    )
    op.create_index("ix_project_closures_job_card_id","project_closures",["job_card_id"])

def downgrade():
    op.drop_index("ix_project_closures_job_card_id",table_name="project_closures")
    op.drop_table("project_closures")
