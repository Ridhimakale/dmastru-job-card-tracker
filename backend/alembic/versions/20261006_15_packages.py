"""Add configurable service package templates and project snapshots."""
from alembic import op
import sqlalchemy as sa

revision="20261006_15"
down_revision="20261006_14"
branch_labels=None
depends_on=None

def upgrade():
    op.create_table("service_packages",
        sa.Column("id",sa.Integer(),primary_key=True),sa.Column("code",sa.String(64),nullable=False),
        sa.Column("name",sa.String(120),nullable=False),sa.Column("scope",sa.Text(),nullable=False),
        sa.Column("planned_mandays",sa.Numeric(10,2),nullable=False),sa.Column("commercial_amount",sa.Numeric(12,2),nullable=False),sa.Column("project_payment_percent",sa.Numeric(5,2),nullable=False),
        sa.Column("milestones",sa.JSON(),nullable=False),sa.Column("activities",sa.JSON(),nullable=False),
        sa.Column("active",sa.Boolean(),nullable=False),sa.Column("updated_by",sa.Integer(),sa.ForeignKey("users.id"),nullable=False),
        sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False),sa.UniqueConstraint("code"))
    op.create_index("ix_service_packages_code","service_packages",["code"]);op.create_index("ix_service_packages_active","service_packages",["active"])
    with op.batch_alter_table("job_cards") as b:
        b.add_column(sa.Column("commercial_amount",sa.Numeric(12,2),nullable=True));b.add_column(sa.Column("project_payment_percent",sa.Numeric(5,2),nullable=True))
    with op.batch_alter_table("milestones") as b:
        b.add_column(sa.Column("planned_mandays",sa.Numeric(10,2),nullable=True));b.add_column(sa.Column("amount",sa.Numeric(12,2),nullable=True))
        b.add_column(sa.Column("payment_percent",sa.Numeric(5,2),nullable=True));b.add_column(sa.Column("activities",sa.JSON(),nullable=False,server_default=sa.text("'[]'")))

def downgrade():
    with op.batch_alter_table("milestones") as b:
        b.drop_column("activities");b.drop_column("payment_percent");b.drop_column("amount");b.drop_column("planned_mandays")
    with op.batch_alter_table("job_cards") as b: b.drop_column("project_payment_percent");b.drop_column("commercial_amount")
    op.drop_index("ix_service_packages_active",table_name="service_packages");op.drop_index("ix_service_packages_code",table_name="service_packages");op.drop_table("service_packages")
