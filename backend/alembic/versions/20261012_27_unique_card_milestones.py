"""Enforce one of each milestone code per Job Card."""
from alembic import op
revision="20261012_27"
down_revision="20261012_26"
branch_labels=None
depends_on=None

def upgrade():
    with op.batch_alter_table("milestones") as batch:
        batch.create_unique_constraint("uq_milestone_job_card_code",["job_card_id","code"])

def downgrade():
    with op.batch_alter_table("milestones") as batch:
        batch.drop_constraint("uq_milestone_job_card_code",type_="unique")
