"""Add configurable workflow templates and auditable action switching."""
from alembic import op
import sqlalchemy as sa

revision="20261006_16"
down_revision="20261006_15"
branch_labels=None
depends_on=None

def upgrade():
    op.create_table("workflow_templates",sa.Column("id",sa.Integer(),primary_key=True),sa.Column("code",sa.String(64),nullable=False),sa.Column("name",sa.String(160),nullable=False),sa.Column("steps",sa.JSON(),nullable=False),sa.Column("active",sa.Boolean(),nullable=False),sa.Column("updated_by",sa.Integer(),sa.ForeignKey("users.id"),nullable=False),sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False),sa.UniqueConstraint("code"))
    op.create_index("ix_workflow_templates_code","workflow_templates",["code"]);op.create_index("ix_workflow_templates_active","workflow_templates",["active"])
    op.create_table("workflow_actions",
        sa.Column("id",sa.Uuid(),primary_key=True),sa.Column("action_number",sa.String(32),nullable=False),sa.Column("template_code",sa.String(64),nullable=False),sa.Column("workflow_step",sa.Integer(),nullable=False),
        sa.Column("ticket_id",sa.Uuid(),sa.ForeignKey("tickets.id")),sa.Column("enquiry_id",sa.Uuid(),sa.ForeignKey("enquiries.id")),sa.Column("job_card_id",sa.Integer(),sa.ForeignKey("job_cards.id")),sa.Column("milestone_id",sa.Integer(),sa.ForeignKey("milestones.id")),
        sa.Column("action_type",sa.String(64),nullable=False),sa.Column("title",sa.String(200),nullable=False),sa.Column("description",sa.Text()),sa.Column("department_id",sa.Integer(),sa.ForeignKey("departments.id"),nullable=False),sa.Column("assigned_role",sa.String(32)),sa.Column("assigned_user_id",sa.Integer(),sa.ForeignKey("users.id")),sa.Column("priority",sa.String(16),nullable=False),sa.Column("due_at",sa.DateTime(timezone=True)),sa.Column("status",sa.String(24),nullable=False),
        sa.Column("parent_action_id",sa.Uuid(),sa.ForeignKey("workflow_actions.id")),sa.Column("previous_action_id",sa.Uuid(),sa.ForeignKey("workflow_actions.id")),sa.Column("next_action_id",sa.Uuid(),sa.ForeignKey("workflow_actions.id")),sa.Column("created_by",sa.Integer(),sa.ForeignKey("users.id"),nullable=False),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),sa.Column("started_at",sa.DateTime(timezone=True)),sa.Column("completed_at",sa.DateTime(timezone=True)),sa.Column("remarks",sa.Text()),
        sa.UniqueConstraint("action_number"),sa.CheckConstraint("status IN ('OPEN','IN_PROGRESS','WAITING','COMPLETED','RETURNED','REJECTED','ON_HOLD','CANCELLED')",name="ck_workflow_actions_status"))
    for column in ("action_number","template_code","ticket_id","enquiry_id","job_card_id","milestone_id","department_id","assigned_role","assigned_user_id","due_at","status","parent_action_id","previous_action_id","next_action_id","created_by","created_at"):
        op.create_index(f"ix_workflow_actions_{column}","workflow_actions",[column])
    op.create_index("ix_workflow_actions_queue","workflow_actions",["status","department_id","assigned_user_id"])

def downgrade():
    op.drop_index("ix_workflow_actions_queue",table_name="workflow_actions")
    for column in ("action_number","template_code","ticket_id","enquiry_id","job_card_id","milestone_id","department_id","assigned_role","assigned_user_id","due_at","status","parent_action_id","previous_action_id","next_action_id","created_by","created_at"):
        op.drop_index(f"ix_workflow_actions_{column}",table_name="workflow_actions")
    op.drop_table("workflow_actions");op.drop_index("ix_workflow_templates_active",table_name="workflow_templates");op.drop_index("ix_workflow_templates_code",table_name="workflow_templates");op.drop_table("workflow_templates")
