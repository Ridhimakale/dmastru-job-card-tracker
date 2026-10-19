"""Move converted enquiries directly to Job Card creation and milestone payments."""
from alembic import op
import sqlalchemy as sa

revision = "20261016_37"
down_revision = "20261016_36"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("enquiry_onboardings") as batch:
        batch.drop_constraint("ck_enquiry_onboardings_status", type_="check")
        batch.drop_constraint("ck_enquiry_onboardings_initial_payment_positive", type_="check")
        batch.create_check_constraint(
            "ck_enquiry_onboardings_status",
            "status IN ('PAYMENT_PENDING','PAYMENT_CONFIRMED','ONBOARDED','JOB_CARD_CREATED')",
        )
    # Existing onboarding and payment history stays available for audit.


def downgrade():
    with op.batch_alter_table("enquiry_onboardings") as batch:
        batch.drop_constraint("ck_enquiry_onboardings_status", type_="check")
        batch.create_check_constraint(
            "ck_enquiry_onboardings_status",
            "status IN ('PAYMENT_PENDING','PAYMENT_CONFIRMED','JOB_CARD_CREATED')",
        )
        batch.create_check_constraint(
            "ck_enquiry_onboardings_initial_payment_positive",
            "initial_payment_amount > 0",
        )
