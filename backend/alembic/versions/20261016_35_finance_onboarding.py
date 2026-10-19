"""Add finance role and enquiry-first onboarding payments."""
from alembic import op
import sqlalchemy as sa

revision = "20261016_35"
down_revision = "20261016_34"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE role ADD VALUE IF NOT EXISTS 'FINANCE'")

    with op.batch_alter_table("job_cards") as batch:
        batch.add_column(sa.Column("service_name", sa.String(250), nullable=True))

    with op.batch_alter_table("payments") as batch:
        batch.alter_column("job_card_id", existing_type=sa.Integer(), nullable=True)
        batch.add_column(sa.Column("enquiry_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key("fk_payments_enquiry_id_enquiries", "enquiries", ["enquiry_id"], ["id"])
        batch.create_index("ix_payments_enquiry_id", ["enquiry_id"])

    op.create_index(
        "uq_payments_enquiry_initial_invoice",
        "payments",
        ["enquiry_id"],
        unique=True,
        sqlite_where=sa.text("enquiry_id IS NOT NULL AND job_card_id IS NULL AND milestone_id IS NULL AND status != 'CANCELLED'"),
        postgresql_where=sa.text("enquiry_id IS NOT NULL AND job_card_id IS NULL AND milestone_id IS NULL AND status != 'CANCELLED'"),
    )
    op.create_table(
        "enquiry_onboardings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enquiry_id", sa.Uuid(), nullable=False),
        sa.Column("msme_id", sa.Integer(), nullable=False),
        sa.Column("project_name", sa.String(200), nullable=False),
        sa.Column("service_name", sa.String(250), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("location", sa.String(160), nullable=False),
        sa.Column("package_code", sa.String(64), nullable=True),
        sa.Column("commercial_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("initial_payment_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="PAYMENT_PENDING"),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payment_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("job_card_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["enquiry_id"], ["enquiries.id"], ondelete="CASCADE", name="fk_enquiry_onboardings_enquiry_id_enquiries"),
        sa.ForeignKeyConstraint(["msme_id"], ["msmes.id"], name="fk_enquiry_onboardings_msme_id_msmes"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name="fk_enquiry_onboardings_created_by_users"),
        sa.ForeignKeyConstraint(["job_card_id"], ["job_cards.id"], name="fk_enquiry_onboardings_job_card_id_job_cards"),
        sa.UniqueConstraint("enquiry_id", name="uq_enquiry_onboardings_enquiry_id"),
        sa.CheckConstraint("status IN ('PAYMENT_PENDING','PAYMENT_CONFIRMED','JOB_CARD_CREATED')", name="ck_enquiry_onboardings_status"),
        sa.CheckConstraint("commercial_amount IS NULL OR commercial_amount >= 0", name="ck_enquiry_onboardings_commercial_amount"),
        sa.CheckConstraint("initial_payment_amount > 0", name="ck_enquiry_onboardings_initial_payment_positive"),
    )
    for column in ("enquiry_id", "msme_id", "package_code", "status", "created_by", "job_card_id"):
        op.create_index(f"ix_enquiry_onboardings_{column}", "enquiry_onboardings", [column])


def downgrade():
    op.drop_table("enquiry_onboardings")
    with op.batch_alter_table("job_cards") as batch:
        batch.drop_column("service_name")
    op.drop_index("uq_payments_enquiry_initial_invoice", table_name="payments")
    with op.batch_alter_table("payments") as batch:
        batch.drop_index("ix_payments_enquiry_id")
        batch.drop_constraint("fk_payments_enquiry_id_enquiries", type_="foreignkey")
        batch.drop_column("enquiry_id")
        batch.alter_column("job_card_id", existing_type=sa.Integer(), nullable=False)
