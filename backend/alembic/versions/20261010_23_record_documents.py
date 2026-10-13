"""Add versioned files for business records beyond Job Cards."""
from alembic import op
import sqlalchemy as sa

revision="20261010_23"
down_revision="20261010_22"
branch_labels=None
depends_on=None

def upgrade():
    op.create_table(
        "record_documents",
        sa.Column("id",sa.Integer(),primary_key=True),
        sa.Column("owner_job_card_id",sa.Integer(),sa.ForeignKey("job_cards.id",ondelete="CASCADE"),nullable=True),
        sa.Column("ticket_id",sa.Uuid(),sa.ForeignKey("tickets.id",ondelete="CASCADE"),nullable=True),
        sa.Column("enquiry_id",sa.Uuid(),sa.ForeignKey("enquiries.id",ondelete="CASCADE"),nullable=True),
        sa.Column("job_card_id",sa.Integer(),sa.ForeignKey("job_cards.id",ondelete="CASCADE"),nullable=True),
        sa.Column("milestone_id",sa.Integer(),sa.ForeignKey("milestones.id",ondelete="CASCADE"),nullable=True),
        sa.Column("action_id",sa.Uuid(),sa.ForeignKey("workflow_actions.id",ondelete="CASCADE"),nullable=True),
        sa.Column("visit_id",sa.Integer(),sa.ForeignKey("visits.id",ondelete="CASCADE"),nullable=True),
        sa.Column("payment_id",sa.Integer(),sa.ForeignKey("payments.id",ondelete="CASCADE"),nullable=True),
        sa.Column("milestone_review_id",sa.Integer(),sa.ForeignKey("milestone_review_history.id",ondelete="CASCADE"),nullable=True),
        sa.Column("visit_review_id",sa.Integer(),sa.ForeignKey("visit_review_history.id",ondelete="CASCADE"),nullable=True),
        sa.Column("approval_id",sa.Integer(),sa.ForeignKey("approvals.id",ondelete="CASCADE"),nullable=True),
        sa.Column("document_type",sa.String(80),nullable=False),
        sa.Column("file_name",sa.String(255),nullable=False),
        sa.Column("storage_key",sa.String(255),nullable=False,unique=True),
        sa.Column("file_size",sa.Integer(),nullable=False),
        sa.Column("mime_type",sa.String(120),nullable=False),
        sa.Column("version",sa.Integer(),nullable=False,server_default="1"),
        sa.Column("status",sa.String(24),nullable=False,server_default="SUBMITTED"),
        sa.Column("remarks",sa.Text(),nullable=True),
        sa.Column("uploaded_by",sa.Integer(),sa.ForeignKey("users.id"),nullable=False),
        sa.Column("uploaded_at",sa.DateTime(timezone=True),nullable=False),
        sa.CheckConstraint("(CASE WHEN ticket_id IS NULL THEN 0 ELSE 1 END)+(CASE WHEN enquiry_id IS NULL THEN 0 ELSE 1 END)+(CASE WHEN job_card_id IS NULL THEN 0 ELSE 1 END)+(CASE WHEN milestone_id IS NULL THEN 0 ELSE 1 END)+(CASE WHEN action_id IS NULL THEN 0 ELSE 1 END)+(CASE WHEN visit_id IS NULL THEN 0 ELSE 1 END)+(CASE WHEN payment_id IS NULL THEN 0 ELSE 1 END)+(CASE WHEN milestone_review_id IS NULL THEN 0 ELSE 1 END)+(CASE WHEN visit_review_id IS NULL THEN 0 ELSE 1 END)+(CASE WHEN approval_id IS NULL THEN 0 ELSE 1 END)=1",name="ck_record_documents_one_target"),
    )
    for column in ("owner_job_card_id","ticket_id","enquiry_id","job_card_id","milestone_id","action_id","visit_id","payment_id","milestone_review_id","visit_review_id","approval_id","document_type","status","uploaded_by","uploaded_at"):
        op.create_index(f"ix_record_documents_{column}","record_documents",[column])
    op.create_index("ix_record_documents_card_uploaded","record_documents",["owner_job_card_id","uploaded_at"])

def downgrade():
    op.drop_table("record_documents")
