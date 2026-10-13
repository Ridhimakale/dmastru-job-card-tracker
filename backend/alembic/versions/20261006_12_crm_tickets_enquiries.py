"""Add CRM foundation: departments, generated numbers, tickets and enquiries."""
from alembic import op
import sqlalchemy as sa

revision="20261006_12"
down_revision="20261005_11"
branch_labels=None
depends_on=None

def upgrade():
    bind=op.get_bind()
    if bind.dialect.name=="postgresql":
        op.execute("ALTER TYPE role ADD VALUE IF NOT EXISTS 'MARKETING'")
    op.create_table("departments",
        sa.Column("id",sa.Integer(),primary_key=True),sa.Column("code",sa.String(48),nullable=False),
        sa.Column("name",sa.String(120),nullable=False),sa.Column("active",sa.Boolean(),nullable=False),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),sa.UniqueConstraint("code"),sa.UniqueConstraint("name"))
    op.create_index("ix_departments_code","departments",["code"],unique=True);op.create_index("ix_departments_active","departments",["active"])
    with op.batch_alter_table("users") as b:
        b.add_column(sa.Column("department_id",sa.Integer(),nullable=True));b.create_foreign_key("fk_users_department_id_departments","departments",["department_id"],["id"]);b.create_index("ix_users_department_id",["department_id"])
    op.create_table("business_sequences",sa.Column("key",sa.String(24),primary_key=True),sa.Column("value",sa.Integer(),nullable=False))
    op.create_table("enquiries",
        sa.Column("id",sa.Uuid(),primary_key=True),sa.Column("number",sa.String(24),nullable=False),
        sa.Column("source_ticket_id",sa.Uuid(),nullable=True),sa.Column("msme_id",sa.Integer(),nullable=True),
        sa.Column("company_name",sa.String(200),nullable=False),sa.Column("contact_person",sa.String(160)),
        sa.Column("email",sa.String(320)),sa.Column("phone",sa.String(40)),sa.Column("industry",sa.String(120)),
        sa.Column("location",sa.String(200)),sa.Column("requirement",sa.Text(),nullable=False),sa.Column("enquiry_date",sa.Date(),nullable=False),
        sa.Column("source",sa.String(80)),sa.Column("package_code",sa.String(64)),sa.Column("expected_scope",sa.Text()),
        sa.Column("assigned_marketing_user_id",sa.Integer()),sa.Column("status",sa.String(24),nullable=False),
        sa.Column("follow_up_date",sa.Date()),sa.Column("remarks",sa.Text()),sa.Column("created_by",sa.Integer(),nullable=False),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False),
        sa.ForeignKeyConstraint(["msme_id"],["msmes.id"]),sa.ForeignKeyConstraint(["assigned_marketing_user_id"],["users.id"]),
        sa.ForeignKeyConstraint(["created_by"],["users.id"]),sa.UniqueConstraint("number"),
        sa.CheckConstraint("status IN ('NEW','CONTACTED','FOLLOW_UP','QUALIFIED','FINALISED','LOST','CANCELLED','CONVERTED')",name="ck_enquiries_status"))
    for column in ("number","msme_id","company_name","email","phone","industry","location","enquiry_date","source","package_code","assigned_marketing_user_id","status","follow_up_date","created_by","created_at"):
        op.create_index(f"ix_enquiries_{column}","enquiries",[column],unique=column=="number")
    op.create_table("tickets",
        sa.Column("id",sa.Uuid(),primary_key=True),sa.Column("number",sa.String(24),nullable=False),sa.Column("title",sa.String(200),nullable=False),
        sa.Column("description",sa.Text(),nullable=False),sa.Column("ticket_type",sa.String(40),nullable=False),sa.Column("priority",sa.String(16),nullable=False),
        sa.Column("created_by",sa.Integer(),nullable=False),sa.Column("created_department_id",sa.Integer()),sa.Column("assigned_department_id",sa.Integer()),
        sa.Column("assigned_user_id",sa.Integer()),sa.Column("status",sa.String(24),nullable=False),sa.Column("due_date",sa.DateTime(timezone=True)),
        sa.Column("parent_ticket_id",sa.Uuid()),sa.Column("enquiry_id",sa.Uuid()),sa.Column("job_card_id",sa.Integer()),sa.Column("remarks",sa.Text()),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False),sa.Column("completed_at",sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["created_by"],["users.id"]),sa.ForeignKeyConstraint(["created_department_id"],["departments.id"]),
        sa.ForeignKeyConstraint(["assigned_department_id"],["departments.id"]),sa.ForeignKeyConstraint(["assigned_user_id"],["users.id"]),
        sa.ForeignKeyConstraint(["parent_ticket_id"],["tickets.id"]),sa.ForeignKeyConstraint(["job_card_id"],["job_cards.id"]),
        sa.CheckConstraint("status IN ('OPEN','IN_PROGRESS','WAITING','COMPLETED','RETURNED','REJECTED','ON_HOLD','CANCELLED')",name="ck_tickets_status"),
        sa.CheckConstraint("priority IN ('LOW','NORMAL','HIGH','URGENT')",name="ck_tickets_priority"),
        sa.CheckConstraint("ticket_type IN ('NEW_ENQUIRY','FOLLOW_UP','PAYMENT_REQUEST','DOCUMENT_REQUEST','CORRECTION','REVIEW','APPROVAL','CLIENT_COMMUNICATION','INTERNAL_ACTION','OTHER')",name="ck_tickets_type"),sa.UniqueConstraint("number"))
    for column in ("number","ticket_type","priority","created_by","assigned_department_id","assigned_user_id","status","due_date","parent_ticket_id","enquiry_id","job_card_id","created_at"):
        op.create_index(f"ix_tickets_{column}","tickets",[column],unique=column=="number")
    # Resolve the ticket/enquiry cycle after both target tables exist.
    with op.batch_alter_table("tickets") as b:
        b.create_foreign_key("fk_tickets_enquiry_id_enquiries","enquiries",["enquiry_id"],["id"])
    with op.batch_alter_table("enquiries") as b:
        b.create_foreign_key("fk_enquiries_source_ticket_id_tickets","tickets",["source_ticket_id"],["id"])
    op.create_table("ticket_relationships",
        sa.Column("id",sa.Integer(),primary_key=True),sa.Column("ticket_id",sa.Uuid(),nullable=False),sa.Column("related_ticket_id",sa.Uuid()),
        sa.Column("related_enquiry_id",sa.Uuid()),sa.Column("related_job_card_id",sa.Integer()),sa.Column("relationship_type",sa.String(24),nullable=False),
        sa.Column("created_by",sa.Integer(),nullable=False),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
        sa.ForeignKeyConstraint(["ticket_id"],["tickets.id"],ondelete="CASCADE"),sa.ForeignKeyConstraint(["related_ticket_id"],["tickets.id"]),
        sa.ForeignKeyConstraint(["related_enquiry_id"],["enquiries.id"]),sa.ForeignKeyConstraint(["related_job_card_id"],["job_cards.id"]),
        sa.ForeignKeyConstraint(["created_by"],["users.id"]),
        sa.CheckConstraint("(CASE WHEN related_ticket_id IS NULL THEN 0 ELSE 1 END)+(CASE WHEN related_enquiry_id IS NULL THEN 0 ELSE 1 END)+(CASE WHEN related_job_card_id IS NULL THEN 0 ELSE 1 END)=1",name="ck_ticket_relationship_one_target"))
    for column in ("ticket_id","related_ticket_id","related_enquiry_id","related_job_card_id","relationship_type"):
        op.create_index(f"ix_ticket_relationships_{column}","ticket_relationships",[column])
    op.create_table("enquiry_followups",
        sa.Column("id",sa.Integer(),primary_key=True),sa.Column("enquiry_id",sa.Uuid(),nullable=False),sa.Column("follow_up_date",sa.DateTime(timezone=True),nullable=False),
        sa.Column("follow_up_type",sa.String(32),nullable=False),sa.Column("person_contacted",sa.String(160)),sa.Column("notes",sa.Text(),nullable=False),
        sa.Column("next_follow_up_date",sa.DateTime(timezone=True)),sa.Column("responsible_user_id",sa.Integer(),nullable=False),sa.Column("status",sa.String(24),nullable=False),
        sa.Column("attachment_name",sa.String(255)),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
        sa.ForeignKeyConstraint(["enquiry_id"],["enquiries.id"],ondelete="CASCADE"),sa.ForeignKeyConstraint(["responsible_user_id"],["users.id"]),
        sa.CheckConstraint("status IN ('OPEN','COMPLETED','CANCELLED')",name="ck_enquiry_followups_status"),
        sa.CheckConstraint("follow_up_type IN ('CALL','MEETING','EMAIL','CLIENT_DISCUSSION','FOLLOW_UP','REQUIREMENT_UPDATE','COMMERCIAL_DISCUSSION','SCOPE_DISCUSSION')",name="ck_enquiry_followups_type"))
    for column in ("enquiry_id","follow_up_date","follow_up_type","next_follow_up_date","responsible_user_id","status"):
        op.create_index(f"ix_enquiry_followups_{column}","enquiry_followups",[column])

def downgrade():
    op.drop_table("enquiry_followups")
    op.drop_table("ticket_relationships")
    op.drop_table("tickets")
    op.drop_table("enquiries")
    op.drop_table("business_sequences")
    with op.batch_alter_table("users") as b:
        b.drop_index("ix_users_department_id");b.drop_constraint("fk_users_department_id_departments",type_="foreignkey");b.drop_column("department_id")
    op.drop_index("ix_departments_active",table_name="departments");op.drop_index("ix_departments_code",table_name="departments");op.drop_table("departments")
    # PostgreSQL enum values cannot be removed safely in place. The new role is
    # intentionally retained on downgrade; application code simply stops exposing it.
