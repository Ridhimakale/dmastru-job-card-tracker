"""Idempotent development seed data. Never creates demo credentials in production."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from sqlalchemy import select
from .core import AccountStatus, ENV, Role, passwords
from .database import SessionLocal
from .events import audit
from .models import ActivityType, Approval, Assignment, AuthSession, Department, Enquiry, EnquiryFollowUp, JobActivity, JobCard, Milestone, MilestoneReviewHistory, MSME, Payment, PaymentReceipt, ProjectClosure, Ticket, TicketRelationship, User, Visit, WorkflowAction
from .business_numbers import next_business_number
from .reports import to_pdf
from .storage import save as save_file

DEFAULTS=(
    ("System Admin","admin@dmastru.local",Role.ADMIN,"Admin@123"),
    ("Management Demo","management@dmastru.local",Role.MANAGEMENT,"Manage@123"),
    ("Consultant Demo","consultant@dmastru.local",Role.END_USER,"Consult@123"),
    ("Unit Representative Demo","unit.rep@dmastru.local",Role.UNIT_REPRESENTATIVE,"UnitRep@123"),
    ("Director Demo","director@dmastru.local",Role.DIRECTOR,"Director@123"),
    ("Marketing Demo","marketing@dmastru.local",Role.MARKETING,"Marketing@123"),
)
DEPARTMENTS=(("MARKETING","Marketing"),("CONSULTING","Consulting"),("REVIEW","Review"),("UNIT_REPRESENTATIVE","Unit Representative"),("MANAGEMENT","Management"),("DIRECTOR","Director"),("ADMIN","Administration"),("OPERATIONS","Operations"),("FINANCE","Finance"))
ACTIVITY_TYPES=(("CLIENT_MEETING","Client meeting"),("DATA_COLLECTION","Data collection"),("ANALYSIS","Analysis"),("IMPLEMENTATION","Implementation"),("DOCUMENTATION","Documentation"),("OTHER","Other"))
def seed():
    if ENV == "production":
        return
    db=SessionLocal()
    try:
        for code,name in DEPARTMENTS:
            if not db.scalar(select(Department).where(Department.code==code)):
                db.add(Department(code=code,name=name,active=True))
        for code,name in ACTIVITY_TYPES:
            if not db.scalar(select(ActivityType).where(ActivityType.code==code)):
                db.add(ActivityType(code=code,name=name,active=True))
        db.flush()
        for full_name,email,role,password in DEFAULTS:
            if not db.scalar(select(User).where(User.email==email)):
                dept=db.scalar(select(Department).where(Department.code=="MARKETING")) if role==Role.MARKETING else None
                db.add(User(full_name=full_name,email=email,role=role,department_id=dept.id if dept else None,status=AccountStatus.ACTIVE,password_hash=passwords.hash(password)))
        marketing=db.scalar(select(User).where(User.email=="marketing@dmastru.local"))
        if marketing and not marketing.department_id:
            marketing.department_id=db.scalar(select(Department).where(Department.code=="MARKETING")).id
        db.commit()
        if ENV=="development": seed_demo_workflow(db)
    finally: db.close()

def disable_demo_accounts():
    """Retire predictable seed identities left behind by earlier deployments."""
    db=SessionLocal()
    try:
        for _,email,_,_ in DEFAULTS:
            user=db.scalar(select(User).where(User.email==email))
            if not user or user.status==AccountStatus.INACTIVE: continue
            previous=user.status;user.status=AccountStatus.INACTIVE
            for session in db.scalars(select(AuthSession).where(AuthSession.user_id==user.id)): session.revoked=True
            audit(db,None,"DEMO_ACCOUNT_DISABLED","USER",user.id,f"{previous.value}->INACTIVE during production startup")
        db.commit()
    finally: db.close()

def seed_demo_workflow(db):
    admin=db.scalar(select(User).where(User.email=="admin@dmastru.local"))
    consultant=db.scalar(select(User).where(User.email=="consultant@dmastru.local"))
    unit_rep=db.scalar(select(User).where(User.email=="unit.rep@dmastru.local"))
    director=db.scalar(select(User).where(User.email=="director@dmastru.local"))
    if not all((admin,consultant,unit_rep,director)): return
    existing=db.scalar(select(JobCard).where(JobCard.number=="DEMO-JC-001"))
    if existing:
        demo_m1=db.scalar(select(Milestone).where(Milestone.job_card_id==existing.id,Milestone.code=="M1"))
        ensure_demo_payment(db,existing,None,Decimal("10000.00"),"DEMO-PROJECT")
        if demo_m1: ensure_demo_payment(db,existing,demo_m1,Decimal("5000.00"),"DEMO-M1")
        seed_demo_scenarios(db,existing,admin,consultant,unit_rep,director)
        db.commit();return
    msme=db.scalar(select(MSME).where(MSME.msme_code=="DEMO-001"))
    if not msme:
        msme=MSME(name="Demo Manufacturing Ltd",msme_code="DEMO-001",zed_id="ZED-DEMO-001",certificate_level="Bronze",city="Pune",state="Maharashtra",status="ACTIVE")
        db.add(msme);db.flush()
    today=date.today();start=today-timedelta(days=12);end=today+timedelta(days=45)
    card=JobCard(number="DEMO-JC-001",msme_id=msme.id,location="Pune",project_start_date=start,project_end_date=end,required_md=Decimal("14"),hours_per_manday=Decimal("8"),required_hours=Decimal("112"),unit_representative_id=unit_rep.id,director_id=director.id,status="IN_PROGRESS",created_by=admin.id,payment_gate_enabled=True)
    db.add(card);db.flush()
    assignments=[(consultant,"LEAD_CONSULTANT"),(unit_rep,"UNIT_REPRESENTATIVE"),(director,"DIRECTOR")]
    for user,kind in assignments: db.add(Assignment(job_card_id=card.id,user_id=user.id,assignment_type=kind,assigned_by=admin.id))
    milestones=[]
    for code,name,planned in (("M1","Initial Assessment",2),("M2","Implementation Support",2),("M3","Final Review",2)):
        milestone=Milestone(job_card_id=card.id,code=code,name=name,planned_visits=planned,start_date=start,closing_date=end,status="NOT_STARTED")
        db.add(milestone);db.flush();milestones.append(milestone)
    ensure_demo_payment(db,card,None,Decimal("10000.00"),"DEMO-PROJECT")
    ensure_demo_payment(db,card,milestones[0],Decimal("5000.00"),"DEMO-M1")
    for number,(milestone,work) in enumerate(((milestones[0],"Baseline assessment and site review"),(milestones[0],"Process mapping and action plan"),(milestones[1],"Implementation support"),(milestones[1],"Progress verification")),start=1):
        db.add(Visit(job_card_id=card.id,milestone_id=milestone.id,visit_number=number,visit_date=start+timedelta(days=number),working_minutes=600,work_details=work,rating_code="A",rating_description="Excellent",status="SUBMITTED",created_by=consultant.id))
    db.flush();seed_demo_scenarios(db,card,admin,consultant,unit_rep,director)
    db.commit()

def seed_demo_scenarios(db,active_card,admin,consultant,unit_rep,director):
    msme=db.get(MSME,active_card.msme_id);today=date.today();start=today-timedelta(days=50);end=today-timedelta(days=5)
    def assign(card):
        for user,kind in ((consultant,"LEAD_CONSULTANT"),(unit_rep,"UNIT_REPRESENTATIVE"),(director,"DIRECTOR")):
            if not db.scalar(select(Assignment.id).where(Assignment.job_card_id==card.id,Assignment.user_id==user.id,Assignment.assignment_type==kind,Assignment.active.is_(True))):
                db.add(Assignment(job_card_id=card.id,user_id=user.id,assignment_type=kind,assigned_by=admin.id))
    waiting=db.scalar(select(JobCard).where(JobCard.project_name=="DEMO-AWAITING-PAYMENT"))
    if not waiting:
        waiting=JobCard(number=next_business_number(db,"job_card"),msme_id=msme.id,project_name="DEMO-AWAITING-PAYMENT",location=active_card.location,project_start_date=today,project_end_date=today+timedelta(days=60),required_md=Decimal("6"),hours_per_manday=Decimal("8"),required_hours=Decimal("48"),unit_representative_id=unit_rep.id,director_id=director.id,status="AWAITING_PAYMENT",created_by=admin.id,payment_gate_enabled=True)
        db.add(waiting);db.flush();assign(waiting);db.add(Milestone(job_card_id=waiting.id,code="M1",name="Initial assessment",planned_visits=2,planned_mandays=Decimal("2"),amount=Decimal("3000"),status="LOCKED"))
    completed=db.scalar(select(JobCard).where(JobCard.project_name=="DEMO-COMPLETED"))
    if not completed:
        ticket=Ticket(number=next_business_number(db,"ticket"),title="Completed project enquiry",description="Seeded end-to-end demo business chain.",ticket_type="NEW_ENQUIRY",priority="NORMAL",created_by=admin.id,status="COMPLETED",completed_at=datetime.now(timezone.utc))
        db.add(ticket);db.flush()
        marketing=db.scalar(select(User).where(User.email=="marketing@dmastru.local"))
        enquiry=Enquiry(number=next_business_number(db,"enquiry"),source_ticket_id=ticket.id,msme_id=msme.id,company_name=msme.name,contact_person=msme.contact_person,email="demo@example.com",phone="+910000000000",industry="Manufacturing",location=active_card.location,requirement="Operational improvement and quality management.",enquiry_date=start,source="DEMO",expected_scope="Assessment, implementation and final review.",assigned_marketing_user_id=marketing.id if marketing else None,status="CONVERTED",created_by=admin.id)
        db.add(enquiry);db.flush();ticket.enquiry_id=enquiry.id
        db.add(EnquiryFollowUp(enquiry_id=enquiry.id,follow_up_date=datetime.combine(start,datetime.min.time(),tzinfo=timezone.utc),follow_up_type="MEETING",person_contacted=msme.contact_person,notes="Initial consultation completed.",responsible_user_id=marketing.id if marketing else admin.id,status="COMPLETED"))
        completed=JobCard(number=next_business_number(db,"job_card"),source_ticket_id=ticket.id,source_enquiry_id=enquiry.id,msme_id=msme.id,project_name="DEMO-COMPLETED",package_code="GOLD",scope="Assessment, implementation and closure demonstration.",commercial_amount=Decimal("14500"),project_payment_percent=Decimal("30"),location=active_card.location,project_start_date=start,project_end_date=end,required_md=Decimal("4"),hours_per_manday=Decimal("8"),required_hours=Decimal("32"),unit_representative_id=unit_rep.id,director_id=director.id,status="COMPLETED",created_by=admin.id,payment_gate_enabled=True)
        db.add(completed);db.flush();ticket.job_card_id=completed.id
        db.add(TicketRelationship(ticket_id=ticket.id,related_job_card_id=completed.id,relationship_type="CREATED_FROM",created_by=admin.id));assign(completed)
        project_payment=Payment(invoice_number=next_business_number(db,"invoice"),job_card_id=completed.id,description="Demo project activation",amount_due=Decimal("10000"),amount_paid=Decimal("10000"),currency="INR",status="PAID",invoice_date=start,created_by=admin.id)
        db.add(project_payment);db.flush();db.add(PaymentReceipt(payment_id=project_payment.id,amount=Decimal("10000"),external_reference=f"DEMO-{completed.id}-PROJECT",confirmed_by=admin.id,remarks="Development demo payment"))
        activity_type=db.scalar(select(ActivityType).where(ActivityType.code=="ANALYSIS"))
        for index,(code,name,work) in enumerate((("M1","Assessment","Baseline assessment and findings"),("M2","Implementation","Process changes implemented"),("M3","Final review","Outcomes verified and submitted")),start=1):
            milestone=Milestone(job_card_id=completed.id,code=code,name=name,description=work,planned_visits=1,planned_mandays=Decimal("1"),amount=Decimal("1500"),payment_percent=Decimal("100"),start_date=start,closing_date=end,status="COMPLETED")
            db.add(milestone);db.flush()
            payment=Payment(invoice_number=next_business_number(db,"invoice"),job_card_id=completed.id,milestone_id=milestone.id,description=f"Demo {code} payment",amount_due=Decimal("1500"),amount_paid=Decimal("1500"),currency="INR",status="PAID",invoice_date=start,created_by=admin.id)
            db.add(payment);db.flush();db.add(PaymentReceipt(payment_id=payment.id,amount=Decimal("1500"),external_reference=f"DEMO-{completed.id}-{code}",confirmed_by=admin.id,remarks="Development demo payment"))
            visit=Visit(job_card_id=completed.id,milestone_id=milestone.id,visit_number=index,visit_date=start+timedelta(days=index),working_minutes=480,work_details=work,findings=f"{code} deliverables verified.",rating_code="A",rating_description="Excellent",status="APPROVED",created_by=consultant.id,consultant_id=consultant.id)
            db.add(visit);db.flush()
            for stage,decision,actor,comment in (("CONSULTANT","SUBMITTED",consultant,"Milestone delivered."),("MANAGEMENT","APPROVED",admin,"Management review complete."),("UNIT","APPROVED",unit_rep,"Unit Representative confirmed.")):
                db.add(MilestoneReviewHistory(milestone_id=milestone.id,cycle=1,stage=stage,decision=decision,comment=comment,decided_by=actor.id))
            if activity_type: db.add(JobActivity(job_card_id=completed.id,milestone_id=milestone.id,activity_type_id=activity_type.id,name=f"{code} delivery activity",description=work,assigned_user_id=consultant.id,start_date=start+timedelta(days=index),end_date=start+timedelta(days=index),duration_minutes=60,status="COMPLETED",created_by=consultant.id))
        for stage,actor in (("CONSULTANT_CONFIRMATION",consultant),("UNIT_REPRESENTATIVE",unit_rep),("DIRECTOR_REVIEW",director)):
            db.add(Approval(job_card_id=completed.id,stage=stage,cycle=1,decision="APPROVED",comment="Development demo approval.",decided_by=actor.id))
        closure=ProjectClosure(job_card_id=completed.id,client_confirmation="The demo client confirmed completion.",confirmed_at=datetime.now(timezone.utc),problem="Inconsistent production processes.",requirement="Improve operational consistency.",solution="Standardized process and action plan.",implementation="Assessment, implementation and verification.",benefits="Improved visibility and repeatability.",results="Target process adopted.",feedback_rating=5,feedback="Demo feedback: project objectives achieved.",testimonial="A useful engagement.",publication_permission=False,prepared_by=admin.id,closed_at=datetime.now(timezone.utc))
        closure.certificate_number=next_business_number(db,"certificate")
        client=db.get(MSME,completed.msme_id)
        certificate=to_pdf("D'Mastru Project Completion Certificate",[{"Certificate":closure.certificate_number,"Client":client.name,"Project":completed.project_name,"Job Card":completed.number,"Package":completed.package_code,"Project Period":f"{completed.project_start_date} to {completed.project_end_date}","Completion Date":date.today(),"Authorized Signatory":"D'Mastru Authorized Signatory"}])
        closure.certificate_key=save_file(f"{closure.certificate_number}.pdf","application/pdf",certificate);db.add(closure)
    if not db.scalar(select(WorkflowAction.id).where(WorkflowAction.template_code=="DEMO_RETURNED_ACTION")):
        department=db.scalar(select(Department).where(Department.code=="CONSULTING"))
        if department:
            db.add(WorkflowAction(action_number=next_business_number(db,"action"),template_code="DEMO_RETURNED_ACTION",workflow_step=1,job_card_id=active_card.id,milestone_id=db.scalar(select(Milestone.id).where(Milestone.job_card_id==active_card.id,Milestone.code=="M2")),action_type="CORRECTION",title="Clarify implementation notes",description="Example of work returned with review feedback.",department_id=department.id,assigned_role="END_USER",assigned_user_id=consultant.id,priority="NORMAL",status="RETURNED",created_by=admin.id,remarks="Please add evidence for the implementation recommendation."))

def ensure_demo_payment(db,card,milestone,amount,reference):
    existing=db.scalar(select(Payment).where(Payment.job_card_id==card.id,Payment.milestone_id==(milestone.id if milestone else None)))
    if existing:return
    today=date.today();payment=Payment(invoice_number=next_business_number(db,"invoice"),job_card_id=card.id,milestone_id=milestone.id if milestone else None,description=f"Demo {'milestone' if milestone else 'project'} invoice",amount_due=amount,amount_paid=amount,currency="INR",status="PAID",invoice_date=today,created_by=card.created_by)
    db.add(payment);db.flush();db.add(PaymentReceipt(payment_id=payment.id,amount=amount,external_reference=reference,confirmed_by=card.created_by,remarks="Development demo record"))
if __name__=="__main__": seed()
