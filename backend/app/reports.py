import csv
from datetime import date
from decimal import Decimal
from io import BytesIO, StringIO
from zipfile import ZIP_DEFLATED, ZipFile
from xml.sax.saxutils import escape
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, aliased
from .models import Approval, Assignment, Document, JobActivity, JobCard, Milestone, MilestoneConsultantAllocation, MSME, Payment, PlannedVisit, RecordDocument, User, Visit, WorkflowAction
from .services import MD_PRECISION, _planner_visit_is_reported, card_dashboard_row, manday_summary, milestone_actual_minutes, milestone_summary

def project_rows(db:Session):
    rows=[]
    for card in db.scalars(select(JobCard).order_by(JobCard.number)):
        msme=db.get(MSME,card.msme_id); lead=db.scalar(select(User.full_name).join(Assignment,Assignment.user_id==User.id).where(Assignment.job_card_id==card.id,Assignment.assignment_type=="LEAD_CONSULTANT",Assignment.active==True))
        lead_id=db.scalar(select(Assignment.user_id).where(Assignment.job_card_id==card.id,Assignment.assignment_type=="LEAD_CONSULTANT",Assignment.active==True));data=card_dashboard_row(db,card); md=manday_summary(db,card); rows.append({"ID":card.id,"Job Card ID":card.id,"Job Card":card.number,"MSME ID":msme.id,"MSME":msme.name,"Consultant ID":lead_id or "","Location":card.location,"Consultant":lead or "","Start Date":str(card.project_start_date),"End Date":str(card.project_end_date),"Status":data["status"],"Project Status":data["status"],"Required MD":str(card.required_md),"Planned MD":str(card.required_md),"Actual MD":str(md["completed_md"]),"Variance MD":str(Decimal(str(md["completed_md"]))-Decimal(str(card.required_md))),"Completed MD":str(data["completed_md"]),"Remaining MD":str(data["remaining_md"]),"Progress %":str(data["progress"]),"Delay Days":data["delay_days"],"Overrun":data["overrun"]})
    return rows
def consultant_rows(db:Session):
    result=[]
    for user in db.scalars(select(User).where(User.role=="END_USER").order_by(User.full_name)):
        cards=list(db.scalars(select(JobCard).join(Milestone,Milestone.job_card_id==JobCard.id).join(MilestoneConsultantAllocation,MilestoneConsultantAllocation.milestone_id==Milestone.id).where(MilestoneConsultantAllocation.consultant_id==user.id,MilestoneConsultantAllocation.active.is_(True)).distinct()))
        participating=or_(Visit.consultant_id==user.id,Visit.co_consultant_id==user.id,Visit.subject_expert_id==user.id,((Visit.consultant_id.is_(None))&(Visit.co_consultant_id.is_(None))&(Visit.subject_expert_id.is_(None))&(Visit.created_by==user.id)))
        visit_rows=db.execute(select(Visit.job_card_id,Visit.working_minutes,JobCard.hours_per_manday).join(JobCard,JobCard.id==Visit.job_card_id).where(participating,Visit.status.in_(("SUBMITTED","REVIEWED","APPROVED")),Visit.deleted_at.is_(None))).all()
        activity_rows=db.execute(select(JobActivity.duration_minutes,JobCard.hours_per_manday).join(JobCard,JobCard.id==JobActivity.job_card_id).where(JobActivity.assigned_user_id==user.id,JobActivity.status=="COMPLETED",JobActivity.visit_id.is_(None))).all()
        planner_rows=db.execute(select(PlannedVisit.job_card_id,PlannedVisit.actual_date,PlannedVisit.actual_duration_minutes,JobCard.hours_per_manday).join(JobCard,JobCard.id==PlannedVisit.job_card_id).where(or_(PlannedVisit.consultant_id==user.id,PlannedVisit.co_consultant_id==user.id,PlannedVisit.subject_expert_id==user.id),PlannedVisit.status=="COMPLETED",~_planner_visit_is_reported())).all()
        visits=len(visit_rows); minutes=sum(int(row[1]) for row in visit_rows)
        mandays=sum((Decimal(int(row[1]))/(Decimal(str(row[2] or 8))*60) for row in visit_rows),Decimal(0))
        mandays+=sum((Decimal(int(row[0]))/(Decimal(str(row[1] or 8))*60) for row in activity_rows),Decimal(0))
        mandays+=sum((Decimal(int(row[2] or 0))/(Decimal(str(row[3] or 8))*60) for row in planner_rows),Decimal(0))
        assigned_rows=db.execute(select(Milestone,MilestoneConsultantAllocation.allocated_mandays).join(JobCard,JobCard.id==Milestone.job_card_id).join(MilestoneConsultantAllocation,MilestoneConsultantAllocation.milestone_id==Milestone.id).where(MilestoneConsultantAllocation.consultant_id==user.id,MilestoneConsultantAllocation.active.is_(True),Milestone.status.not_in(("COMPLETED","CANCELLED")),JobCard.deleted_at.is_(None))).all()
        assigned_milestones=[row[0] for row in assigned_rows]
        planned=sum((Decimal(str(row[1] or 0)) for row in assigned_rows),Decimal(0));util=(mandays*100/planned).quantize(Decimal(".01")) if planned else Decimal(0);today=date.today();active_allocation=any(milestone.start_date is not None and milestone.start_date<=today and (milestone.closing_date is None or today<=milestone.closing_date) for milestone in assigned_milestones);progress=sum((Decimal(str(card_dashboard_row(db,card)["progress"])) for card in cards),Decimal(0))/Decimal(len(cards)) if cards else Decimal(0)
        result.append({"Consultant ID":user.id,"Consultant":user.full_name,"Assigned Projects":len(cards),"Completed Projects":sum(c.status=="COMPLETED" for c in cards),"Average Project Progress %":str(progress.quantize(Decimal(".01"))),"Visits":visits,"Working Hours":f"{minutes/60:.2f}","Planned MD":str(planned),"Actual MD":f"{mandays.quantize(MD_PRECISION):.4f}","Remaining MD":str(max(planned-mandays,Decimal(0)).quantize(MD_PRECISION)),"Utilization %":str(util),"Available":not active_allocation,"Mandays":f"{mandays.quantize(MD_PRECISION):.4f}"})
    return result
def milestone_rows(db:Session):
    result=[]
    for m in db.scalars(select(Milestone).order_by(Milestone.job_card_id,Milestone.code)):
        s=milestone_summary(db,m); card=db.get(JobCard,m.job_card_id)
        msme=db.get(MSME,card.msme_id);lead=m.responsible_consultant_id;actual_minutes=milestone_actual_minutes(db,m,card);actual_md=(Decimal(actual_minutes)/(Decimal(str(card.hours_per_manday or 8))*60)).quantize(MD_PRECISION);planned_md=Decimal(str(m.planned_mandays or 0));
        result.append({"Start Date":str(m.start_date or ""),"Job Card ID":card.id,"Job Card":card.number,"MSME ID":msme.id if msme else "","MSME":msme.name if msme else "","Consultant ID":lead or "","Milestone ID":m.id,"Milestone":m.code,"Name":m.name,"Project Status":card_dashboard_row(db,card)["status"],"Planned MD":str(planned_md),"Actual MD":str(actual_md),"Remaining MD":str(max(planned_md-actual_md,Decimal(0)).quantize(MD_PRECISION)),"Variance MD":str((actual_md-planned_md).quantize(MD_PRECISION)),"Planned Visits":m.planned_visits,"Completed Visits":s["actual_visits"],"Progress %":str(s["progress"]),"Closing Date":str(m.closing_date or ""),"Payment Date":str(m.payment_date or ""),"Portal Report Submission Date":str(m.portal_report_submission_date or ""),"Status":s["status"]})
    return result
def visit_rows(db:Session):
    result=[]
    lead=aliased(User); co=aliased(User); expert=aliased(User)
    rows=db.execute(select(Visit,JobCard,MSME,User,lead,co,expert).join(JobCard,JobCard.id==Visit.job_card_id).join(MSME,MSME.id==JobCard.msme_id).join(User,User.id==Visit.created_by).outerjoin(lead,lead.id==Visit.consultant_id).outerjoin(co,co.id==Visit.co_consultant_id).outerjoin(expert,expert.id==Visit.subject_expert_id).where(Visit.deleted_at==None,JobCard.deleted_at==None).order_by(Visit.visit_date.desc()))
    for visit,card,msme,user,lead_user,co_user,expert_user in rows:
        team={person.id for person in (lead_user,co_user,expert_user) if person is not None}
        mandays=Decimal(visit.working_minutes*max(len(team),1))/(Decimal(str(card.hours_per_manday or 8))*60)
        result.append({"Date":str(visit.visit_date),"Job Card ID":card.id,"Job Card":card.number,"MSME ID":msme.id,"MSME":msme.name,"Consultant ID":visit.consultant_id or "","Recorded By":user.full_name,"Consultant":lead_user.full_name if lead_user else "","Co-Consultant":co_user.full_name if co_user else "","Subject Expert":expert_user.full_name if expert_user else "","Visit":visit.visit_number,"Hours":f"{visit.working_minutes/60:.2f}","Mandays":f"{mandays.quantize(MD_PRECISION):.4f}","Visit Type":"REPORTED","Status":visit.status,"Rating":visit.rating_code,"Work Details":visit.work_details})
    for planned,card,msme,consultant in db.execute(select(PlannedVisit,JobCard,MSME,User).join(JobCard,JobCard.id==PlannedVisit.job_card_id).join(MSME,MSME.id==JobCard.msme_id).join(User,User.id==PlannedVisit.consultant_id).where(JobCard.deleted_at.is_(None),PlannedVisit.status.in_(("PLANNED","COMPLETED"))).order_by(PlannedVisit.visit_date.desc())):
        minutes=planned.actual_duration_minutes if planned.status=="COMPLETED" else planned.expected_duration_minutes
        mandays=Decimal(minutes or 0)/(Decimal(str(card.hours_per_manday or 8))*60)
        result.append({"Date":str(planned.actual_date or planned.visit_date),"Job Card ID":card.id,"Job Card":card.number,"MSME ID":msme.id,"MSME":msme.name,"Consultant ID":consultant.id,"Recorded By":consultant.full_name,"Consultant":consultant.full_name,"Co-Consultant":"","Subject Expert":"","Visit":planned.id,"Hours":f"{Decimal(minutes or 0)/60:.2f}","Mandays":f"{mandays.quantize(MD_PRECISION):.4f}","Visit Type":"PLANNER","Status":planned.status,"Rating":"","Work Details":planned.outcome or planned.objective})
    return result
def document_rows(db:Session):
    result=[]
    rows=db.execute(select(Document,JobCard,MSME,User).join(JobCard,JobCard.id==Document.job_card_id).join(MSME,MSME.id==JobCard.msme_id).join(User,User.id==Document.uploaded_by).where(Document.deleted_at==None,JobCard.deleted_at==None).order_by(Document.uploaded_at.desc())).all()
    for document,card,msme,user in rows:
        result.append({"Date":document.uploaded_at.isoformat(),"Job Card ID":card.id,"Job Card":card.number,"MSME ID":msme.id,"MSME":msme.name,"Consultant ID":user.id,"Consultant":user.full_name,"Document Type":document.document_type,"File":document.file_name,"Version":document.version,"Status":document.status,"Reviewer ID":document.reviewed_by or "","Reviewed At":str(document.reviewed_at or ""),"Remarks":document.remarks or ""})
    rows=db.execute(select(RecordDocument,JobCard,MSME,User).join(JobCard,JobCard.id==RecordDocument.owner_job_card_id).join(MSME,MSME.id==JobCard.msme_id).join(User,User.id==RecordDocument.uploaded_by).where(JobCard.deleted_at.is_(None)).order_by(RecordDocument.uploaded_at.desc())).all()
    for document,card,msme,user in rows:
        result.append({"Date":document.uploaded_at.isoformat(),"Job Card ID":card.id,"Job Card":card.number,"MSME ID":msme.id,"MSME":msme.name,"Consultant ID":user.id,"Consultant":user.full_name,"Document Type":document.document_type,"File":document.file_name,"Version":document.version,"Status":document.status,"Reviewer ID":document.reviewed_by or "","Reviewed At":str(document.reviewed_at or ""),"Remarks":document.remarks or ""})
    return result

def task_rows(db:Session):
    result=[]
    actions=db.execute(select(WorkflowAction,JobCard,MSME).outerjoin(JobCard,JobCard.id==WorkflowAction.job_card_id).outerjoin(MSME,MSME.id==JobCard.msme_id).where(WorkflowAction.status.in_(("OPEN","IN_PROGRESS","WAITING","ON_HOLD","RETURNED"))).order_by(WorkflowAction.due_at)).all()
    for action,card,msme in actions:
        assigned=db.get(User,action.assigned_user_id) if action.assigned_user_id else None
        result.append({"Task ID":str(action.id),"Task":action.title,"Type":"WORKFLOW","Job Card ID":card.id if card else "","Job Card":card.number if card else "","MSME ID":msme.id if msme else "","MSME":msme.name if msme else "","Consultant ID":assigned.id if assigned else "","Consultant":assigned.full_name if assigned else "","Milestone ID":action.milestone_id or "","Due Date":str(action.due_at or ""),"Status":action.status,"Project Status":card_dashboard_row(db,card)["status"] if card else "","Overdue":bool(action.due_at and action.due_at.date()<date.today())})
    activities=db.execute(select(JobActivity,JobCard,MSME,User).join(JobCard,JobCard.id==JobActivity.job_card_id).join(MSME,MSME.id==JobCard.msme_id).join(User,User.id==JobActivity.assigned_user_id).where(JobActivity.status.in_(("PLANNED","IN_PROGRESS"))).order_by(JobActivity.end_date)).all()
    for activity,card,msme,user in activities:
        result.append({"Task ID":activity.id,"Task":activity.name,"Type":"ACTIVITY","Job Card ID":card.id,"Job Card":card.number,"MSME ID":msme.id,"MSME":msme.name,"Consultant ID":user.id,"Consultant":user.full_name,"Milestone ID":activity.milestone_id,"Due Date":str(activity.end_date or ""),"Status":activity.status,"Project Status":card_dashboard_row(db,card)["status"],"Overdue":bool(activity.end_date and activity.end_date<date.today())})
    return result
def approval_rows(db:Session):
    result=[]
    rows=db.execute(select(Approval,JobCard,User).join(JobCard,JobCard.id==Approval.job_card_id).join(User,User.id==Approval.decided_by).where(JobCard.deleted_at==None).order_by(Approval.decided_at.desc())).all()
    for approval,card,user in rows:
        result.append({"Date":approval.decided_at.isoformat(),"Job Card":card.number,"Cycle":approval.cycle,"Stage":approval.stage,"Decision":approval.decision,"Reviewer":user.full_name,"Comment":approval.comment or ""})
    return result
def payment_rows(db:Session):
    rows=[]
    for payment in db.scalars(select(Payment).order_by(Payment.invoice_date.desc(),Payment.id.desc())):
        card=db.get(JobCard,payment.job_card_id);msme=db.get(MSME,card.msme_id) if card else None
        status="OVERDUE" if payment.status in ("INVOICE_RAISED","PARTIALLY_PAID") and payment.due_date and payment.due_date<__import__("datetime").date.today() else payment.status
        rows.append({"Invoice":payment.invoice_number,"Job Card ID":card.id if card else "","Job Card":card.number if card else "","MSME ID":msme.id if msme else "","Client":msme.name if msme else "","Milestone ID":payment.milestone_id or "","Stage":"Project activation" if payment.milestone_id is None else (db.get(Milestone,payment.milestone_id).code if db.get(Milestone,payment.milestone_id) else ""),"Description":payment.description,"Currency":payment.currency,"Amount Due":str(payment.amount_due),"Amount Paid":str(payment.amount_paid),"Outstanding":str(payment.amount_due-payment.amount_paid),"Invoice Date":str(payment.invoice_date),"Due Date":str(payment.due_date or ""),"Status":status})
    return rows
def to_csv(items:list[dict]):
    fields=list(items[0]) if items else []
    out=StringIO(); writer=csv.DictWriter(out,fieldnames=fields); writer.writeheader(); writer.writerows(items); return out.getvalue().encode()
def to_xlsx(items:list[dict]):
    fields=list(items[0]) if items else ["No data"]
    def cell(value):
        text=str(value or "")
        if text.startswith("=") and len(text)>1: return f'<c><f>{escape(text[1:])}</f><v></v></c>'
        return f'<c t="inlineStr"><is><t>{escape(text)}</t></is></c>'
    def row(values): return "<row>"+"".join(cell(v) for v in values)+"</row>"
    sheet='<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'+row(fields)+''.join(row([x.get(f,"") for f in fields]) for x in items)+"</sheetData></worksheet>"
    buf=BytesIO()
    with ZipFile(buf,"w",ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        z.writestr("_rels/.rels",'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr("xl/workbook.xml",'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Report" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/worksheets/sheet1.xml",sheet)
    return buf.getvalue()
def to_pdf(title:str,items:list[dict]):
    """Small dependency-free PDF table export; adequate for operational summaries."""
    fields=list(items[0]) if items else ["No data"]
    lines=[title,"", " | ".join(fields)]
    lines.extend(" | ".join(str(row.get(field,"")) for field in fields) for row in items)
    lines=[line.encode("latin-1","replace").decode("latin-1")[:150] for line in lines]
    stream="BT /F1 10 Tf 45 760 Td 14 TL " + " ".join(f"({line.replace('\\','\\\\').replace('(','\\(').replace(')','\\)')}) Tj T*" for line in lines) + " ET"
    objects=["<< /Type /Catalog /Pages 2 0 R >>","<< /Type /Pages /Kids [3 0 R] /Count 1 >>","<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>","<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",f"<< /Length {len(stream.encode())} >>\nstream\n{stream}\nendstream"]
    data="%PDF-1.4\n"; positions=[]
    for i,obj in enumerate(objects,1): positions.append(len(data.encode())); data+=f"{i} 0 obj\n{obj}\nendobj\n"
    start=len(data.encode()); data+=f"xref\n0 {len(objects)+1}\n0000000000 65535 f \n"+"".join(f"{p:010d} 00000 n \n" for p in positions)+f"trailer << /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF"
    return data.encode()
