"""Track consultant mandays per milestone allocation."""
from alembic import op
import sqlalchemy as sa

revision = "20261020_50"
down_revision = "20261019_49"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("planned_visits") as batch_op:
        batch_op.add_column(sa.Column("co_consultant_id", sa.Integer(), sa.ForeignKey("users.id", name="fk_planned_visits_co_consultant_users"), nullable=True))
        batch_op.add_column(sa.Column("subject_expert_id", sa.Integer(), sa.ForeignKey("users.id", name="fk_planned_visits_subject_expert_users"), nullable=True))
    op.create_index("ix_planned_visits_co_consultant_id", "planned_visits", ["co_consultant_id"])
    op.create_index("ix_planned_visits_subject_expert_id", "planned_visits", ["subject_expert_id"])
    op.create_table(
        "milestone_consultant_allocations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("milestone_id", sa.Integer(), sa.ForeignKey("milestones.id", ondelete="CASCADE"), nullable=False),
        sa.Column("consultant_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("allocated_mandays", sa.Numeric(10, 2), nullable=False),
        sa.Column("assigned_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_milestone_consultant_allocations_milestone_id", "milestone_consultant_allocations", ["milestone_id"])
    op.create_index("ix_milestone_consultant_allocations_consultant_id", "milestone_consultant_allocations", ["consultant_id"])
    op.create_index("ix_milestone_consultant_allocations_active", "milestone_consultant_allocations", ["active"])
    op.create_index("ix_milestone_consultant_alloc_active", "milestone_consultant_allocations", ["milestone_id", "active"])
    op.create_index("ix_consultant_alloc_active", "milestone_consultant_allocations", ["consultant_id", "active"])
    op.execute(sa.text("""
        INSERT INTO milestone_consultant_allocations
            (milestone_id, consultant_id, allocated_mandays, assigned_by, assigned_at, active)
        SELECT id, responsible_consultant_id, COALESCE(planned_mandays, 0), NULL, CURRENT_TIMESTAMP, TRUE
        FROM milestones WHERE responsible_consultant_id IS NOT NULL AND COALESCE(planned_mandays, 0) > 0
    """))


def downgrade():
    op.drop_index("ix_consultant_alloc_active", table_name="milestone_consultant_allocations")
    op.drop_index("ix_milestone_consultant_alloc_active", table_name="milestone_consultant_allocations")
    op.drop_index("ix_milestone_consultant_allocations_active", table_name="milestone_consultant_allocations")
    op.drop_index("ix_milestone_consultant_allocations_consultant_id", table_name="milestone_consultant_allocations")
    op.drop_index("ix_milestone_consultant_allocations_milestone_id", table_name="milestone_consultant_allocations")
    op.drop_table("milestone_consultant_allocations")
    op.drop_index("ix_planned_visits_subject_expert_id", table_name="planned_visits")
    op.drop_index("ix_planned_visits_co_consultant_id", table_name="planned_visits")
    with op.batch_alter_table("planned_visits") as batch_op:
        batch_op.drop_column("subject_expert_id")
        batch_op.drop_column("co_consultant_id")
