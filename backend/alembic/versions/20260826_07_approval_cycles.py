"""Preserve approval history across rework cycles."""
from alembic import op
import sqlalchemy as sa
revision="20260826_07"; down_revision="20260826_05"; branch_labels=None; depends_on=None
def upgrade():
    with op.batch_alter_table("approvals") as b: b.add_column(sa.Column("cycle",sa.Integer(),nullable=False,server_default="1")); b.create_index("ix_approvals_cycle",["cycle"])
def downgrade():
    with op.batch_alter_table("approvals") as b: b.drop_index("ix_approvals_cycle"); b.drop_column("cycle")
