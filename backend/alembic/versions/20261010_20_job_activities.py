"""Add configurable activity types and project activities."""
from alembic import op
import sqlalchemy as sa

revision="20261010_20"
down_revision="20261010_19"
branch_labels=None
depends_on=None

def upgrade():
    op.create_table("activity_types",
        sa.Column("id",sa.Integer(),primary_key=True),
        sa.Column("code",sa.String(48),nullable=False,unique=True),
        sa.Column("name",sa.String(120),nullable=False,unique=True),
        sa.Column("active",sa.Boolean(),nullable=False,server_default=sa.true()),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False))
    op.create_index("ix_activity_types_code","activity_types",["code"],unique=True)
    op.create_index("ix_activity_types_active","activity_types",["active"])
    op.create_table("job_activities",
        sa.Column("id",sa.Integer(),primary_key=True),
        sa.Column("job_card_id",sa.Integer(),sa.ForeignKey("job_cards.id",ondelete="CASCADE"),nullable=False),
        sa.Column("milestone_id",sa.Integer(),sa.ForeignKey("milestones.id"),nullable=False),
        sa.Column("visit_id",sa.Integer(),sa.ForeignKey("visits.id"),nullable=True),
        sa.Column("activity_type_id",sa.Integer(),sa.ForeignKey("activity_types.id"),nullable=False),
        sa.Column("name",sa.String(200),nullable=False),
        sa.Column("description",sa.Text(),nullable=False),
        sa.Column("assigned_user_id",sa.Integer(),sa.ForeignKey("users.id"),nullable=False),
        sa.Column("start_date",sa.Date(),nullable=True),
        sa.Column("end_date",sa.Date(),nullable=True),
        sa.Column("duration_minutes",sa.Integer(),nullable=False,server_default="0"),
        sa.Column("status",sa.String(24),nullable=False,server_default="PLANNED"),
        sa.Column("remarks",sa.Text(),nullable=True),
        sa.Column("created_by",sa.Integer(),sa.ForeignKey("users.id"),nullable=False),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False),
        sa.CheckConstraint("status IN ('PLANNED','IN_PROGRESS','COMPLETED','CANCELLED')",name="ck_job_activities_status"))
    for name,column in (("job_card_id","job_card_id"),("milestone_id","milestone_id"),("visit_id","visit_id"),("activity_type_id","activity_type_id"),("assigned_user_id","assigned_user_id"),("start_date","start_date"),("status","status"),("created_by","created_by"),("created_at","created_at")):
        op.create_index(f"ix_job_activities_{name}","job_activities",[column])
    op.create_index("ix_job_activities_card_date","job_activities",["job_card_id","start_date"])
    op.create_table("job_activity_attachments",
        sa.Column("id",sa.Integer(),primary_key=True),
        sa.Column("activity_id",sa.Integer(),sa.ForeignKey("job_activities.id",ondelete="CASCADE"),nullable=False),
        sa.Column("file_name",sa.String(255),nullable=False),
        sa.Column("storage_key",sa.String(255),nullable=False,unique=True),
        sa.Column("file_size",sa.Integer(),nullable=False),
        sa.Column("mime_type",sa.String(120),nullable=False),
        sa.Column("uploaded_by",sa.Integer(),sa.ForeignKey("users.id"),nullable=False),
        sa.Column("uploaded_at",sa.DateTime(timezone=True),nullable=False))
    op.create_index("ix_job_activity_attachments_activity_id","job_activity_attachments",["activity_id"])
    op.create_index("ix_job_activity_attachments_uploaded_by","job_activity_attachments",["uploaded_by"])
    op.create_index("ix_job_activity_attachments_uploaded_at","job_activity_attachments",["uploaded_at"])
    types=sa.table("activity_types",sa.column("code",sa.String),sa.column("name",sa.String),sa.column("active",sa.Boolean),sa.column("created_at",sa.DateTime(timezone=True)))
    from datetime import datetime,timezone
    op.bulk_insert(types,[{"code":code,"name":name,"active":True,"created_at":datetime.now(timezone.utc)} for code,name in (("CLIENT_MEETING","Client meeting"),("DATA_COLLECTION","Data collection"),("ANALYSIS","Analysis"),("IMPLEMENTATION","Implementation"),("DOCUMENTATION","Documentation"),("OTHER","Other"))])

def downgrade():
    op.drop_table("job_activity_attachments")
    op.drop_table("job_activities")
    op.drop_table("activity_types")
