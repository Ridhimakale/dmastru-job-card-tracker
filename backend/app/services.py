from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session
from .models import Assignment, ClientMilestonePayment, JobActivity, JobCard, Milestone, MilestoneReviewHistory, Payment, PlannedVisit, Visit

MD_PRECISION=Decimal("0.0001")

def _planner_visit_is_reported():
    """Match a completed planner booking to its visit report, regardless of team role."""
    return select(Visit.id).where(
        or_(
            Visit.planned_visit_id==PlannedVisit.id,
            and_(
                Visit.planned_visit_id.is_(None),
                Visit.job_card_id==PlannedVisit.job_card_id,
                Visit.visit_date==PlannedVisit.actual_date,
                or_(
                    Visit.consultant_id==PlannedVisit.consultant_id,
                    Visit.co_consultant_id==PlannedVisit.consultant_id,
                    Visit.subject_expert_id==PlannedVisit.consultant_id,
                ),
            ),
        ),
        Visit.status.in_(("SUBMITTED","REVIEWED","APPROVED")),
        Visit.deleted_at.is_(None),
    ).exists()

def visit_effort_minutes(db:Session,job_card_id:int|None=None,milestone_id:int|None=None,consultant_id:int|None=None)->int:
    query=select(Visit.working_minutes,Visit.consultant_id,Visit.co_consultant_id,Visit.subject_expert_id,Visit.created_by).where(
        Visit.status.in_(("SUBMITTED","REVIEWED","APPROVED")),Visit.deleted_at.is_(None)
    )
    if job_card_id is not None: query=query.where(Visit.job_card_id==job_card_id)
    if milestone_id is not None: query=query.where(Visit.milestone_id==milestone_id)
    total=0
    for minutes,lead,co,expert,created_by in db.execute(query):
        team={person_id for person_id in (lead,co,expert) if person_id is not None}
        if consultant_id is not None:
            if consultant_id in team or (not team and created_by==consultant_id): total+=int(minutes or 0)
        else:
            total+=int(minutes or 0)*max(len(team),1)
    return total

def planner_actual_minutes(db:Session,card:JobCard,consultant_id:int|None=None,milestone_id:int|None=None)->int:
    query=(
        select(PlannedVisit.consultant_id,PlannedVisit.co_consultant_id,PlannedVisit.subject_expert_id,PlannedVisit.actual_date,PlannedVisit.actual_start_time,PlannedVisit.actual_duration_minutes)
        .where(
            PlannedVisit.job_card_id==card.id,
            PlannedVisit.status=="COMPLETED",
            ~_planner_visit_is_reported(),
        )
    )
    if consultant_id is not None:
        query=query.where(or_(PlannedVisit.consultant_id==consultant_id,PlannedVisit.co_consultant_id==consultant_id,PlannedVisit.subject_expert_id==consultant_id))
    if milestone_id is not None:
        query=query.where(PlannedVisit.milestone_id==milestone_id)
    rows=db.execute(query).all()
    intervals={}
    untimed_minutes=0
    for lead_id,co_id,expert_id,actual_date,start,duration in rows:
        if not duration:
            continue
        team={person_id for person_id in (lead_id,co_id,expert_id) if person_id is not None}
        if consultant_id is not None: team={consultant_id} if consultant_id in team else set()
        for assigned_id in team or ({lead_id} if consultant_id is None else set()):
            if actual_date is None or start is None:
                untimed_minutes+=duration
                continue
            start_minute=start.hour*60+start.minute
            intervals.setdefault((assigned_id,actual_date),[]).append((start_minute,start_minute+duration))
    total=untimed_minutes
    for bookings in intervals.values():
        bookings.sort()
        merged_start=merged_end=None
        for start,end in bookings:
            if merged_end is None:
                merged_start,merged_end=start,end
            elif start<=merged_end:
                merged_end=max(merged_end,end)
            else:
                total+=merged_end-merged_start
                merged_start,merged_end=start,end
        if merged_end is not None:
            total+=merged_end-merged_start
    return total

def manday_summary(db:Session, card:JobCard):
    visit_minutes=visit_effort_minutes(db,job_card_id=card.id)
    standalone_activity_minutes=int(db.scalar(select(func.coalesce(func.sum(JobActivity.duration_minutes),0)).where(JobActivity.job_card_id==card.id,JobActivity.status=="COMPLETED",JobActivity.visit_id.is_(None))) or 0)
    planner_minutes=planner_actual_minutes(db,card)
    completed=visit_minutes+standalone_activity_minutes+planner_minutes
    minutes_per_manday=Decimal(str(card.hours_per_manday or 8))*60
    required=Decimal(str(card.required_md))*minutes_per_manday
    progress=(Decimal(completed)*100/required).quantize(Decimal(".01"),ROUND_HALF_UP) if required else Decimal(0)
    remaining=max(required-Decimal(completed),Decimal(0))
    return {"completed_minutes":completed,"completed_hours":(Decimal(completed)/60).quantize(Decimal(".01")),"completed_md":(Decimal(completed)/minutes_per_manday).quantize(MD_PRECISION,ROUND_HALF_UP),"remaining_minutes":remaining,"remaining_hours":(remaining/60).quantize(Decimal(".01")),"remaining_md":(remaining/minutes_per_manday).quantize(MD_PRECISION,ROUND_HALF_UP),"progress":min(progress,Decimal(100)),"overrun":completed>required}

def milestone_actual_minutes(db:Session,milestone:Milestone,card:JobCard|None=None)->int:
    card=card or db.get(JobCard,milestone.job_card_id)
    visit_minutes=visit_effort_minutes(db,milestone_id=milestone.id)
    activity_minutes=int(db.scalar(select(func.coalesce(func.sum(JobActivity.duration_minutes),0)).where(JobActivity.milestone_id==milestone.id,JobActivity.status=="COMPLETED",JobActivity.visit_id.is_(None))) or 0)
    planner_minutes=planner_actual_minutes(db,card,milestone_id=milestone.id) if card else 0
    return visit_minutes+activity_minutes+planner_minutes
def milestone_summary(db:Session, milestone:Milestone):
    recorded=int(db.scalar(select(func.count(Visit.id)).outerjoin(PlannedVisit,PlannedVisit.id==Visit.planned_visit_id).where(Visit.milestone_id==milestone.id,Visit.status.in_(("SUBMITTED","REVIEWED","APPROVED")),Visit.deleted_at.is_(None),or_(Visit.planned_visit_id.is_(None),PlannedVisit.status=="COMPLETED"))) or 0)
    planned=int(db.scalar(select(func.count(PlannedVisit.id)).where(PlannedVisit.milestone_id==milestone.id,PlannedVisit.status=="COMPLETED",~_planner_visit_is_reported())) or 0)
    actual=recorded+planned
    progress=(Decimal(actual)*100/Decimal(milestone.planned_visits)).quantize(Decimal(".01")) if milestone.planned_visits else Decimal(0)
    card=db.get(JobCard,milestone.job_card_id)
    client_payment=db.scalar(select(ClientMilestonePayment).where(ClientMilestonePayment.milestone_id==milestone.id)) if card and card.payment_gate_enabled else None
    payment_status="PAID" if client_payment or not (card and card.payment_gate_enabled) else "PAYMENT_PENDING"
    latest=db.scalar(select(MilestoneReviewHistory).where(MilestoneReviewHistory.milestone_id==milestone.id).order_by(MilestoneReviewHistory.cycle.desc(),MilestoneReviewHistory.id.desc()).limit(1))
    review_status="NOT_SUBMITTED";review_cycle=0;review_remarks=None
    if latest:
        review_cycle=latest.cycle
        events=list(db.scalars(select(MilestoneReviewHistory).where(MilestoneReviewHistory.milestone_id==milestone.id,MilestoneReviewHistory.cycle==latest.cycle).order_by(MilestoneReviewHistory.id)))
        stages={event.stage:event for event in events}
        # Management's decision completes milestone review. Legacy UNIT events are
        # retained for audit history but no longer affect the milestone status.
        decision=stages.get("MANAGEMENT") or latest
        review_status=("MANAGEMENT_APPROVED" if decision.stage=="MANAGEMENT" and decision.decision=="APPROVED" else "ADMIN_APPROVED" if decision.stage=="ADMIN" and decision.decision=="APPROVED" else decision.decision)
        review_remarks=decision.comment
    gated=card and card.payment_gate_enabled and payment_status!="PAID"
    if gated: status="LOCKED"
    elif actual<milestone.planned_visits: status="IN_PROGRESS" if actual else ("READY" if card and card.payment_gate_enabled else "NOT_STARTED")
    elif review_status in ("APPROVED","MANAGEMENT_APPROVED"): status="COMPLETED"
    elif review_status in ("RETURNED","REJECTED"): status=review_status
    elif review_status in ("SUBMITTED","ADMIN_APPROVED"): status="UNDER_REVIEW"
    else: status="READY_FOR_REVIEW"
    return {"actual_visits":actual,"progress":min(progress,Decimal(100)),"status":status,"payment_status":payment_status,"review_status":review_status,"review_cycle":review_cycle,"review_remarks":review_remarks}
def card_dashboard_row(db:Session, card:JobCard):
    summary=manday_summary(db,card)
    progress=summary["progress"]
    delayed=card.project_end_date < __import__("datetime").date.today() and card.status not in ("COMPLETED","CANCELLED")
    return {"id":card.id,"number":card.number,"msme_id":card.msme_id,"status":"DELAYED" if delayed else card.status,"completed_md":summary["completed_md"],"remaining_md":summary["remaining_md"],"overrun":summary["overrun"],"progress":progress,"delay_days":max((__import__("datetime").date.today()-card.project_end_date).days,0) if delayed else 0}
