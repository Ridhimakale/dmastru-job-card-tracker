"""Complete Admin master data, milestones and review metadata."""
from alembic import op
import sqlalchemy as sa
revision="20261012_25"
down_revision="20261012_24"
branch_labels=None
depends_on=None

def upgrade():
    for table,name,type_ in [
        ("msmes","email",sa.String(320)),("msmes","phone",sa.String(40)),("msmes","industry",sa.String(120)),("msmes","unit_name",sa.String(160)),
        ("job_cards","remarks",sa.Text()),("milestones","responsible_consultant_id",sa.Integer()),
        ("documents","reviewed_by",sa.Integer()),("documents","reviewed_at",sa.DateTime(timezone=True)),
        ("record_documents","reviewed_by",sa.Integer()),("record_documents","reviewed_at",sa.DateTime(timezone=True))]:
        op.add_column(table,sa.Column(name,type_,nullable=True))
    for table,column in [("milestones","responsible_consultant_id"),("documents","reviewed_by"),("record_documents","reviewed_by")]:
        with op.batch_alter_table(table) as batch:
            batch.create_foreign_key(f"fk_{table}_{column}","users",[column],["id"])
    for table,column in [("msmes","email"),("msmes","phone"),("msmes","industry"),("msmes","unit_name"),("milestones","responsible_consultant_id"),("documents","reviewed_by"),("record_documents","reviewed_by")]:
        op.create_index(f"ix_{table}_{column}",table,[column])

def downgrade():
    for table,column in [("msmes","email"),("msmes","phone"),("msmes","industry"),("msmes","unit_name"),("milestones","responsible_consultant_id"),("documents","reviewed_by"),("record_documents","reviewed_by")]:
        op.drop_index(f"ix_{table}_{column}",table_name=table)
    for table,column in [("milestones","responsible_consultant_id"),("documents","reviewed_by"),("record_documents","reviewed_by")]:
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(f"fk_{table}_{column}",type_="foreignkey")
    for table,column in [("msmes","email"),("msmes","phone"),("msmes","industry"),("msmes","unit_name"),("job_cards","remarks"),("milestones","responsible_consultant_id"),("documents","reviewed_by"),("documents","reviewed_at"),("record_documents","reviewed_by"),("record_documents","reviewed_at")]:
        op.drop_column(table,column)
