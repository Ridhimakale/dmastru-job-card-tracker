"""Index MSME attributes used by administrative search."""
from alembic import op

revision = "20261005_10"
down_revision = "20261005_09"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_msmes_zed_id", "msmes", ["zed_id"])
    op.create_index("ix_msmes_city", "msmes", ["city"])
    op.create_index("ix_msmes_state", "msmes", ["state"])


def downgrade():
    op.drop_index("ix_msmes_state", table_name="msmes")
    op.drop_index("ix_msmes_city", table_name="msmes")
    op.drop_index("ix_msmes_zed_id", table_name="msmes")
