"""Configure workflow event triggers."""
from alembic import op
import sqlalchemy as sa

revision="20261010_18"
down_revision="20261010_17"
branch_labels=None
depends_on=None

def upgrade():
    with op.batch_alter_table("workflow_templates") as b:
        b.add_column(sa.Column("trigger_event",sa.String(32),nullable=False,server_default="MANUAL"))
    op.create_index("ix_workflow_templates_trigger_event","workflow_templates",["trigger_event"])

def downgrade():
    op.drop_index("ix_workflow_templates_trigger_event",table_name="workflow_templates")
    with op.batch_alter_table("workflow_templates") as b:b.drop_column("trigger_event")
