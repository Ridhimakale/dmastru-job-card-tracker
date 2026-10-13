"""Install default operational departments for production workflows."""
from datetime import datetime, timezone
from alembic import op
import sqlalchemy as sa

revision="20261010_19"
down_revision="20261010_18"
branch_labels=None
depends_on=None

def upgrade():
    departments=sa.table("departments",sa.column("code",sa.String),sa.column("name",sa.String),sa.column("active",sa.Boolean),sa.column("created_at",sa.DateTime(timezone=True)))
    existing={row[0] for row in op.get_bind().execute(sa.select(departments.c.code)).all()}
    defaults=(("MARKETING","Marketing"),("CONSULTING","Consulting"),("REVIEW","Review"),("UNIT_REPRESENTATIVE","Unit Representative"),("MANAGEMENT","Management"),("DIRECTOR","Director"),("ADMIN","Administration"),("OPERATIONS","Operations"),("FINANCE","Finance"))
    missing=[{"code":code,"name":name,"active":True,"created_at":datetime.now(timezone.utc)} for code,name in defaults if code not in existing]
    if missing:op.bulk_insert(departments,missing)
    op.execute(sa.text("UPDATE users SET department_id=(SELECT id FROM departments WHERE code='MARKETING') WHERE department_id IS NULL AND role='MARKETING'"))

def downgrade():
    # Do not remove departments after users or workflow records may depend on them.
    pass
