"""Add optional validated visit attachment metadata."""
from alembic import op
import sqlalchemy as sa
revision="20260826_08"; down_revision="20260826_07"; branch_labels=None; depends_on=None
def upgrade():
    with op.batch_alter_table("visits") as b:
        b.add_column(sa.Column("attachment_name",sa.String(255),nullable=True)); b.add_column(sa.Column("attachment_key",sa.String(255),nullable=True)); b.add_column(sa.Column("attachment_mime_type",sa.String(120),nullable=True))
def downgrade():
    with op.batch_alter_table("visits") as b:
        b.drop_column("attachment_mime_type"); b.drop_column("attachment_key"); b.drop_column("attachment_name")
