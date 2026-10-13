"""Add invoice, payment confirmation and receipt history."""
from alembic import op
import sqlalchemy as sa

revision="20261006_13"
down_revision="20261006_12"
branch_labels=None
depends_on=None

def upgrade():
    op.add_column("job_cards",sa.Column("payment_gate_enabled",sa.Boolean(),nullable=False,server_default=sa.true()))
    with op.batch_alter_table("job_cards") as b:
        b.add_column(sa.Column("source_ticket_id",sa.Uuid(),nullable=True));b.add_column(sa.Column("source_enquiry_id",sa.Uuid(),nullable=True))
        b.add_column(sa.Column("project_name",sa.String(200),nullable=True));b.add_column(sa.Column("package_code",sa.String(64),nullable=True));b.add_column(sa.Column("scope",sa.Text(),nullable=True))
        b.create_foreign_key("fk_job_cards_source_ticket_id_tickets","tickets",["source_ticket_id"],["id"])
        b.create_foreign_key("fk_job_cards_source_enquiry_id_enquiries","enquiries",["source_enquiry_id"],["id"])
        b.create_index("ix_job_cards_source_ticket_id",["source_ticket_id"]);b.create_index("ix_job_cards_source_enquiry_id",["source_enquiry_id"]);b.create_index("ix_job_cards_package_code",["package_code"])
    op.create_index("uq_job_cards_source_ticket","job_cards",["source_ticket_id"],unique=True,sqlite_where=sa.text("source_ticket_id IS NOT NULL"),postgresql_where=sa.text("source_ticket_id IS NOT NULL"))
    op.create_index("uq_job_cards_source_enquiry","job_cards",["source_enquiry_id"],unique=True,sqlite_where=sa.text("source_enquiry_id IS NOT NULL"),postgresql_where=sa.text("source_enquiry_id IS NOT NULL"))
    op.create_index("uq_enquiries_source_ticket","enquiries",["source_ticket_id"],unique=True,sqlite_where=sa.text("source_ticket_id IS NOT NULL"),postgresql_where=sa.text("source_ticket_id IS NOT NULL"))
    op.create_index("uq_ticket_rel_ticket","ticket_relationships",["ticket_id","related_ticket_id","relationship_type"],unique=True,sqlite_where=sa.text("related_ticket_id IS NOT NULL"),postgresql_where=sa.text("related_ticket_id IS NOT NULL"))
    op.create_index("uq_ticket_rel_enquiry","ticket_relationships",["ticket_id","related_enquiry_id","relationship_type"],unique=True,sqlite_where=sa.text("related_enquiry_id IS NOT NULL"),postgresql_where=sa.text("related_enquiry_id IS NOT NULL"))
    op.create_index("uq_ticket_rel_job_card","ticket_relationships",["ticket_id","related_job_card_id","relationship_type"],unique=True,sqlite_where=sa.text("related_job_card_id IS NOT NULL"),postgresql_where=sa.text("related_job_card_id IS NOT NULL"))
    with op.batch_alter_table("enquiry_followups") as b:
        b.add_column(sa.Column("attachment_key",sa.String(255),nullable=True));b.add_column(sa.Column("attachment_mime_type",sa.String(120),nullable=True))
    op.create_table("ticket_attachments",
        sa.Column("id",sa.Integer(),primary_key=True),sa.Column("ticket_id",sa.Uuid(),nullable=False),sa.Column("file_name",sa.String(255),nullable=False),
        sa.Column("storage_key",sa.String(255),nullable=False),sa.Column("file_size",sa.Integer(),nullable=False),sa.Column("mime_type",sa.String(120),nullable=False),
        sa.Column("uploaded_by",sa.Integer(),nullable=False),sa.Column("uploaded_at",sa.DateTime(timezone=True),nullable=False),
        sa.ForeignKeyConstraint(["ticket_id"],["tickets.id"],ondelete="CASCADE"),sa.ForeignKeyConstraint(["uploaded_by"],["users.id"]),sa.UniqueConstraint("storage_key"))
    op.create_index("ix_ticket_attachments_ticket_id","ticket_attachments",["ticket_id"]);op.create_index("ix_ticket_attachments_uploaded_by","ticket_attachments",["uploaded_by"]);op.create_index("ix_ticket_attachments_uploaded_at","ticket_attachments",["uploaded_at"])
    op.create_table("enquiry_documents",
        sa.Column("id",sa.Integer(),primary_key=True),sa.Column("enquiry_id",sa.Uuid(),nullable=False),sa.Column("file_name",sa.String(255),nullable=False),
        sa.Column("storage_key",sa.String(255),nullable=False),sa.Column("file_size",sa.Integer(),nullable=False),sa.Column("mime_type",sa.String(120),nullable=False),
        sa.Column("document_type",sa.String(80),nullable=False),sa.Column("remarks",sa.Text()),sa.Column("uploaded_by",sa.Integer(),nullable=False),sa.Column("uploaded_at",sa.DateTime(timezone=True),nullable=False),
        sa.ForeignKeyConstraint(["enquiry_id"],["enquiries.id"],ondelete="CASCADE"),sa.ForeignKeyConstraint(["uploaded_by"],["users.id"]),sa.UniqueConstraint("storage_key"))
    op.create_index("ix_enquiry_documents_enquiry_id","enquiry_documents",["enquiry_id"]);op.create_index("ix_enquiry_documents_document_type","enquiry_documents",["document_type"]);op.create_index("ix_enquiry_documents_uploaded_by","enquiry_documents",["uploaded_by"]);op.create_index("ix_enquiry_documents_uploaded_at","enquiry_documents",["uploaded_at"])
    with op.batch_alter_table("tickets") as b:
        b.add_column(sa.Column("milestone_id",sa.Integer(),nullable=True));b.create_foreign_key("fk_tickets_milestone_id_milestones","milestones",["milestone_id"],["id"]);b.create_index("ix_tickets_milestone_id",["milestone_id"])
    op.create_table("payments",
        sa.Column("id",sa.Integer(),primary_key=True),sa.Column("invoice_number",sa.String(32),nullable=False),
        sa.Column("job_card_id",sa.Integer(),nullable=False),sa.Column("milestone_id",sa.Integer()),sa.Column("source_ticket_id",sa.Uuid()),
        sa.Column("description",sa.String(240),nullable=False),sa.Column("amount_due",sa.Numeric(12,2),nullable=False),
        sa.Column("amount_paid",sa.Numeric(12,2),nullable=False),sa.Column("currency",sa.String(3),nullable=False),
        sa.Column("status",sa.String(24),nullable=False),sa.Column("invoice_date",sa.Date(),nullable=False),sa.Column("due_date",sa.Date()),
        sa.Column("remarks",sa.Text()),sa.Column("created_by",sa.Integer(),nullable=False),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False),sa.ForeignKeyConstraint(["job_card_id"],["job_cards.id"]),
        sa.ForeignKeyConstraint(["milestone_id"],["milestones.id"]),sa.ForeignKeyConstraint(["source_ticket_id"],["tickets.id"]),
        sa.ForeignKeyConstraint(["created_by"],["users.id"]),sa.UniqueConstraint("invoice_number"),
        sa.CheckConstraint("amount_due > 0",name="ck_payments_amount_due_positive"),
        sa.CheckConstraint("amount_paid >= 0 AND amount_paid <= amount_due",name="ck_payments_amount_paid_range"),
        sa.CheckConstraint("status IN ('PENDING','INVOICE_RAISED','PARTIALLY_PAID','PAID','OVERDUE','CANCELLED')",name="ck_payments_status"))
    for column in ("job_card_id","milestone_id","source_ticket_id","status","invoice_date","due_date","created_by"):
        op.create_index(f"ix_payments_{column}","payments",[column])
    # At most one active invoice per project or milestone. Partial indexes also
    # permit existing payment history for other milestone IDs.
    op.create_index("uq_payments_project_invoice","payments",["job_card_id"],unique=True,sqlite_where=sa.text("milestone_id IS NULL AND status != 'CANCELLED'"),postgresql_where=sa.text("milestone_id IS NULL AND status != 'CANCELLED'"))
    op.create_index("uq_payments_milestone_invoice","payments",["milestone_id"],unique=True,sqlite_where=sa.text("milestone_id IS NOT NULL AND status != 'CANCELLED'"),postgresql_where=sa.text("milestone_id IS NOT NULL AND status != 'CANCELLED'"))
    op.create_table("payment_receipts",
        sa.Column("id",sa.Integer(),primary_key=True),sa.Column("payment_id",sa.Integer(),nullable=False),sa.Column("amount",sa.Numeric(12,2),nullable=False),
        sa.Column("external_reference",sa.String(120)),sa.Column("received_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("confirmed_by",sa.Integer(),nullable=False),sa.Column("remarks",sa.Text()),
        sa.ForeignKeyConstraint(["payment_id"],["payments.id"],ondelete="CASCADE"),sa.ForeignKeyConstraint(["confirmed_by"],["users.id"]),
        sa.UniqueConstraint("external_reference"),sa.CheckConstraint("amount > 0",name="ck_payment_receipts_amount_positive"))
    op.create_index("ix_payment_receipts_payment_id","payment_receipts",["payment_id"])
    op.create_index("ix_payment_receipts_confirmed_by","payment_receipts",["confirmed_by"])

def downgrade():
    op.drop_table("enquiry_documents")
    op.drop_table("ticket_attachments")
    with op.batch_alter_table("enquiry_followups") as b:
        b.drop_column("attachment_mime_type");b.drop_column("attachment_key")
    op.drop_index("uq_ticket_rel_job_card",table_name="ticket_relationships")
    op.drop_index("uq_ticket_rel_enquiry",table_name="ticket_relationships")
    op.drop_index("uq_ticket_rel_ticket",table_name="ticket_relationships")
    op.drop_index("uq_enquiries_source_ticket",table_name="enquiries")
    op.drop_index("uq_job_cards_source_enquiry",table_name="job_cards")
    op.drop_index("uq_job_cards_source_ticket",table_name="job_cards")
    with op.batch_alter_table("tickets") as b:
        b.drop_index("ix_tickets_milestone_id");b.drop_constraint("fk_tickets_milestone_id_milestones",type_="foreignkey");b.drop_column("milestone_id")
    op.drop_table("payment_receipts")
    op.drop_index("uq_payments_milestone_invoice",table_name="payments")
    op.drop_index("uq_payments_project_invoice",table_name="payments")
    op.drop_table("payments")
    with op.batch_alter_table("job_cards") as b:
        b.drop_index("ix_job_cards_package_code");b.drop_index("ix_job_cards_source_enquiry_id");b.drop_index("ix_job_cards_source_ticket_id")
        b.drop_constraint("fk_job_cards_source_enquiry_id_enquiries",type_="foreignkey");b.drop_constraint("fk_job_cards_source_ticket_id_tickets",type_="foreignkey")
        b.drop_column("scope");b.drop_column("package_code");b.drop_column("project_name");b.drop_column("source_enquiry_id");b.drop_column("source_ticket_id")
    op.drop_column("job_cards","payment_gate_enabled")
