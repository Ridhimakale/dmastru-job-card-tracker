from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from .models import Assignment, JobCard, Milestone, Visit

MD_MINUTES=480
MD_PRECISION=Decimal("0.0001")
def manday_summary(db:Session, card:JobCard):
    completed=int(db.scalar(select(func.coalesce(func.sum(Visit.working_minutes),0)).where(Visit.job_card_id==card.id,Visit.status=="SUBMITTED")) or 0)
    required=Decimal(str(card.required_md))*MD_MINUTES
    return {"completed_minutes":completed,"completed_hours":(Decimal(completed)/60).quantize(Decimal(".01")),"completed_md":(Decimal(completed)/MD_MINUTES).quantize(MD_PRECISION,ROUND_HALF_UP),"remaining_minutes":int(required)-completed,"remaining_md":(Decimal(str(card.required_md))-Decimal(completed)/MD_MINUTES).quantize(MD_PRECISION,ROUND_HALF_UP),"overrun":completed>required}
def milestone_summary(db:Session, milestone:Milestone):
    actual=int(db.scalar(select(func.count(Visit.id)).where(Visit.milestone_id==milestone.id,Visit.status=="SUBMITTED")) or 0)
    progress=(Decimal(actual)*100/Decimal(milestone.planned_visits)).quantize(Decimal(".01")) if milestone.planned_visits else Decimal(0)
    return {"actual_visits":actual,"progress":min(progress,Decimal(100)),"status":"COMPLETED" if actual>=milestone.planned_visits else ("IN_PROGRESS" if actual else "NOT_STARTED")}
def card_dashboard_row(db:Session, card:JobCard):
    summary=manday_summary(db,card)
    milestone_rows=[milestone_summary(db,m) for m in db.scalars(select(Milestone).where(Milestone.job_card_id==card.id))]
    progress=(sum((x["progress"] for x in milestone_rows),Decimal(0))/len(milestone_rows)).quantize(Decimal(".01")) if milestone_rows else Decimal(0)
    delayed=card.project_end_date < __import__("datetime").date.today() and card.status not in ("COMPLETED","CANCELLED")
    return {"id":card.id,"number":card.number,"msme_id":card.msme_id,"status":"DELAYED" if delayed else card.status,"completed_md":summary["completed_md"],"remaining_md":summary["remaining_md"],"overrun":summary["overrun"],"progress":progress,"delay_days":max((__import__("datetime").date.today()-card.project_end_date).days,0) if delayed else 0}
