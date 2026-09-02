import csv
from decimal import Decimal
from io import BytesIO, StringIO
from zipfile import ZIP_DEFLATED, ZipFile
from xml.sax.saxutils import escape
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from .models import Assignment, JobCard, Milestone, MSME, User, Visit
from .services import MD_PRECISION, card_dashboard_row, manday_summary, milestone_summary

def project_rows(db:Session):
    rows=[]
    for card in db.scalars(select(JobCard).order_by(JobCard.number)):
        msme=db.get(MSME,card.msme_id); lead=db.scalar(select(User.full_name).join(Assignment,Assignment.user_id==User.id).where(Assignment.job_card_id==card.id,Assignment.assignment_type=="LEAD_CONSULTANT",Assignment.active==True))
        data=card_dashboard_row(db,card); rows.append({"Job Card":card.number,"MSME":msme.name,"Consultant":lead or "","Status":data["status"],"Required MD":str(card.required_md),"Completed MD":str(data["completed_md"]),"Remaining MD":str(data["remaining_md"]),"Progress %":str(data["progress"]),"Delay Days":data["delay_days"],"Overrun":data["overrun"]})
    return rows
def consultant_rows(db:Session):
    result=[]
    for user in db.scalars(select(User).where(User.role=="END_USER").order_by(User.full_name)):
        cards=list(db.scalars(select(JobCard).join(Assignment,Assignment.job_card_id==JobCard.id).where(Assignment.user_id==user.id,Assignment.active==True)))
        visits=int(db.scalar(select(func.count(Visit.id)).where(Visit.created_by==user.id,Visit.status=="SUBMITTED")) or 0)
        minutes=int(db.scalar(select(func.coalesce(func.sum(Visit.working_minutes),0)).where(Visit.created_by==user.id,Visit.status=="SUBMITTED")) or 0)
        result.append({"Consultant":user.full_name,"Assigned Projects":len(cards),"Completed Projects":sum(c.status=="COMPLETED" for c in cards),"Visits":visits,"Working Hours":f"{minutes/60:.2f}","Mandays":f"{(Decimal(minutes)/480).quantize(MD_PRECISION):.4f}"})
    return result
def milestone_rows(db:Session):
    result=[]
    for m in db.scalars(select(Milestone).order_by(Milestone.job_card_id,Milestone.code)):
        s=milestone_summary(db,m); card=db.get(JobCard,m.job_card_id)
        result.append({"Job Card":card.number,"Milestone":m.code,"Name":m.name,"Planned Visits":m.planned_visits,"Completed Visits":s["actual_visits"],"Progress %":str(s["progress"]),"Status":s["status"]})
    return result
def to_csv(items:list[dict]):
    fields=list(items[0]) if items else []
    out=StringIO(); writer=csv.DictWriter(out,fieldnames=fields); writer.writeheader(); writer.writerows(items); return out.getvalue().encode()
def to_xlsx(items:list[dict]):
    fields=list(items[0]) if items else ["No data"]
    def row(values): return "<row>"+"".join(f'<c t="inlineStr"><is><t>{escape(str(v))}</t></is></c>' for v in values)+"</row>"
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
