"""Link M1 visit deliverable tasks to planner visits."""
from datetime import datetime, timedelta, timezone
from alembic import op
import sqlalchemy as sa

revision = "20261016_39"
down_revision = "20261016_38"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("job_activities")}
    if "planned_visit_id" not in columns:
        op.add_column("job_activities", sa.Column("planned_visit_id", sa.Integer(), nullable=True))
        with op.batch_alter_table("job_activities") as batch:
            batch.create_foreign_key("fk_job_activities_planned_visit_id", "planned_visits", ["planned_visit_id"], ["id"], ondelete="CASCADE")
            batch.create_unique_constraint("uq_job_activity_planned_visit_name", ["planned_visit_id", "name"])
        op.create_index("ix_job_activities_planned_visit_id", "job_activities", ["planned_visit_id"])

    bind = op.get_bind()
    activity_types = sa.table("activity_types", sa.column("id", sa.Integer), sa.column("code", sa.String), sa.column("name", sa.String), sa.column("active", sa.Boolean), sa.column("created_at", sa.DateTime(timezone=True)))
    type_id = bind.execute(sa.select(activity_types.c.id).where(activity_types.c.code == "DOCUMENTATION")).scalar_one_or_none()
    if type_id is None:
        bind.execute(activity_types.insert().values(code="DOCUMENTATION", name="Documentation", active=True, created_at=datetime.now(timezone.utc)))
        type_id = bind.execute(sa.select(activity_types.c.id).where(activity_types.c.code == "DOCUMENTATION")).scalar_one()

    planned_visits = sa.table("planned_visits", sa.column("id", sa.Integer), sa.column("job_card_id", sa.Integer), sa.column("milestone_id", sa.Integer), sa.column("consultant_id", sa.Integer), sa.column("visit_date", sa.Date), sa.column("status", sa.String))
    milestones = sa.table("milestones", sa.column("id", sa.Integer), sa.column("code", sa.String))
    activities = sa.table("job_activities", sa.column("id", sa.Integer), sa.column("job_card_id", sa.Integer), sa.column("milestone_id", sa.Integer), sa.column("planned_visit_id", sa.Integer), sa.column("activity_type_id", sa.Integer), sa.column("name", sa.String), sa.column("description", sa.Text), sa.column("assigned_user_id", sa.Integer), sa.column("start_date", sa.Date), sa.column("end_date", sa.Date), sa.column("duration_minutes", sa.Integer), sa.column("status", sa.String), sa.column("remarks", sa.Text), sa.column("created_by", sa.Integer), sa.column("created_at", sa.DateTime(timezone=True)), sa.column("updated_at", sa.DateTime(timezone=True)))
    visits = bind.execute(sa.select(planned_visits).join(milestones, milestones.c.id == planned_visits.c.milestone_id).where(milestones.c.code == "M1", planned_visits.c.status == "PLANNED")).mappings()
    for visit in visits:
        for name, description in (("Case study", "Prepare and submit the M1 case study for this visit."), ("Photo", "Upload a photo record from this M1 visit."), ("Plan", "Submit the plan and next steps for this M1 visit.")):
            exists = bind.execute(sa.select(activities.c.id).where(activities.c.planned_visit_id == visit["id"], activities.c.name == name)).first()
            if not exists:
                now = datetime.now(timezone.utc)
                bind.execute(activities.insert().values(job_card_id=visit["job_card_id"], milestone_id=visit["milestone_id"], planned_visit_id=visit["id"], activity_type_id=type_id, name=name, description=description, assigned_user_id=visit["consultant_id"], start_date=visit["visit_date"], end_date=visit["visit_date"] + timedelta(days=1), duration_minutes=0, status="PLANNED", remarks="Required M1 visit deliverable", created_by=visit["consultant_id"], created_at=now, updated_at=now))


def downgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("job_activities")}
    if "planned_visit_id" in columns:
        op.drop_index("ix_job_activities_planned_visit_id", table_name="job_activities")
        with op.batch_alter_table("job_activities") as batch:
            batch.drop_constraint("uq_job_activity_planned_visit_name", type_="unique")
            batch.drop_constraint("fk_job_activities_planned_visit_id", type_="foreignkey")
            batch.drop_column("planned_visit_id")
