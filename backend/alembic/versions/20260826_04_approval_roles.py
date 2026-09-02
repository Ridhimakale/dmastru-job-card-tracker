"""Add stage-specific approval roles and Job Card approvers."""
from alembic import op
import sqlalchemy as sa

revision="20260826_04"
down_revision="20260826_03"
branch_labels=None
depends_on=None

def upgrade():
    if op.get_bind().dialect.name=="postgresql":
        op.execute("ALTER TYPE role ADD VALUE IF NOT EXISTS 'UNIT_REPRESENTATIVE'")
        op.execute("ALTER TYPE role ADD VALUE IF NOT EXISTS 'DIRECTOR'")
    with op.batch_alter_table("job_cards") as batch:
        batch.add_column(sa.Column("unit_representative_id",sa.Integer,nullable=True))
        batch.add_column(sa.Column("director_id",sa.Integer,nullable=True))
        batch.create_foreign_key("fk_job_cards_unit_representative_users","users",["unit_representative_id"],["id"])
        batch.create_foreign_key("fk_job_cards_director_users","users",["director_id"],["id"])

def downgrade():
    with op.batch_alter_table("job_cards") as batch:
        batch.drop_constraint("fk_job_cards_director_users",type_="foreignkey")
        batch.drop_constraint("fk_job_cards_unit_representative_users",type_="foreignkey")
        batch.drop_column("director_id")
        batch.drop_column("unit_representative_id")
