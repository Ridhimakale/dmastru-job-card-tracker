"""Add archival, visit review, master-data and settings tables."""
from alembic import op
import sqlalchemy as sa

revision="20260826_03"
down_revision="20260825_02"
branch_labels=None
depends_on=None
def archive_fields(table):
    with op.batch_alter_table(table) as b:
        b.add_column(sa.Column("deleted_at",sa.DateTime(timezone=True),nullable=True)); b.add_column(sa.Column("deleted_by",sa.Integer,nullable=True)); b.create_foreign_key(f"fk_{table}_deleted_by_users","users",["deleted_by"],["id"])
def upgrade():
    for table in ("msmes","job_cards","visits","documents"): archive_fields(table)
    with op.batch_alter_table("visits") as b:
        b.add_column(sa.Column("rejection_reason",sa.Text,nullable=True)); b.add_column(sa.Column("rejected_by",sa.Integer,nullable=True)); b.add_column(sa.Column("rejected_at",sa.DateTime(timezone=True),nullable=True)); b.create_foreign_key("fk_visits_rejected_by_users","users",["rejected_by"],["id"])
    op.create_table("master_data",sa.Column("id",sa.Integer,primary_key=True),sa.Column("category",sa.String(64),nullable=False),sa.Column("code",sa.String(64),nullable=False),sa.Column("label",sa.String(160),nullable=False),sa.Column("active",sa.Boolean,nullable=False),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False)); op.create_index("ix_master_data_category","master_data",["category"]); op.create_index("ix_master_data_active","master_data",["active"])
    op.create_table("system_settings",sa.Column("key",sa.String(100),primary_key=True),sa.Column("value",sa.Text,nullable=False),sa.Column("updated_by",sa.Integer,sa.ForeignKey("users.id")),sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False))
def downgrade():
    op.drop_table("system_settings"); op.drop_table("master_data")
    with op.batch_alter_table("visits") as b:
        b.drop_constraint("fk_visits_rejected_by_users",type_="foreignkey"); b.drop_column("rejected_at"); b.drop_column("rejected_by"); b.drop_column("rejection_reason")
    for table in ("documents","visits","job_cards","msmes"):
        with op.batch_alter_table(table) as b: b.drop_constraint(f"fk_{table}_deleted_by_users",type_="foreignkey"); b.drop_column("deleted_by"); b.drop_column("deleted_at")
