"""Add validated files to workflow actions."""
from alembic import op
import sqlalchemy as sa

revision="20261010_17"
down_revision="20261006_16"
branch_labels=None
depends_on=None

def upgrade():
    op.create_table("workflow_action_attachments",sa.Column("id",sa.Integer(),primary_key=True),sa.Column("action_id",sa.Uuid(),sa.ForeignKey("workflow_actions.id",ondelete="CASCADE"),nullable=False),sa.Column("file_name",sa.String(255),nullable=False),sa.Column("storage_key",sa.String(255),nullable=False),sa.Column("file_size",sa.Integer(),nullable=False),sa.Column("mime_type",sa.String(120),nullable=False),sa.Column("uploaded_by",sa.Integer(),sa.ForeignKey("users.id"),nullable=False),sa.Column("uploaded_at",sa.DateTime(timezone=True),nullable=False),sa.UniqueConstraint("storage_key"))
    for column in ("action_id","uploaded_by","uploaded_at"):op.create_index(f"ix_workflow_action_attachments_{column}","workflow_action_attachments",[column])

def downgrade():
    for column in ("action_id","uploaded_by","uploaded_at"):op.drop_index(f"ix_workflow_action_attachments_{column}",table_name="workflow_action_attachments")
    op.drop_table("workflow_action_attachments")
