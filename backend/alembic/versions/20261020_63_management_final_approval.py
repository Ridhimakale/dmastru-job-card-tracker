"""Move final Job Card approval responsibilities to Management."""
from alembic import op
import sqlalchemy as sa

revision = "20261020_63"
down_revision = "20261020_62"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    # Preserve existing approver access by converting Director accounts to Management.
    op.execute(sa.text("UPDATE users SET role='MANAGEMENT' WHERE role='DIRECTOR'"))
    management_department = bind.execute(sa.text("SELECT id FROM departments WHERE code='MANAGEMENT' LIMIT 1")).scalar()
    director_department = bind.execute(sa.text("SELECT id FROM departments WHERE code='DIRECTOR' LIMIT 1")).scalar()
    if management_department is not None and director_department is not None:
        op.execute(sa.text("UPDATE users SET department_id=:management WHERE department_id=:director").bindparams(management=management_department, director=director_department))
        op.execute(sa.text("UPDATE departments SET active=:active WHERE id=:id").bindparams(active=False, id=director_department))
    op.execute(sa.text("UPDATE assignments SET assignment_type='MANAGEMENT' WHERE assignment_type='DIRECTOR'"))
    op.execute(sa.text("UPDATE approvals SET stage='MANAGEMENT_REVIEW' WHERE stage='DIRECTOR_REVIEW'"))
    op.execute(sa.text("UPDATE job_cards SET director_id=NULL"))


def downgrade():
    # Roles and approvals now belong to Management; restoring a distinct Director role
    # would require knowing which converted accounts and approval rows were historical.
    pass
