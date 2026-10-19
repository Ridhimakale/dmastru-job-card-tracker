"""Simplify enquiry lifecycle states and preserve existing records."""
from alembic import op
import sqlalchemy as sa

revision = "20261017_42"
down_revision = "20261016_41"
branch_labels = None
depends_on = None


def upgrade():
    # Scheduled follow-ups remain in enquiry_followups; the enquiry itself is CONTACTED.
    op.execute(sa.text("UPDATE enquiries SET status='CONTACTED' WHERE status='FOLLOW_UP'"))
    # FINALISED represented a qualified lead ready for the same conversion handoff.
    op.execute(sa.text("UPDATE enquiries SET status='QUALIFIED' WHERE status='FINALISED'"))
    with op.batch_alter_table("enquiries") as batch:
        batch.drop_constraint("ck_enquiries_status", type_="check")
        batch.create_check_constraint("ck_enquiries_status", "status IN ('NEW','CONTACTED','QUALIFIED','LOST','CANCELLED','CONVERTED')")


def downgrade():
    with op.batch_alter_table("enquiries") as batch:
        batch.drop_constraint("ck_enquiries_status", type_="check")
        batch.create_check_constraint("ck_enquiries_status", "status IN ('NEW','CONTACTED','FOLLOW_UP','QUALIFIED','FINALISED','LOST','CANCELLED','CONVERTED')")
