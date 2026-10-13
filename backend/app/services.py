from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from .models import Assignment, JobActivity, JobCard, Milestone, MilestoneReviewHistory, Payment, PlannedVisit, Visit

MD_PRECISION=Decimal("0.0001")
def manday_summary(db:Session, card:JobCard):
    visit_minutes=int(db.scalar(select(func.coalesce(func.sum(Visit.working_minutes),0)).where(Visit.job_card_id==card.id,Visit.status.in_(("SUBMITTED","REVIEWED","APPROVED")),Visit.deleted_at.is_(None))) or 0)
    standalone_activity_minutes=int(db.scalar(select(func.coalesce(func.sum(JobActivity.duration_minutes),0)).where(JobActivity.job_card_id==card.id,JobActivity.status=="COMPLETED",JobActivity.visit_id.is_(None))) or 0)
    planner_minutes=int(db.scalar(select(func.coalesce(func.sum(PlannedVisit.actual_duration_minutes),0)).where(PlannedVisit.job_card_id==card.id,PlannedVisit.status=="COMPLETED",~select(Visit.id).where(Visit.job_card_id==card.id,Visit.visit_date==PlannedVisit.actual_date,Visit.consultant_id==PlannedVisit.consultant_id,Visit.status.in_(("SUBMITTED","REVIEWED","APPROVED")),Visit.deleted_at.is_(None)).exists())) or 0)
    completed=visit_minutes+standalone_activity_minutes+planner_minutes
    minutes_per_manday=Decimal(str(card.hours_per_manday or 8))*60
    required=Decimal(str(card.required_md))*minutes_per_manday
    progress=(Decimal(completed)*100/required).quantize(Decimal(".01"),ROUND_HALF_UP) if required else Decimal(0)
    return {"completed_minutes":completed,"completed_hours":(Decimal(completed)/60).quantize(Decimal(".01")),"completed_md":(Decimal(completed)/minutes_per_manday).quantize(MD_PRECISION,ROUND_HALF_UP),"remaining_minutes":required-completed,"remaining_hours":((required-Decimal(completed))/60).quantize(Decimal(".01")),"remaining_md":(Decimal(str(card.required_md))-Decimal(completed)/minutes_per_manday).quantize(MD_PRECISION,ROUND_HALF_UP),"progress":min(progress,Decimal(100)),"overrun":completed>required}
def milestone_summary(db:Session, milestone:Milestone):
    actual=int(db.scalar(select(func.count(Visit.id)).where(Visit.milestone_id==milestone.id,Visit.status.in_(("SUBMITTED","REVIEWED","APPROVED")),Visit.deleted_at.is_(None))) or 0)
    progress=(Decimal(actual)*100/Decimal(milestone.planned_visits)).quantize(Decimal(".01")) if milestone.planned_visits else Decimal(0)
    card=db.get(JobCard,milestone.job_card_id)
    project_payment=db.scalar(select(Payment).where(Payment.job_card_id==card.id,Payment.milestone_id.is_(None),Payment.status=="PAID")) if card and card.payment_gate_enabled else None
    payment=db.scalar(select(Payment).where(Payment.milestone_id==milestone.id)) if card and card.payment_gate_enabled else None
    payment_status=payment.status if payment else ("PAYMENT_PENDING" if card and card.payment_gate_enabled else "PAID")
    overdue=payment and payment.status in ("INVOICE_RAISED","PARTIALLY_PAID") and payment.due_date and payment.due_date<__import__("datetime").date.today()
    if overdue: payment_status="OVERDUE"
    latest=db.scalar(select(MilestoneReviewHistory).where(MilestoneReviewHistory.milestone_id==milestone.id).order_by(MilestoneReviewHistory.cycle.desc(),MilestoneReviewHistory.id.desc()).limit(1))
    review_status="NOT_SUBMITTED";review_cycle=0;review_remarks=None
    if latest:
        review_cycle=latest.cycle
        events=list(db.scalars(select(MilestoneReviewHistory).where(MilestoneReviewHistory.milestone_id==milestone.id,MilestoneReviewHistory.cycle==latest.cycle).order_by(MilestoneReviewHistory.id)))
        stages={event.stage:event for event in events}
        decision=stages.get("UNIT") or stages.get("MANAGEMENT") or latest
        review_status=("MANAGEMENT_APPROVED" if decision.stage=="MANAGEMENT" and decision.decision=="APPROVED" else decision.decision)
        review_remarks=decision.comment
    gated=card and card.payment_gate_enabled and (not project_payment or not payment or payment.status!="PAID")
    if gated: status="LOCKED"
    elif actual<milestone.planned_visits: status="IN_PROGRESS" if actual else ("READY" if card and card.payment_gate_enabled else "NOT_STARTED")
    elif review_status=="APPROVED": status="COMPLETED"
    elif review_status in ("RETURNED","REJECTED"): status=review_status
    elif review_status=="SUBMITTED": status="UNDER_REVIEW"
    elif review_status=="MANAGEMENT_APPROVED": status="AWAITING_UNIT_CONFIRMATION"
    else: status="READY_FOR_REVIEW"
    return {"actual_visits":actual,"progress":min(progress,Decimal(100)),"status":status,"payment_status":payment_status,"review_status":review_status,"review_cycle":review_cycle,"review_remarks":review_remarks}
def card_dashboard_row(db:Session, card:JobCard):
    summary=manday_summary(db,card)
    progress=summary["progress"]
    delayed=card.project_end_date < __import__("datetime").date.today() and card.status not in ("COMPLETED","CANCELLED")
    return {"id":card.id,"number":card.number,"msme_id":card.msme_id,"status":"DELAYED" if delayed else card.status,"completed_md":summary["completed_md"],"remaining_md":summary["remaining_md"],"overrun":summary["overrun"],"progress":progress,"delay_days":max((__import__("datetime").date.today()-card.project_end_date).days,0) if delayed else 0}
