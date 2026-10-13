import base64
import hashlib
import os
import secrets
from datetime import datetime, time as TimeOfDay, timedelta, timezone
from datetime import date
from uuid import UUID
from decimal import Decimal
from typing import Annotated
import logging
import time
from urllib.parse import quote
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from .core import AccountStatus, ENV, REFRESH_EXPIRY_DAYS, Role, check_password_rules, decode_token, issue_token, passwords, JWT_EXPIRY_MINUTES
from .database import Base, engine, get_db
from .models import ActivityType, Approval, Assignment, AuditLog, AuthSession, BusinessSequence, Department, Document, Enquiry, EnquiryDocument, EnquiryFollowUp, JobActivity, JobActivityAttachment, JobCard, MasterData, Milestone, MilestoneReviewHistory, MSME, Notification, Payment, PaymentReceipt, PlannedVisit, ProjectClosure, RecordDocument, ServicePackage, SystemSetting, Ticket, TicketAttachment, TicketRelationship, User, Visit, VisitReviewHistory, WorkflowAction, WorkflowActionAttachment, WorkflowTemplate
from .seed import disable_demo_accounts, seed
from .services import MD_PRECISION, card_dashboard_row, manday_summary, milestone_summary
from .storage import ROOT as STORAGE_ROOT, save as save_file
from .events import audit, notify, notify_once
from .reports import approval_rows, consultant_rows, document_rows, milestone_rows, payment_rows, project_rows, task_rows, to_csv, to_pdf, to_xlsx, visit_rows
from .mail import send_password_reset
from .business_numbers import next_business_number

app=FastAPI(title="D'Mastru Job Card API",version="1.0.0")
bearer=HTTPBearer(auto_error=False)
DB=Annotated[Session,Depends(get_db)]
logger=logging.getLogger("job_card_api")
origins=[origin.strip() for origin in os.getenv("CORS_ORIGINS","http://127.0.0.1:5173,http://localhost:5173").split(",") if origin.strip()]
app.add_middleware(CORSMiddleware,allow_origins=origins,allow_credentials=False,allow_methods=["GET","POST","PATCH","PUT","DELETE"],allow_headers=["Authorization","Content-Type"],max_age=600)
RATE_WINDOW_SECONDS=int(os.getenv("RATE_LIMIT_WINDOW_SECONDS","60")); RATE_MAX_REQUESTS=int(os.getenv("RATE_LIMIT_MAX_REQUESTS","120")); LOGIN_MAX_REQUESTS=int(os.getenv("LOGIN_RATE_LIMIT_MAX_REQUESTS","10")); _rates:dict[str,tuple[float,int]]={}

@app.middleware("http")
async def hardening(request:Request, call_next):
    started=time.perf_counter()
    now=time.monotonic(); client=request.client.host if request.client else "unknown"; key=f"{client}:{'login' if request.url.path=='/api/auth/login' else 'api'}"; limit=LOGIN_MAX_REQUESTS if request.url.path=='/api/auth/login' else RATE_MAX_REQUESTS
    window,count=_rates.get(key,(now,0)); window,count=(now,0) if now-window>RATE_WINDOW_SECONDS else (window,count)
    if count>=limit: return JSONResponse(status_code=429,content={"success":False,"error":{"code":"RATE_LIMITED","message":"Too many requests. Please try again shortly."}},headers={"Retry-After":str(RATE_WINDOW_SECONDS)})
    _rates[key]=(window,count+1)
    try: response=await call_next(request)
    except Exception:
        logger.exception("Unhandled request error: %s %s",request.method,request.url.path)
        response=JSONResponse(status_code=500,content={"success":False,"error":{"code":"INTERNAL_ERROR","message":"Unable to complete the request. Please try again."}})
    response.headers.update({"X-Content-Type-Options":"nosniff","X-Frame-Options":"DENY","Referrer-Policy":"no-referrer","Content-Security-Policy":"default-src 'none'; frame-ancestors 'none'","Permissions-Policy":"geolocation=(), microphone=(), camera=()"})
    if os.getenv("HTTPS_ENABLED","false").lower()=="true": response.headers["Strict-Transport-Security"]="max-age=31536000; includeSubDomains"
    logger.info("%s %s %s %.3fs",request.method,request.url.path,response.status_code,time.perf_counter()-started)
    return response
@app.exception_handler(HTTPException)
async def http_error(_:Request, exc:HTTPException):
    code="AUTHORIZATION_ERROR" if exc.status_code==403 else ("AUTHENTICATION_ERROR" if exc.status_code==401 else "REQUEST_ERROR")
    return JSONResponse(status_code=exc.status_code,content={"success":False,"error":{"code":code,"message":str(exc.detail)}})
@app.exception_handler(RequestValidationError)
async def validation_error(_:Request, exc:RequestValidationError):
    return JSONResponse(status_code=422,content={"success":False,"error":{"code":"VALIDATION_ERROR","message":"Invalid request data"}})

@app.get("/health")
def health(db:DB):
    db.execute(select(1)); return {"success":True,"data":{"status":"ok"},"message":"Service is healthy"}

class EmailModel(BaseModel):
    email: str
    @field_validator("email")
    @classmethod
    def valid_email(cls,value):
        value=value.strip().lower()
        if len(value)>320 or "@" not in value or value.startswith("@") or value.endswith("@"): raise ValueError("A valid email address is required.")
        return value
class Login(EmailModel): password: str
class UserCreate(EmailModel): full_name: str=Field(min_length=2,max_length=160); password: str; mobile: str|None=None; consultant_id: str|None=None; role: Role=Role.END_USER; department_id:int|None=None
class UserAdminUpdate(BaseModel): email:str|None=Field(default=None,max_length=320);full_name:str|None=Field(default=None,min_length=2,max_length=160); mobile:str|None=None; consultant_id:str|None=None; role:Role|None=None; department_id:int|None=None
class AdminPasswordReset(BaseModel): new_password:str
class UserOut(EmailModel):
    model_config=ConfigDict(from_attributes=True)
    id:int; full_name:str; role:Role; status:AccountStatus; mobile:str|None=None; department_id:int|None=None
class TokenOut(BaseModel): access_token:str; refresh_token:str; token_type:str="bearer"; user:UserOut
class Register(BaseModel):
    full_name:str=Field(min_length=2,max_length=160); email:str; mobile:str|None=None; consultant_id:str|None=None; password:str; confirm_password:str
    @field_validator("email")
    @classmethod
    def valid_email(cls,value): return EmailModel(email=value).email
class RefreshRequest(BaseModel): refresh_token:str=Field(min_length=32,max_length=512)
class ForgotPassword(EmailModel): pass
class ResetPassword(BaseModel): token:str=Field(min_length=32,max_length=512); new_password:str; confirm_password:str
class ChangePassword(BaseModel): current_password:str; new_password:str; confirm_password:str
class ProfileUpdate(BaseModel): full_name:str=Field(min_length=2,max_length=160); mobile:str|None=None; consultant_id:str|None=None
class MasterDataIn(BaseModel): category:str=Field(min_length=2,max_length=64); code:str=Field(min_length=1,max_length=64); label:str=Field(min_length=2,max_length=160); active:bool=True
class MasterDataOut(MasterDataIn): model_config=ConfigDict(from_attributes=True); id:int
class SettingIn(BaseModel): value:str=Field(min_length=1,max_length=5000)
class SettingOut(BaseModel): model_config=ConfigDict(from_attributes=True); key:str; value:str
class JobCardUpdate(BaseModel): model_config=ConfigDict(extra="forbid"); msme_id:int|None=None; location:str|None=Field(default=None,min_length=2,max_length=160); project_start_date:date|None=None; project_end_date:date|None=None; required_md:Decimal|None=Field(default=None,gt=0); hours_per_manday:Decimal|None=Field(default=None,gt=0,le=24); unit_representative_id:int|None=None; director_id:int|None=None; remarks:str|None=Field(default=None,max_length=10000);status:str|None=Field(default=None,pattern="^(ASSIGNED|IN_PROGRESS|ON_HOLD|CANCELLED|AWAITING_PAYMENT)$")
class ReassignmentIn(BaseModel):
    user_id:int;assignment_type:str=Field(pattern="^(LEAD_CONSULTANT|CO_CONSULTANT|SUBJECT_EXPERT)$");reason:str=Field(min_length=3,max_length=5000);allocation_start_date:date|None=None;allocation_end_date:date|None=None
    @model_validator(mode="after")
    def valid_allocation_dates(self):
        if self.allocation_start_date and self.allocation_end_date and self.allocation_end_date<self.allocation_start_date: raise ValueError("Allocation end date cannot be before its start date.")
        return self
class ReopenIn(BaseModel): reason:str=Field(min_length=3,max_length=5000)
class VisitRejectIn(BaseModel): reason:str=Field(min_length=3,max_length=5000)
class MSMECreate(BaseModel):
    name:str=Field(min_length=2,max_length=200); msme_code:str=Field(min_length=2,max_length=64); zed_id:str|None=None; certificate_level:str="Bronze"; address:str|None=None; city:str|None=None; state:str|None=None; contact_person:str|None=None;email:str|None=Field(default=None,max_length=320);phone:str|None=Field(default=None,max_length=40);industry:str|None=Field(default=None,max_length=120);unit_name:str|None=Field(default=None,max_length=160)
class MSMEUpdate(BaseModel):
    name:str|None=Field(default=None,min_length=2,max_length=200); msme_code:str|None=Field(default=None,min_length=2,max_length=64); zed_id:str|None=None; certificate_level:str|None=None; address:str|None=None; city:str|None=None; state:str|None=None; contact_person:str|None=None;email:str|None=Field(default=None,max_length=320);phone:str|None=Field(default=None,max_length=40);industry:str|None=Field(default=None,max_length=120);unit_name:str|None=Field(default=None,max_length=160);status:str|None=Field(default=None,pattern="^(ACTIVE|INACTIVE)$")
class MSMEOut(MSMECreate):
    model_config=ConfigDict(from_attributes=True)
    id:int; status:str
class JobCardCreate(BaseModel):
    model_config=ConfigDict(extra="forbid")
    msme_id:int; location:str=Field(min_length=2,max_length=160); project_start_date:date; project_end_date:date; required_md:Decimal|None=Field(default=None,gt=0); hours_per_manday:Decimal|None=Field(default=None,gt=0,le=24); lead_consultant_id:int; co_consultant_id:int|None=None; subject_expert_id:int|None=None; unit_representative_id:int|None=None; director_id:int|None=None;remarks:str|None=Field(default=None,max_length=10000)
    source_ticket_id:UUID|None=None;source_enquiry_id:UUID|None=None;project_name:str|None=Field(default=None,max_length=200);package_code:str|None=Field(default=None,max_length=64);scope:str|None=Field(default=None,max_length=10000)
class PackageMilestoneConfig(BaseModel):
    code:str=Field(min_length=1,max_length=24);name:str=Field(min_length=2,max_length=120);planned_visits:int=Field(ge=1);planned_mandays:Decimal=Field(gt=0);amount:Decimal=Field(gt=0);payment_percent:Decimal=Field(gt=0,le=100);activities:list[str]=Field(default_factory=list)
class PackageConfig(BaseModel):
    code:str=Field(min_length=2,max_length=64,pattern="^[A-Z0-9_-]+$");name:str=Field(min_length=2,max_length=120);scope:str=Field(min_length=3,max_length=10000)
    planned_mandays:Decimal=Field(gt=0);commercial_amount:Decimal=Field(gt=0);project_payment_percent:Decimal=Field(gt=0,le=100)
    milestones:list[PackageMilestoneConfig]=Field(min_length=1,max_length=12);activities:list[str]=Field(default_factory=list);active:bool=True
    @field_validator("milestones")
    @classmethod
    def validate_milestone_plan(cls,value):
        if len({item.code for item in value})!=len(value): raise ValueError("Milestone codes must be unique within a package.")
        return value
class PackageOut(PackageConfig):
    model_config=ConfigDict(from_attributes=True)
    id:int;updated_at:datetime
class WorkflowStepConfig(BaseModel):
    action_type:str=Field(min_length=2,max_length=64);title:str=Field(min_length=2,max_length=200);description:str|None=Field(default=None,max_length=10000)
    department_code:str=Field(min_length=2,max_length=48);role:Role|None=None;due_days:int|None=Field(default=None,ge=1,le=365);return_to_step:int|None=Field(default=None,ge=1,le=25)
class WorkflowConfig(BaseModel):
    code:str=Field(min_length=2,max_length=64,pattern="^[A-Z0-9_-]+$");name:str=Field(min_length=2,max_length=160);steps:list[WorkflowStepConfig]=Field(min_length=1,max_length=25);trigger_event:str=Field(default="MANUAL",pattern="^(MANUAL|PROJECT_ACTIVATED|MILESTONE_PAID)$");active:bool=True
    @field_validator("steps")
    @classmethod
    def validate_return_steps(cls,value):
        if any(step.return_to_step and step.return_to_step>len(value) for step in value): raise ValueError("Return step must refer to a step in this workflow.")
        return value
class WorkflowOut(WorkflowConfig):
    model_config=ConfigDict(from_attributes=True)
    id:int;updated_at:datetime
class ActionStart(BaseModel):
    template_code:str=Field(min_length=2,max_length=64);ticket_id:UUID|None=None;enquiry_id:UUID|None=None;job_card_id:int|None=None;milestone_id:int|None=None;assigned_user_id:int|None=None
class ActionDecision(BaseModel):
    decision:str=Field(pattern="^(COMPLETED|RETURNED|REJECTED|CANCELLED)$");remarks:str|None=Field(default=None,min_length=3,max_length=10000)
class ActionStatusUpdate(BaseModel):
    status:str=Field(pattern="^(IN_PROGRESS|WAITING|ON_HOLD)$");remarks:str|None=Field(default=None,max_length=10000)
class ActionTransfer(BaseModel):
    department_id:int=Field(gt=0);assigned_user_id:int|None=None;reason:str=Field(min_length=3,max_length=10000)
class WorkflowActionOut(BaseModel):
    id:UUID;action_number:str;template_code:str;workflow_step:int;ticket_id:UUID|None=None;enquiry_id:UUID|None=None;job_card_id:int|None=None;milestone_id:int|None=None
    action_type:str;title:str;description:str|None=None;department_id:int;department_name:str;assigned_role:str|None=None;assigned_user_id:int|None=None;assigned_user_name:str|None=None;priority:str;due_at:datetime|None=None;status:str
    parent_action_id:UUID|None=None;previous_action_id:UUID|None=None;next_action_id:UUID|None=None;created_by:int;created_at:datetime;started_at:datetime|None=None;completed_at:datetime|None=None;remarks:str|None=None
class WorkflowAttachmentOut(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id:int;action_id:UUID;file_name:str;file_size:int;mime_type:str;uploaded_by:int;uploaded_at:datetime
class JobCardOut(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id:int; number:str; source_ticket_id:UUID|None=None;source_enquiry_id:UUID|None=None;project_name:str|None=None;package_code:str|None=None;scope:str|None=None;msme_id:int; location:str; project_start_date:date; project_end_date:date; required_md:Decimal; hours_per_manday:Decimal=Decimal(8); required_hours:Decimal; unit_representative_id:int|None=None; director_id:int|None=None;remarks:str|None=None;status:str; payment_gate_enabled:bool=False
    @field_serializer("required_hours")
    def serialize_required_hours(self,value:Decimal)->Decimal: return value.quantize(Decimal("0.01"))
class JobCardListOut(JobCardOut): completed_md:Decimal=Decimal(0); remaining_md:Decimal=Decimal(0); progress:Decimal=Decimal(0); overrun:bool=False
class MilestoneCreate(BaseModel): code:str=Field(pattern="^M[123]$"); name:str=Field(min_length=2,max_length=120); planned_visits:int=Field(ge=1); planned_mandays:Decimal|None=Field(default=None,gt=0);amount:Decimal|None=Field(default=None,ge=0);payment_percent:Decimal|None=Field(default=None,ge=0,le=100);activities:list[str]=Field(default_factory=list);responsible_consultant_id:int|None=None;description:str|None=None; start_date:date|None=None; closing_date:date|None=None; payment_date:date|None=None; portal_report_submission_date:date|None=None
class MilestoneUpdate(BaseModel): name:str|None=Field(default=None,min_length=2,max_length=120); planned_visits:int|None=Field(default=None,ge=1);planned_mandays:Decimal|None=Field(default=None,gt=0);amount:Decimal|None=Field(default=None,ge=0);payment_percent:Decimal|None=Field(default=None,ge=0,le=100);activities:list[str]|None=None;responsible_consultant_id:int|None=None;description:str|None=None;start_date:date|None=None; closing_date:date|None=None; payment_date:date|None=None; portal_report_submission_date:date|None=None
class MilestoneOut(MilestoneCreate):
    model_config=ConfigDict(from_attributes=True)
    id:int; job_card_id:int; status:str; payment_status:str|None="PAYMENT_PENDING"; actual_visits:int=0; progress:Decimal=Decimal(0); review_status:str="NOT_SUBMITTED"; review_cycle:int=0; review_remarks:str|None=None
class MilestoneReviewDecision(BaseModel):
    decision:str=Field(pattern="^(APPROVED|RETURNED|REJECTED)$");comment:str|None=Field(default=None,max_length=10000)
class MilestoneReviewOut(BaseModel):
    id:int;milestone_id:int;cycle:int;stage:str;decision:str;comment:str|None=None;decided_by:int;created_at:datetime
class ActivityTypeUpsert(BaseModel):
    code:str=Field(min_length=2,max_length=48,pattern="^[A-Z][A-Z0-9_]*$");name:str=Field(min_length=2,max_length=120);active:bool=True
class ActivityTypeOut(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id:int;code:str;name:str;active:bool
class JobActivityCreate(BaseModel):
    milestone_id:int;activity_type_id:int;name:str=Field(min_length=2,max_length=200);description:str=Field(min_length=3,max_length=10000)
    visit_id:int|None=None;assigned_user_id:int|None=None;start_date:date|None=None;end_date:date|None=None;duration_minutes:int=Field(default=0,ge=0,le=100000);status:str=Field(default="PLANNED",pattern="^(PLANNED|IN_PROGRESS|COMPLETED)$");remarks:str|None=Field(default=None,max_length=10000)
    @field_validator("end_date")
    @classmethod
    def activity_dates_ordered(cls,value,info):
        start=info.data.get("start_date")
        if value and start and value<start:raise ValueError("Activity end date cannot precede its start date.")
        return value
class JobActivityUpdate(BaseModel):
    status:str|None=Field(default=None,pattern="^(PLANNED|IN_PROGRESS|COMPLETED|CANCELLED)$");remarks:str|None=Field(default=None,max_length=10000);duration_minutes:int|None=Field(default=None,ge=0,le=100000);start_date:date|None=None;end_date:date|None=None
class JobActivityOut(BaseModel):
    id:int;job_card_id:int;milestone_id:int;visit_id:int|None;activity_type_id:int;activity_type_code:str;activity_type_name:str;name:str;description:str;assigned_user_id:int;assigned_user_name:str;start_date:date|None;end_date:date|None;duration_minutes:int;mandays:Decimal;status:str;remarks:str|None;created_by:int;created_at:datetime
class JobActivityAttachmentOut(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id:int;activity_id:int;file_name:str;file_size:int;mime_type:str;uploaded_by:int;uploaded_at:datetime
class VisitCreate(BaseModel):
    milestone_id:int;visit_date:date;hours:int=Field(default=0,ge=0);minutes:int=Field(default=0,ge=0,lt=60)
    start_time:TimeOfDay|None=None;end_time:TimeOfDay|None=None;location:str|None=Field(default=None,max_length=240);findings:str|None=Field(default=None,max_length=10000)
    work_details:str=Field(min_length=3,max_length=10000);rating_code:str=Field(pattern="^[ABCDE]$");consultant_id:int|None=None;co_consultant_id:int|None=None;subject_expert_id:int|None=None;remarks:str|None=None;attachment_name:str|None=None;attachment_mime_type:str|None=None;attachment_base64:str|None=None
    @field_validator("end_time")
    @classmethod
    def visit_times_ordered(cls,value,info):
        start=info.data.get("start_time")
        if (start is None)!=(value is None): raise ValueError("Enter both visit start and end times, or leave both blank.")
        if start is not None and value<=start: raise ValueError("Visit end time must be after start time.")
        return value
class PlannedVisitCreate(BaseModel):
    job_card_id:int;milestone_id:int;visit_date:date;start_time:TimeOfDay;expected_duration_minutes:int=Field(gt=0,le=1440);location:str=Field(min_length=2,max_length=240);objective:str=Field(min_length=3,max_length=5000);allow_conflict:bool=False
class PlannedVisitUpdate(BaseModel):
    visit_date:date|None=None;start_time:TimeOfDay|None=None;expected_duration_minutes:int|None=Field(default=None,gt=0,le=1440);location:str|None=Field(default=None,min_length=2,max_length=240);objective:str|None=Field(default=None,min_length=3,max_length=5000);status:str|None=Field(default=None,pattern="^(PLANNED|CANCELLED|COMPLETED)$");reason:str|None=Field(default=None,min_length=3,max_length=5000);allow_conflict:bool=False;actual_date:date|None=None;actual_start_time:TimeOfDay|None=None;actual_duration_minutes:int|None=Field(default=None,gt=0,le=1440);outcome:str|None=Field(default=None,max_length=10000)
class PlannedVisitOut(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id:int;job_card_id:int;job_card_number:str|None=None;msme_name:str|None=None;milestone_id:int;milestone_code:str|None=None;milestone_name:str|None=None;consultant_id:int;consultant_name:str|None=None;visit_date:date;start_time:TimeOfDay;expected_duration_minutes:int;location:str;objective:str;status:str;change_reason:str|None=None;actual_date:date|None=None;actual_start_time:TimeOfDay|None=None;actual_duration_minutes:int|None=None;outcome:str|None=None;created_by:int;created_at:datetime;updated_at:datetime
class VisitUpdate(VisitCreate): pass
class VisitOut(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id:int; job_card_id:int; milestone_id:int; visit_number:int; visit_date:date; start_time:TimeOfDay|None=None;end_time:TimeOfDay|None=None;location:str|None=None;findings:str|None=None;consultant_id:int|None=None; co_consultant_id:int|None=None; subject_expert_id:int|None=None; working_minutes:int; work_details:str; rating_code:str; rating_description:str; remarks:str|None=None; attachment_name:str|None=None; attachment_mime_type:str|None=None; status:str; rejection_reason:str|None=None; created_by:int; job_card_number:str|None=None
class VisitReviewDecision(BaseModel): decision:str=Field(pattern="^(APPROVED|RETURNED|REJECTED)$");comment:str|None=Field(default=None,max_length=5000)
class DocumentUpload(BaseModel):
    document_type:str=Field(min_length=2,max_length=80); file_name:str=Field(min_length=1,max_length=255); mime_type:str; content_base64:str; remarks:str|None=None
class DocumentOut(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id:int; job_card_id:int; document_type:str; file_name:str; file_size:int; mime_type:str; version:int; status:str; remarks:str|None=None; uploaded_by:int; reviewed_by:int|None=None;reviewed_at:datetime|None=None;job_card_number:str|None=None
class RecordDocumentUpload(BaseModel): document_type:str=Field(min_length=2,max_length=80);file_name:str=Field(min_length=1,max_length=255);mime_type:str;content_base64:str;remarks:str|None=Field(default=None,max_length=10000);status:str=Field(default="SUBMITTED",pattern="^(DRAFT|SUBMITTED)$")
class RecordDocumentOut(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id:int;owner_job_card_id:int|None=None;ticket_id:UUID|None=None;enquiry_id:UUID|None=None;job_card_id:int|None=None;milestone_id:int|None=None;action_id:UUID|None=None;visit_id:int|None=None;payment_id:int|None=None;milestone_review_id:int|None=None;visit_review_id:int|None=None;approval_id:int|None=None
    document_type:str;file_name:str;file_size:int;mime_type:str;version:int;status:str;remarks:str|None=None;uploaded_by:int;uploaded_at:datetime;reviewed_by:int|None=None;reviewed_at:datetime|None=None
class RecordDocumentReview(BaseModel): decision:str=Field(pattern="^(UNDER_REVIEW|APPROVED|RETURNED|REJECTED)$");comment:str=Field(min_length=3,max_length=5000)
class DocumentReview(BaseModel): decision:str=Field(pattern="^(APPROVED|RETURNED|REJECTED)$"); comment:str=Field(min_length=3,max_length=5000)
class ApprovalAction(BaseModel): decision:str=Field(pattern="^(APPROVED|REJECTED)$"); comment:str|None=Field(default=None,max_length=5000)
class ApprovalOut(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id:int; job_card_id:int; stage:str; cycle:int; decision:str; comment:str|None=None; decided_by:int
class ClosureUpsert(BaseModel):
    client_confirmation:str=Field(min_length=5,max_length=5000)
    problem:str=Field(min_length=3,max_length=10000);requirement:str=Field(min_length=3,max_length=10000)
    solution:str=Field(min_length=3,max_length=10000);implementation:str=Field(min_length=3,max_length=10000)
    benefits:str=Field(min_length=3,max_length=10000);results:str=Field(min_length=3,max_length=10000)
    feedback_rating:int=Field(ge=1,le=5);feedback:str=Field(min_length=3,max_length=10000)
    testimonial:str|None=Field(default=None,max_length=5000);publication_permission:bool=False
class ClosureOut(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id:int;job_card_id:int;client_confirmation:str|None=None;confirmed_at:datetime|None=None
    problem:str|None=None;requirement:str|None=None;solution:str|None=None;implementation:str|None=None;benefits:str|None=None;results:str|None=None
    feedback_rating:int|None=None;feedback:str|None=None;testimonial:str|None=None;publication_permission:bool
    certificate_number:str|None=None;closed_at:datetime|None=None
class NotificationOut(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id:int; message:str; link:str|None=None; read_at:datetime|None=None; created_at:datetime
class AuditOut(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id:int; actor_id:int|None=None; action:str; entity_type:str; entity_id:int|None=None; entity_ref:str|None=None; details:str|None=None; created_at:datetime

class DepartmentCreate(BaseModel):
    code:str=Field(min_length=2,max_length=48,pattern="^[A-Z][A-Z0-9_]*$")
    name:str=Field(min_length=2,max_length=120)
class DepartmentOut(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id:int;code:str;name:str;active:bool
class ActionTransferUserOut(BaseModel):
    id:int;full_name:str;role:str
class GlobalSearchResult(BaseModel):
    kind:str;id:str;title:str;subtitle:str;status:str;href:str
class PaymentCreate(BaseModel):
    milestone_id:int|None=None;source_ticket_id:UUID|None=None
    description:str=Field(min_length=3,max_length=240);amount_due:Decimal=Field(gt=0,max_digits=12,decimal_places=2)
    currency:str=Field(default="INR",pattern="^[A-Z]{3}$");invoice_date:date=Field(default_factory=date.today)
    due_date:date|None=None;remarks:str|None=Field(default=None,max_length=10000)
    @field_validator("due_date")
    @classmethod
    def due_after_invoice(cls,value,info):
        invoice_date=info.data.get("invoice_date")
        if value and invoice_date and value<invoice_date: raise ValueError("Invoice due date cannot be before the invoice date.")
        return value
class PaymentOut(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id:int;invoice_number:str;job_card_id:int;milestone_id:int|None=None;source_ticket_id:UUID|None=None
    description:str;amount_due:Decimal;amount_paid:Decimal;currency:str;status:str;invoice_date:date;due_date:date|None=None
    remarks:str|None=None;created_by:int;created_at:datetime;updated_at:datetime
class PaymentReceiptCreate(BaseModel):
    amount:Decimal=Field(gt=0,max_digits=12,decimal_places=2)
    external_reference:str=Field(min_length=2,max_length=120)
    received_at:datetime=Field(default_factory=lambda:datetime.now(timezone.utc))
    remarks:str|None=Field(default=None,max_length=5000)
    @field_validator("external_reference")
    @classmethod
    def normalize_reference(cls,value):
        value=value.strip().upper()
        if len(value)<2: raise ValueError("A payment reference is required.")
        return value
class PaymentCancelIn(BaseModel): reason:str=Field(min_length=3,max_length=1000)
class PaymentReceiptOut(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id:int;payment_id:int;amount:Decimal;external_reference:str|None=None;received_at:datetime;confirmed_by:int;remarks:str|None=None
class TicketCreate(BaseModel):
    title:str=Field(min_length=3,max_length=200)
    description:str=Field(min_length=1,max_length=10000)
    ticket_type:str=Field(pattern="^(NEW_ENQUIRY|FOLLOW_UP|PAYMENT_REQUEST|DOCUMENT_REQUEST|CORRECTION|REVIEW|APPROVAL|CLIENT_COMMUNICATION|INTERNAL_ACTION|OTHER)$")
    priority:str=Field(default="NORMAL",pattern="^(LOW|NORMAL|HIGH|URGENT)$")
    assigned_department_id:int|None=None
    assigned_user_id:int|None=None
    due_date:datetime|None=None
    parent_ticket_id:UUID|None=None
    enquiry_id:UUID|None=None
    job_card_id:int|None=None
    milestone_id:int|None=None
    remarks:str|None=Field(default=None,max_length=10000)
class TicketUpdate(BaseModel):
    title:str|None=Field(default=None,min_length=3,max_length=200)
    description:str|None=Field(default=None,min_length=1,max_length=10000)
    priority:str|None=Field(default=None,pattern="^(LOW|NORMAL|HIGH|URGENT)$")
    assigned_department_id:int|None=None
    assigned_user_id:int|None=None
    due_date:datetime|None=None
    enquiry_id:UUID|None=None
    job_card_id:int|None=None
    milestone_id:int|None=None
    status:str|None=Field(default=None,pattern="^(OPEN|IN_PROGRESS|WAITING|COMPLETED|RETURNED|REJECTED|ON_HOLD|CANCELLED)$")
    remarks:str|None=Field(default=None,max_length=10000)
class TicketOut(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id:UUID;number:str;title:str;description:str;ticket_type:str;priority:str;created_by:int
    created_department_id:int|None=None;assigned_department_id:int|None=None;assigned_user_id:int|None=None
    status:str;due_date:datetime|None=None;parent_ticket_id:UUID|None=None;enquiry_id:UUID|None=None
    job_card_id:int|None=None;milestone_id:int|None=None;enquiry_number:str|None=None;job_card_number:str|None=None;remarks:str|None=None;created_at:datetime;updated_at:datetime;completed_at:datetime|None=None
class EnquiryCreate(BaseModel):
    source_ticket_id:UUID|None=None;msme_id:int|None=None
    company_name:str=Field(min_length=2,max_length=200);contact_person:str|None=Field(default=None,max_length=160)
    email:str|None=Field(default=None,max_length=320);phone:str|None=Field(default=None,max_length=40)
    industry:str|None=Field(default=None,max_length=120);location:str|None=Field(default=None,max_length=200)
    requirement:str=Field(min_length=3,max_length=10000);enquiry_date:date=Field(default_factory=date.today)
    source:str|None=Field(default=None,max_length=80);package_code:str|None=Field(default=None,max_length=64)
    expected_scope:str|None=Field(default=None,max_length=10000);expected_commercial_amount:Decimal|None=Field(default=None,ge=0,max_digits=12,decimal_places=2);assigned_marketing_user_id:int|None=None
    status:str=Field(default="NEW",pattern="^(NEW|CONTACTED|FOLLOW_UP|QUALIFIED|FINALISED|LOST|CANCELLED)$")
    follow_up_date:date|None=None;remarks:str|None=Field(default=None,max_length=10000)
    @field_validator("email")
    @classmethod
    def normalize_optional_email(cls,value): return value.strip().lower() if value else value
class EnquiryUpdate(BaseModel):
    msme_id:int|None=None;company_name:str|None=Field(default=None,min_length=2,max_length=200)
    contact_person:str|None=Field(default=None,max_length=160);email:str|None=Field(default=None,max_length=320)
    phone:str|None=Field(default=None,max_length=40);industry:str|None=Field(default=None,max_length=120)
    location:str|None=Field(default=None,max_length=200);requirement:str|None=Field(default=None,min_length=3,max_length=10000)
    enquiry_date:date|None=None;source:str|None=Field(default=None,max_length=80);package_code:str|None=Field(default=None,max_length=64)
    expected_scope:str|None=Field(default=None,max_length=10000);expected_commercial_amount:Decimal|None=Field(default=None,ge=0,max_digits=12,decimal_places=2);assigned_marketing_user_id:int|None=None
    status:str|None=Field(default=None,pattern="^(NEW|CONTACTED|FOLLOW_UP|QUALIFIED|FINALISED|LOST|CANCELLED|CONVERTED)$")
    follow_up_date:date|None=None;remarks:str|None=Field(default=None,max_length=10000)
class EnquiryLinkJobCard(BaseModel): job_card_id:int
class EnquiryOut(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id:UUID;number:str;source_ticket_id:UUID|None=None;msme_id:int|None=None;company_name:str
    contact_person:str|None=None;email:str|None=None;phone:str|None=None;industry:str|None=None;location:str|None=None
    requirement:str;enquiry_date:date;source:str|None=None;package_code:str|None=None;expected_scope:str|None=None;expected_commercial_amount:Decimal|None=None
    assigned_marketing_user_id:int|None=None;status:str;follow_up_date:date|None=None;remarks:str|None=None
    created_by:int;created_at:datetime;updated_at:datetime
class EnquiryFollowUpCreate(BaseModel):
    follow_up_date:datetime=Field(default_factory=lambda:datetime.now(timezone.utc))
    follow_up_type:str=Field(pattern="^(CALL|MEETING|EMAIL|CLIENT_DISCUSSION|FOLLOW_UP|REQUIREMENT_UPDATE|COMMERCIAL_DISCUSSION|SCOPE_DISCUSSION)$")
    person_contacted:str|None=Field(default=None,max_length=160);notes:str=Field(min_length=2,max_length=10000)
    next_follow_up_date:datetime|None=None;status:str=Field(default="COMPLETED",pattern="^(OPEN|COMPLETED|CANCELLED)$")
    attachment_name:str|None=Field(default=None,max_length=255);attachment_mime_type:str|None=None;attachment_base64:str|None=None
class EnquiryFollowUpOut(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id:int;enquiry_id:UUID;follow_up_date:datetime;follow_up_type:str;person_contacted:str|None=None
    notes:str;next_follow_up_date:datetime|None=None;responsible_user_id:int;status:str;attachment_name:str|None=None;attachment_mime_type:str|None=None;created_at:datetime
class RecordFileUpload(BaseModel):
    file_name:str=Field(min_length=1,max_length=255);mime_type:str=Field(min_length=3,max_length=120)
    content_base64:str=Field(min_length=8,max_length=15_000_000);document_type:str|None=Field(default=None,min_length=2,max_length=80);remarks:str|None=Field(default=None,max_length=5000)
class RecordFileOut(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id:int;file_name:str;file_size:int;mime_type:str;uploaded_by:int;uploaded_at:datetime
class TicketAttachmentOut(RecordFileOut): ticket_id:UUID
class EnquiryDocumentOut(RecordFileOut): enquiry_id:UUID;document_type:str;remarks:str|None=None
class TicketRelationshipCreate(BaseModel):
    related_type:str=Field(pattern="^(TICKET|ENQUIRY|JOB_CARD)$")
    related_id:str=Field(min_length=1,max_length=64)
    relationship_type:str=Field(pattern="^(PARENT|CHILD|RELATED|FOLLOW_UP|REFERENCE|CREATED_FROM|BLOCKED_BY|REOPENED_FROM|DUPLICATE_OF)$")
class TicketRelationshipOut(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id:int;ticket_id:UUID;related_ticket_id:UUID|None=None;related_enquiry_id:UUID|None=None
    related_job_card_id:int|None=None;relationship_type:str;created_by:int;created_at:datetime

def current_user(db:DB, credentials: Annotated[HTTPAuthorizationCredentials|None,Depends(bearer)]):
    if not credentials: raise HTTPException(401,"Authentication required.")
    claims=decode_token(credentials.credentials); session=db.get(AuthSession,claims["sid"])
    expiry=session.expires_at.replace(tzinfo=timezone.utc) if session and session.expires_at.tzinfo is None else (session.expires_at if session else None)
    if not session or session.revoked or expiry < datetime.now(timezone.utc): raise HTTPException(401,"Your session has expired. Please sign in again.")
    user=db.get(User,int(claims["sub"]))
    if not user or user.status != AccountStatus.ACTIVE: raise HTTPException(403,"Your account is not active.")
    return user
def require(*roles:Role):
    def check(user:Annotated[User,Depends(current_user)]):
        if roles and user.role not in roles: raise HTTPException(403,"You do not have permission to access this resource.")
        return user
    return check
def token_hash(value:str): return hashlib.sha256(value.encode()).hexdigest()
def issue_session(db:Session,user:User):
    sid=secrets.token_urlsafe(32); refresh=secrets.token_urlsafe(48); now=datetime.now(timezone.utc)
    db.add(AuthSession(id=sid,user_id=user.id,expires_at=now+timedelta(minutes=JWT_EXPIRY_MINUTES),refresh_token_hash=token_hash(refresh),refresh_expires_at=now+timedelta(days=REFRESH_EXPIRY_DAYS)))
    return TokenOut(access_token=issue_token(user.id,sid),refresh_token=refresh,user=user)

def _can_manage_crm(user:User)->bool:
    return user.role in (Role.ADMIN,Role.MARKETING)

def _ticket_visible(ticket:Ticket,user:User)->bool:
    if user.role in (Role.ADMIN,Role.MANAGEMENT): return True
    return ticket.created_by==user.id or ticket.assigned_user_id==user.id or (user.department_id is not None and user.department_id in (ticket.created_department_id,ticket.assigned_department_id))

def _get_visible_ticket(db:Session,ticket_id:UUID,user:User)->Ticket:
    ticket=db.get(Ticket,ticket_id)
    if not ticket: raise HTTPException(404,"Ticket not found.")
    if not _ticket_visible(ticket,user): raise HTTPException(404,"Ticket not found.")
    return ticket

def _ticket_out(ticket:Ticket,enquiry_number:str|None=None,job_card_number:str|None=None):
    data=TicketOut.model_validate(ticket).model_dump()
    data["enquiry_number"]=enquiry_number;data["job_card_number"]=job_card_number
    due=ticket.due_date
    if due and due.tzinfo is None: due=due.replace(tzinfo=timezone.utc)
    if due and due < datetime.now(timezone.utc) and ticket.status not in ("COMPLETED","REJECTED","CANCELLED"):
        data["status"]="OVERDUE"
    return data

def _visible_enquiry(enquiry:Enquiry,user:User)->bool:
    return user.role in (Role.ADMIN,Role.MANAGEMENT) or enquiry.created_by==user.id or enquiry.assigned_marketing_user_id==user.id

def _payment_out(payment:Payment):
    data=PaymentOut.model_validate(payment).model_dump()
    if payment.status in ("PENDING","INVOICE_RAISED","PARTIALLY_PAID") and payment.due_date and payment.due_date<date.today(): data["status"]="OVERDUE"
    return data

def _job_card_out_for_user(card:JobCard,user:User):
    data=JobCardOut.model_validate(card).model_dump()
    if user.role==Role.ADMIN:
        if data["status"]=="AWAITING_PAYMENT": data["status"]="PENDING_ACTIVATION"
        data["payment_gate_enabled"]=False
    return JobCardOut(**data)

def _milestone_out_for_user(db:Session,milestone:Milestone,user:User):
    values={**milestone.__dict__,**milestone_summary(db,milestone)}
    if user.role==Role.ADMIN:
        values["payment_status"]=None
        values["payment_date"]=None
        if values.get("status")=="LOCKED": values["status"]="PENDING_ACTIVATION"
    return MilestoneOut(**values)

def _require_work_payment(db:Session,card:JobCard,milestone:Milestone|None=None):
    if not card.payment_gate_enabled: return
    project_paid=db.scalar(select(Payment.id).where(Payment.job_card_id==card.id,Payment.milestone_id.is_(None),Payment.status=="PAID"))
    if not project_paid: raise HTTPException(423,"Project work is locked until Marketing confirms the initial project payment.")
    if milestone:
        milestone_paid=db.scalar(select(Payment.id).where(Payment.job_card_id==card.id,Payment.milestone_id==milestone.id,Payment.status=="PAID"))
        if not milestone_paid: raise HTTPException(423,f"{milestone.code} work is locked until Marketing confirms its milestone payment.")

def _require_all_project_payments(db:Session,card:JobCard):
    _require_work_payment(db,card)
    if card.payment_gate_enabled:
        for milestone in db.scalars(select(Milestone).where(Milestone.job_card_id==card.id)):
            _require_work_payment(db,card,milestone)

def _save_encoded_file(file_name:str,mime_type:str,encoded:str):
    try:
        content=base64.b64decode(encoded,validate=True)
        key=save_file(file_name,mime_type,content)
        return key,len(content)
    except ValueError as exc: raise HTTPException(422,str(exc))
    except Exception: raise HTTPException(422,"The uploaded attachment is invalid.")

def _download_attachment(file_name:str,mime_type:str,key:str):
    from .storage import ROOT
    path=ROOT/key
    if not path.is_file(): raise HTTPException(404,"Attachment file not found.")
    return Response(content=path.read_bytes(),media_type=mime_type,headers={"Content-Disposition":f"attachment; filename*=UTF-8''{quote(file_name,safe='')}"})

@app.post("/api/job-cards/{card_id}/payments",response_model=PaymentOut,status_code=201)
def create_payment_invoice(card_id:int,data:PaymentCreate,db:DB,user:Annotated[User,Depends(require(Role.MARKETING))]):
    card=mutable_card(assigned_card(db,user,card_id))
    if data.milestone_id:
        milestone=db.get(Milestone,data.milestone_id)
        if not milestone or milestone.job_card_id!=card_id: raise HTTPException(422,"The selected milestone does not belong to this Job Card.")
        if milestone_summary(db,milestone)["status"]=="COMPLETED": raise HTTPException(409,"A completed milestone cannot receive a new invoice.")
    else: milestone=None
    if card.commercial_amount is not None:
        expected_amount=(Decimal(str(card.commercial_amount))*Decimal(str(card.project_payment_percent or 0))/Decimal(100)) if milestone is None else (Decimal(str(milestone.amount)) if milestone.amount is not None else None)
        if expected_amount is None or Decimal(data.amount_due)!=expected_amount:
            raise HTTPException(422,"Invoice amount must match the commercial amount configured for this package payment stage.")
    existing=db.scalar(select(Payment).where(Payment.job_card_id==card_id,Payment.milestone_id==data.milestone_id,Payment.status!="CANCELLED"))
    if existing: raise HTTPException(409,"An invoice already exists for this project or milestone. Update or cancel the existing invoice instead.")
    ticket=None
    if data.source_ticket_id:
        ticket=_get_visible_ticket(db,data.source_ticket_id,user)
        if ticket.job_card_id and ticket.job_card_id!=card_id: raise HTTPException(409,"The source ticket is already linked to another Job Card.")
    values=data.model_dump();values["amount_due"]=Decimal(values["amount_due"]);values["amount_paid"]=Decimal("0.00");values["status"]="INVOICE_RAISED"
    invoice=Payment(**values,invoice_number=next_business_number(db,"invoice"),job_card_id=card_id,created_by=user.id)
    db.add(invoice)
    try: db.flush()
    except IntegrityError:
        db.rollback();raise HTTPException(409,"An invoice already exists for this project or milestone.")
    if ticket:
        ticket.job_card_id=card_id
        linked=db.scalar(select(TicketRelationship.id).where(TicketRelationship.ticket_id==ticket.id,TicketRelationship.related_job_card_id==card_id))
        if not linked: db.add(TicketRelationship(ticket_id=ticket.id,related_job_card_id=card_id,relationship_type="REFERENCE",created_by=user.id))
    for assignment in db.scalars(select(Assignment).where(Assignment.job_card_id==card_id,Assignment.active.is_(True))):
        notify(db,assignment.user_id,f"Invoice {invoice.invoice_number} was raised for Job Card {card.number}.",f"/job-cards/{card_id}")
    audit(db,user.id,"PAYMENT_INVOICE_CREATED","PAYMENT",invoice.id,f"{invoice.invoice_number}; amount={invoice.amount_due}; milestone={milestone.code if milestone else 'PROJECT'}")
    db.commit();db.refresh(invoice);return invoice

@app.get("/api/job-cards/{card_id}/payments",response_model=list[PaymentOut])
def list_payments(card_id:int,db:DB,user:Annotated[User,Depends(require(Role.MARKETING,Role.MANAGEMENT))]):
    assigned_card(db,user,card_id)
    rows=list(db.scalars(select(Payment).where(Payment.job_card_id==card_id).order_by(Payment.invoice_date.desc(),Payment.id.desc())))
    return [_payment_out(payment) for payment in rows]

@app.get("/api/payments/{payment_id}/receipts",response_model=list[PaymentReceiptOut])
def list_payment_receipts(payment_id:int,db:DB,user:Annotated[User,Depends(require(Role.MARKETING,Role.MANAGEMENT))]):
    payment=db.get(Payment,payment_id)
    if not payment: raise HTTPException(404,"Invoice not found.")
    assigned_card(db,user,payment.job_card_id)
    return list(db.scalars(select(PaymentReceipt).where(PaymentReceipt.payment_id==payment_id).order_by(PaymentReceipt.received_at.desc())))

@app.post("/api/payments/{payment_id}/confirm",response_model=PaymentOut)
def confirm_payment(payment_id:int,data:PaymentReceiptCreate,db:DB,user:Annotated[User,Depends(require(Role.MARKETING))]):
    payment=db.scalar(select(Payment).where(Payment.id==payment_id).with_for_update())
    if not payment: raise HTTPException(404,"Invoice not found.")
    if not db.scalar(select(JobCard.id).where(JobCard.id==payment.job_card_id).with_for_update()): raise HTTPException(404,"Job Card not found.")
    card=mutable_card(assigned_card(db,user,payment.job_card_id))
    if payment.status in ("PAID","CANCELLED"): raise HTTPException(409,"This invoice cannot accept another payment confirmation.")
    if db.scalar(select(PaymentReceipt.id).where(PaymentReceipt.external_reference==data.external_reference)): raise HTTPException(409,"This payment reference has already been recorded.")
    received_at=data.received_at
    if received_at.tzinfo is None: received_at=received_at.replace(tzinfo=timezone.utc)
    if received_at>datetime.now(timezone.utc): raise HTTPException(422,"Payment received time cannot be in the future.")
    if received_at.date()<payment.invoice_date: raise HTTPException(422,"Payment cannot be received before the invoice date.")
    amount=Decimal(data.amount);balance=Decimal(payment.amount_due)-Decimal(payment.amount_paid)
    if amount>balance: raise HTTPException(422,"The received amount exceeds the outstanding invoice balance.")
    receipt=PaymentReceipt(payment_id=payment.id,amount=amount,external_reference=data.external_reference,received_at=received_at,confirmed_by=user.id,remarks=data.remarks)
    db.add(receipt);payment.amount_paid=Decimal(payment.amount_paid)+amount
    payment.status="PAID" if payment.amount_paid>=payment.amount_due else "PARTIALLY_PAID";payment.updated_at=datetime.now(timezone.utc)
    try: db.flush()
    except IntegrityError:
        db.rollback();raise HTTPException(409,"This payment reference has already been recorded.")
    if payment.status=="PAID":
        if payment.milestone_id:
            milestone=db.get(Milestone,payment.milestone_id)
            project_paid=not card.payment_gate_enabled or bool(db.scalar(select(Payment.id).where(Payment.job_card_id==card.id,Payment.milestone_id.is_(None),Payment.status=="PAID")))
            if milestone and milestone.job_card_id==card.id: milestone.status="READY" if project_paid else "LOCKED"
            if milestone and project_paid: _start_triggered_workflows(db,"MILESTONE_PAID",card,user,milestone)
            if not project_paid:
                for assignment in db.scalars(select(Assignment).where(Assignment.job_card_id==card.id,Assignment.active.is_(True))):
                    notify(db,assignment.user_id,f"Payment for {milestone.code if milestone else 'the milestone'} is confirmed; work remains locked until project activation.",f"/job-cards/{card.id}")
        else:
            card.status="IN_PROGRESS"
            audit(db,user.id,"JOB_CARD_ACTIVATED","JOB_CARD",card.id,f"Initial invoice {payment.invoice_number} paid")
            _start_triggered_workflows(db,"PROJECT_ACTIVATED",card,user)
            paid_milestones=db.scalars(select(Milestone).join(Payment,Payment.milestone_id==Milestone.id).where(Milestone.job_card_id==card.id,Payment.status=="PAID"))
            for milestone in paid_milestones:
                milestone.status="READY"
                _start_triggered_workflows(db,"MILESTONE_PAID",card,user,milestone)
                for assignment in db.scalars(select(Assignment).where(Assignment.job_card_id==card.id,Assignment.active.is_(True))):
                    notify(db,assignment.user_id,f"{milestone.code} is now unlocked after project activation.",f"/job-cards/{card.id}")
        for assignment in db.scalars(select(Assignment).where(Assignment.job_card_id==card.id,Assignment.active.is_(True))):
            message=f"Payment confirmed for invoice {payment.invoice_number}; {payment.milestone_id and 'milestone payment received' or 'project activated'}."
            if payment.milestone_id and card.payment_gate_enabled and not db.scalar(select(Payment.id).where(Payment.job_card_id==card.id,Payment.milestone_id.is_(None),Payment.status=="PAID")): message+= " Initial project payment is still required before work can begin."
            notify(db,assignment.user_id,message,f"/job-cards/{card.id}")
    audit(db,user.id,"PAYMENT_CONFIRMED","PAYMENT",payment.id,f"invoice={payment.invoice_number}; received={amount}; reference={data.external_reference}; status={payment.status}")
    db.commit();db.refresh(payment);return _payment_out(payment)

@app.post("/api/payments/{payment_id}/cancel",response_model=PaymentOut)
def cancel_payment_invoice(payment_id:int,data:PaymentCancelIn,db:DB,user:Annotated[User,Depends(require(Role.MARKETING))]):
    payment=db.scalar(select(Payment).where(Payment.id==payment_id).with_for_update())
    if not payment: raise HTTPException(404,"Invoice not found.")
    assigned_card(db,user,payment.job_card_id)
    if payment.status in ("PAID","CANCELLED") or Decimal(payment.amount_paid)>0: raise HTTPException(409,"Only an unpaid invoice can be cancelled.")
    payment.status="CANCELLED";payment.updated_at=datetime.now(timezone.utc)
    audit(db,user.id,"PAYMENT_INVOICE_CANCELLED","PAYMENT",payment.id,f"{payment.invoice_number}: {data.reason}")
    db.commit();db.refresh(payment);return _payment_out(payment)

@app.get("/api/departments",response_model=list[DepartmentOut])
def departments(db:DB,user:Annotated[User,Depends(current_user)]):
    return list(db.scalars(select(Department).where(Department.active.is_(True)).order_by(Department.name)))

@app.get("/api/activity-types",response_model=list[ActivityTypeOut])
def list_activity_types(db:DB,user:Annotated[User,Depends(current_user)],include_inactive:bool=False):
    if include_inactive and user.role!=Role.ADMIN:raise HTTPException(403,"Only Admin can view inactive activity types.")
    query=select(ActivityType).order_by(ActivityType.name)
    if not include_inactive:query=query.where(ActivityType.active.is_(True))
    return list(db.scalars(query))

@app.put("/api/activity-types/{code}",response_model=ActivityTypeOut)
def save_activity_type(code:str,data:ActivityTypeUpsert,db:DB,admin:Annotated[User,Depends(require(Role.ADMIN))]):
    if code.upper()!=data.code:raise HTTPException(422,"Activity type path code must match the body code.")
    row=db.scalar(select(ActivityType).where(ActivityType.code==data.code))
    conflict=db.scalar(select(ActivityType).where(ActivityType.name==data.name,ActivityType.code!=data.code))
    if conflict:raise HTTPException(409,"An activity type with that name already exists.")
    if not row:row=ActivityType(code=data.code,name=data.name,active=data.active);db.add(row)
    else:row.name=data.name;row.active=data.active
    db.flush();audit(db,admin.id,"ACTIVITY_TYPE_SAVED","ACTIVITY_TYPE",row.id,row.code);db.commit();db.refresh(row);return row

@app.get("/api/departments/{department_id}/action-assignees",response_model=list[ActionTransferUserOut])
def action_transfer_assignees(department_id:int,db:DB,user:Annotated[User,Depends(require(Role.ADMIN,Role.MANAGEMENT,Role.MARKETING))],role_filter:str|None=Query(default=None,alias="role")):
    if not db.scalar(select(Department.id).where(Department.id==department_id,Department.active.is_(True))): raise HTTPException(404,"Active department not found.")
    query=select(User).where(User.department_id==department_id,User.status==AccountStatus.ACTIVE)
    if role_filter: query=query.where(User.role==role_filter)
    return [ActionTransferUserOut(id=row.id,full_name=row.full_name,role=row.role.value) for row in db.scalars(query.order_by(User.full_name))]

@app.get("/api/search",response_model=list[GlobalSearchResult])
def global_search(db:DB,user:Annotated[User,Depends(current_user)],q:str=Query(min_length=2,max_length=100)):
    term=f"%{q.strip()}%";results=[]
    def add(kind:str,id_value:object,title:str,subtitle:str,status:str,href:str):
        results.append(GlobalSearchResult(kind=kind,id=str(id_value),title=title,subtitle=subtitle,status=status,href=href))
    tickets=select(Ticket).where(or_(Ticket.number.ilike(term),Ticket.title.ilike(term)))
    if user.role not in (Role.ADMIN,Role.MANAGEMENT):
        visible=[Ticket.created_by==user.id,Ticket.assigned_user_id==user.id]
        if user.department_id: visible.extend((Ticket.created_department_id==user.department_id,Ticket.assigned_department_id==user.department_id))
        tickets=tickets.where(or_(*visible))
    for row in db.scalars(tickets.order_by(Ticket.created_at.desc()).limit(5)):
        add("Ticket",row.id,f"{row.number} · {row.title}",row.ticket_type,row.status,"/marketing")
    cards=select(JobCard,MSME).join(MSME,MSME.id==JobCard.msme_id).where(JobCard.deleted_at.is_(None),or_(JobCard.number.ilike(term),MSME.name.ilike(term),MSME.msme_code.ilike(term),MSME.zed_id.ilike(term)))
    if user.role==Role.END_USER: cards=cards.join(Assignment,Assignment.job_card_id==JobCard.id).where(Assignment.user_id==user.id,Assignment.active.is_(True))
    elif user.role==Role.UNIT_REPRESENTATIVE: cards=cards.where(JobCard.unit_representative_id==user.id,JobCard.status=="PENDING_APPROVAL")
    elif user.role==Role.DIRECTOR: cards=cards.where(JobCard.director_id==user.id,JobCard.status=="PENDING_APPROVAL")
    for card,msme in db.execute(cards.order_by(JobCard.created_at.desc()).limit(5)):
        href="/admin/job-cards" if user.role==Role.ADMIN else ("/management/projects" if user.role==Role.MANAGEMENT else ("/approvals" if user.role in (Role.DIRECTOR,Role.UNIT_REPRESENTATIVE) else "/user/job-cards"))
        add("Job Card",card.id,f"{card.number} · {msme.name}",card.location,card.status,href)
    milestones=select(Milestone,JobCard).join(JobCard,JobCard.id==Milestone.job_card_id).where(JobCard.deleted_at.is_(None),or_(Milestone.code.ilike(term),Milestone.name.ilike(term)))
    if user.role==Role.END_USER: milestones=milestones.join(Assignment,Assignment.job_card_id==JobCard.id).where(Assignment.user_id==user.id,Assignment.active.is_(True))
    elif user.role==Role.UNIT_REPRESENTATIVE: milestones=milestones.where(JobCard.unit_representative_id==user.id,JobCard.status=="PENDING_APPROVAL")
    elif user.role==Role.DIRECTOR: milestones=milestones.where(JobCard.director_id==user.id,JobCard.status=="PENDING_APPROVAL")
    for row,card in db.execute(milestones.order_by(Milestone.id.desc()).limit(5)):
        href="/admin/job-cards" if user.role==Role.ADMIN else ("/management/projects" if user.role==Role.MANAGEMENT else ("/approvals" if user.role in (Role.DIRECTOR,Role.UNIT_REPRESENTATIVE) else "/user/job-cards"))
        add("Milestone",row.id,f"{card.number} · {row.code} · {row.name}","Project milestone",row.status,href)
    if user.role in (Role.ADMIN,Role.MANAGEMENT,Role.MARKETING):
        enquiries=select(Enquiry).where(or_(Enquiry.number.ilike(term),Enquiry.company_name.ilike(term),Enquiry.contact_person.ilike(term)))
        if user.role==Role.MARKETING: enquiries=enquiries.where(or_(Enquiry.created_by==user.id,Enquiry.assigned_marketing_user_id==user.id))
        for row in db.scalars(enquiries.order_by(Enquiry.updated_at.desc()).limit(5)):
            add("Enquiry",row.id,f"{row.number} · {row.company_name}",row.contact_person or "Enquiry",row.status,"/marketing")
    if user.role in (Role.ADMIN,Role.MANAGEMENT):
        for row in db.scalars(select(Department).where(Department.active.is_(True),or_(Department.name.ilike(term),Department.code.ilike(term))).order_by(Department.name).limit(5)):
            add("Department",row.id,row.name,row.code,"ACTIVE","/actions")
        for row in db.scalars(select(MSME).where(MSME.deleted_at.is_(None),or_(MSME.name.ilike(term),MSME.msme_code.ilike(term),MSME.zed_id.ilike(term))).order_by(MSME.name).limit(5)):
            add("Client",row.id,row.name,f"{row.msme_code} · {row.city or row.state or 'MSME'}",row.status,"/admin/msmes" if user.role==Role.ADMIN else "/management/projects")
        for row in db.scalars(select(User).where(User.role==Role.END_USER,User.status==AccountStatus.ACTIVE,User.full_name.ilike(term)).order_by(User.full_name).limit(5)):
            add("Consultant",row.id,row.full_name,row.consultant_id or row.email,row.status,"/admin/users" if user.role==Role.ADMIN else "/management/projects")
    return results[:25]

@app.post("/api/departments",response_model=DepartmentOut,status_code=201)
def create_department(data:DepartmentCreate,db:DB,user:Annotated[User,Depends(require(Role.ADMIN))]):
    if db.scalar(select(Department).where(or_(Department.code==data.code,Department.name==data.name))): raise HTTPException(409,"A department with that code or name already exists.")
    row=Department(**data.model_dump());db.add(row);db.flush();audit(db,user.id,"DEPARTMENT_CREATED","DEPARTMENT",row.id,row.code);db.commit();db.refresh(row);return row

@app.get("/api/tickets")
def list_tickets(db:DB,user:Annotated[User,Depends(current_user)],status_filter:str|None=Query(default=None,alias="status"),priority:str|None=None,ticket_type:str|None=None,search:str|None=None,assigned_to_me:bool=False,page:int=Query(default=1,ge=1),page_size:int=Query(default=25,ge=1,le=100)):
    stmt=select(Ticket)
    if user.role not in (Role.ADMIN,Role.MANAGEMENT):
        visible=[Ticket.created_by==user.id,Ticket.assigned_user_id==user.id]
        if user.department_id is not None: visible.extend((Ticket.created_department_id==user.department_id,Ticket.assigned_department_id==user.department_id))
        stmt=stmt.where(or_(*visible))
    if assigned_to_me: stmt=stmt.where(Ticket.assigned_user_id==user.id)
    if status_filter=="OVERDUE": stmt=stmt.where(Ticket.due_date<datetime.now(timezone.utc),Ticket.status.notin_(("COMPLETED","REJECTED","CANCELLED")))
    elif status_filter: stmt=stmt.where(Ticket.status==status_filter)
    if priority: stmt=stmt.where(Ticket.priority==priority)
    if ticket_type: stmt=stmt.where(Ticket.ticket_type==ticket_type)
    if search: stmt=stmt.where(or_(Ticket.number.ilike(f"%{search.strip()}%"),Ticket.title.ilike(f"%{search.strip()}%"),Ticket.description.ilike(f"%{search.strip()}%")))
    total=db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items=list(db.scalars(stmt.order_by(Ticket.created_at.desc()).offset((page-1)*page_size).limit(page_size)))
    enquiry_ids={item.enquiry_id for item in items if item.enquiry_id};card_ids={item.job_card_id for item in items if item.job_card_id}
    enquiry_numbers={item.id:item.number for item in db.scalars(select(Enquiry).where(Enquiry.id.in_(enquiry_ids)))} if enquiry_ids else {}
    card_numbers={item.id:item.number for item in db.scalars(select(JobCard).where(JobCard.id.in_(card_ids)))} if card_ids else {}
    return {"items":[_ticket_out(item,enquiry_numbers.get(item.enquiry_id),card_numbers.get(item.job_card_id)) for item in items],"total":total,"page":page,"page_size":page_size}

@app.post("/api/tickets",response_model=TicketOut,status_code=201)
def create_ticket(data:TicketCreate,db:DB,user:Annotated[User,Depends(current_user)]):
    if (data.assigned_user_id or data.assigned_department_id) and user.role not in (Role.ADMIN,Role.MANAGEMENT,Role.MARKETING): raise HTTPException(403,"Only an authorized coordinator can assign tickets.")
    if data.assigned_user_id:
        assignee=db.get(User,data.assigned_user_id)
        if not assignee or assignee.status!=AccountStatus.ACTIVE: raise HTTPException(422,"Assigned user must be active.")
        if data.assigned_department_id and assignee.department_id!=data.assigned_department_id: raise HTTPException(422,"Assigned user must belong to the assigned department.")
        if not data.assigned_department_id and assignee.department_id: data.assigned_department_id=assignee.department_id
    if data.assigned_department_id and not db.get(Department,data.assigned_department_id): raise HTTPException(422,"Assigned department does not exist.")
    parent=None
    if data.parent_ticket_id:
        parent=_get_visible_ticket(db,data.parent_ticket_id,user)
        if parent.id==data.parent_ticket_id: raise HTTPException(422,"A ticket cannot be its own parent.")
    if data.enquiry_id:
        enq=db.get(Enquiry,data.enquiry_id)
        if not enq or (user.role not in (Role.ADMIN,Role.MANAGEMENT,Role.MARKETING) and not _visible_enquiry(enq,user)): raise HTTPException(404,"Enquiry not found.")
    if data.job_card_id: assigned_card(db,user,data.job_card_id)
    if data.milestone_id:
        milestone=db.get(Milestone,data.milestone_id)
        if not milestone: raise HTTPException(422,"Milestone does not exist.")
        if data.job_card_id and milestone.job_card_id!=data.job_card_id: raise HTTPException(422,"The selected milestone belongs to another Job Card.")
        if not data.job_card_id: data.job_card_id=milestone.job_card_id
        assigned_card(db,user,data.job_card_id)
    row=Ticket(**data.model_dump(),number=next_business_number(db,"ticket"),created_by=user.id,created_department_id=user.department_id,status="OPEN")
    db.add(row);db.flush()
    if parent: db.add(TicketRelationship(ticket_id=row.id,related_ticket_id=parent.id,relationship_type="PARENT",created_by=user.id))
    if row.enquiry_id: db.add(TicketRelationship(ticket_id=row.id,related_enquiry_id=row.enquiry_id,relationship_type="REFERENCE",created_by=user.id))
    if row.job_card_id: db.add(TicketRelationship(ticket_id=row.id,related_job_card_id=row.job_card_id,relationship_type="REFERENCE",created_by=user.id))
    audit(db,user.id,"TICKET_CREATED","TICKET",None,row.number,entity_ref=str(row.id))
    if row.assigned_user_id and row.assigned_user_id!=user.id: notify(db,row.assigned_user_id,f"Ticket {row.number} assigned to you.",f"/tickets/{row.id}")
    db.commit();db.refresh(row);return _ticket_out(row,db.get(Enquiry,row.enquiry_id).number if row.enquiry_id else None,db.get(JobCard,row.job_card_id).number if row.job_card_id else None)

@app.get("/api/tickets/{ticket_id}",response_model=TicketOut)
def get_ticket(ticket_id:UUID,db:DB,user:Annotated[User,Depends(current_user)]):
    row=_get_visible_ticket(db,ticket_id,user)
    return _ticket_out(row,db.get(Enquiry,row.enquiry_id).number if row.enquiry_id else None,db.get(JobCard,row.job_card_id).number if row.job_card_id else None)

@app.patch("/api/tickets/{ticket_id}",response_model=TicketOut)
def update_ticket(ticket_id:UUID,data:TicketUpdate,db:DB,user:Annotated[User,Depends(current_user)]):
    row=_get_visible_ticket(db,ticket_id,user)
    if user.role not in (Role.ADMIN,Role.MANAGEMENT) and row.assigned_user_id!=user.id and row.created_by!=user.id and not _can_manage_crm(user): raise HTTPException(403,"Only the ticket owner or assignee may update it.")
    changes=data.model_dump(exclude_unset=True)
    if ({"assigned_user_id","assigned_department_id"}&changes.keys()) and user.role not in (Role.ADMIN,Role.MANAGEMENT,Role.MARKETING): raise HTTPException(403,"Only an authorized coordinator can reassign tickets.")
    if changes.get("assigned_user_id"):
        assignee=db.get(User,changes["assigned_user_id"])
        dept=changes.get("assigned_department_id",row.assigned_department_id)
        if not assignee or assignee.status!=AccountStatus.ACTIVE: raise HTTPException(422,"Assigned user must be active.")
        if dept and assignee.department_id!=dept: raise HTTPException(422,"Assigned user must belong to the assigned department.")
        if not dept and assignee.department_id: changes["assigned_department_id"]=assignee.department_id
    new_status=changes.get("status")
    if new_status in ("RETURNED","REJECTED") and len((changes.get("remarks") or "").strip())<3: raise HTTPException(422,"A reason in remarks is required for returning or rejecting a ticket.")
    if "assigned_department_id" in changes and changes["assigned_department_id"] and not db.get(Department,changes["assigned_department_id"]): raise HTTPException(422,"Assigned department does not exist.")
    if changes.get("enquiry_id"):
        enquiry=db.get(Enquiry,changes["enquiry_id"])
        if not enquiry or (user.role not in (Role.ADMIN,Role.MANAGEMENT,Role.MARKETING) and not _visible_enquiry(enquiry,user)): raise HTTPException(404,"Enquiry not found.")
    target_card_id=changes.get("job_card_id",row.job_card_id)
    if target_card_id: assigned_card(db,user,target_card_id)
    if changes.get("milestone_id"):
        milestone=db.get(Milestone,changes["milestone_id"])
        if not milestone: raise HTTPException(422,"Milestone does not exist.")
        if target_card_id and milestone.job_card_id!=target_card_id: raise HTTPException(422,"The selected milestone belongs to another Job Card.")
        if not target_card_id: target_card_id=milestone.job_card_id;changes["job_card_id"]=target_card_id;assigned_card(db,user,target_card_id)
    before={key:getattr(row,key) for key in changes}
    for key,value in changes.items(): setattr(row,key,value)
    if row.job_card_id and row.job_card_id!=before.get("job_card_id"):
        if not db.scalar(select(TicketRelationship.id).where(TicketRelationship.ticket_id==row.id,TicketRelationship.related_job_card_id==row.job_card_id)):
            db.add(TicketRelationship(ticket_id=row.id,related_job_card_id=row.job_card_id,relationship_type="REFERENCE",created_by=user.id))
    if new_status=="COMPLETED": row.completed_at=datetime.now(timezone.utc)
    elif new_status and new_status!="COMPLETED": row.completed_at=None
    row.updated_at=datetime.now(timezone.utc)
    audit(db,user.id,"TICKET_UPDATED","TICKET",None,f"{row.number}: {before} -> {changes}",entity_ref=str(row.id))
    if row.assigned_user_id and row.assigned_user_id!=user.id: notify(db,row.assigned_user_id,f"Ticket {row.number} was updated.",f"/tickets/{row.id}")
    db.commit();db.refresh(row);return _ticket_out(row,db.get(Enquiry,row.enquiry_id).number if row.enquiry_id else None,db.get(JobCard,row.job_card_id).number if row.job_card_id else None)

@app.get("/api/tickets/{ticket_id}/relationships",response_model=list[TicketRelationshipOut])
def ticket_relationships(ticket_id:UUID,db:DB,user:Annotated[User,Depends(current_user)]):
    _get_visible_ticket(db,ticket_id,user)
    return list(db.scalars(select(TicketRelationship).where(TicketRelationship.ticket_id==ticket_id).order_by(TicketRelationship.created_at.desc())))

@app.get("/api/tickets/{ticket_id}/attachments",response_model=list[TicketAttachmentOut])
def list_ticket_attachments(ticket_id:UUID,db:DB,user:Annotated[User,Depends(current_user)]):
    _get_visible_ticket(db,ticket_id,user)
    return list(db.scalars(select(TicketAttachment).where(TicketAttachment.ticket_id==ticket_id).order_by(TicketAttachment.uploaded_at.desc()).limit(100)))

@app.post("/api/tickets/{ticket_id}/attachments",response_model=TicketAttachmentOut,status_code=201)
def upload_ticket_attachment(ticket_id:UUID,data:RecordFileUpload,db:DB,user:Annotated[User,Depends(current_user)]):
    ticket=_get_visible_ticket(db,ticket_id,user)
    key,size=_save_encoded_file(data.file_name,data.mime_type,data.content_base64)
    row=TicketAttachment(ticket_id=ticket_id,file_name=data.file_name,storage_key=key,file_size=size,mime_type=data.mime_type,uploaded_by=user.id)
    db.add(row);db.flush();audit(db,user.id,"TICKET_ATTACHMENT_UPLOADED","TICKET",None,f"{ticket.number}: {data.file_name}",entity_ref=str(ticket.id));db.commit();db.refresh(row);return row

@app.get("/api/ticket-attachments/{attachment_id}/download")
def download_ticket_attachment(attachment_id:int,db:DB,user:Annotated[User,Depends(current_user)]):
    row=db.get(TicketAttachment,attachment_id)
    if not row: raise HTTPException(404,"Attachment not found.")
    _get_visible_ticket(db,row.ticket_id,user)
    return _download_attachment(row.file_name,row.mime_type,row.storage_key)

@app.post("/api/tickets/{ticket_id}/relationships",response_model=TicketRelationshipOut,status_code=201)
def create_ticket_relationship(ticket_id:UUID,data:TicketRelationshipCreate,db:DB,user:Annotated[User,Depends(current_user)]):
    _get_visible_ticket(db,ticket_id,user)
    target=None
    try:
        if data.related_type=="TICKET":
            related_id=UUID(data.related_id); target=_get_visible_ticket(db,related_id,user)
            if related_id==ticket_id: raise HTTPException(422,"A ticket cannot be related to itself.")
            target_values={"related_ticket_id":related_id}
        elif data.related_type=="ENQUIRY":
            related_id=UUID(data.related_id);target=db.get(Enquiry,related_id)
            if not target or (user.role not in (Role.ADMIN,Role.MANAGEMENT,Role.MARKETING) and not _visible_enquiry(target,user)): raise HTTPException(404,"Enquiry not found.")
            target_values={"related_enquiry_id":related_id}
        else:
            related_id=int(data.related_id);target=db.get(JobCard,related_id)
            if not target: raise HTTPException(404,"Job Card not found.")
            assigned_card(db,user,related_id)
            target_values={"related_job_card_id":related_id}
    except ValueError: raise HTTPException(422,"Related record ID is invalid.")
    existing=db.scalar(select(TicketRelationship).where(TicketRelationship.ticket_id==ticket_id,TicketRelationship.relationship_type==data.relationship_type,*[getattr(TicketRelationship,k)==v for k,v in target_values.items()]))
    if existing: raise HTTPException(409,"That relationship already exists.")
    row=TicketRelationship(ticket_id=ticket_id,relationship_type=data.relationship_type,created_by=user.id,**target_values);db.add(row);audit(db,user.id,"TICKET_RELATIONSHIP_CREATED","TICKET",None,f"{ticket_id} {data.relationship_type}",entity_ref=str(ticket_id))
    try: db.commit()
    except IntegrityError: db.rollback();raise HTTPException(409,"That relationship already exists.")
    db.refresh(row);return row

@app.get("/api/enquiries")
def list_enquiries(db:DB,user:Annotated[User,Depends(current_user)],status_filter:str|None=Query(default=None,alias="status"),search:str|None=None,assigned_to_me:bool=False,page:int=Query(default=1,ge=1),page_size:int=Query(default=25,ge=1,le=100)):
    if user.role not in (Role.ADMIN,Role.MANAGEMENT,Role.MARKETING): raise HTTPException(403,"You do not have permission to access enquiries.")
    stmt=select(Enquiry)
    if user.role==Role.MARKETING: stmt=stmt.where(or_(Enquiry.created_by==user.id,Enquiry.assigned_marketing_user_id==user.id))
    if assigned_to_me: stmt=stmt.where(Enquiry.assigned_marketing_user_id==user.id)
    if status_filter: stmt=stmt.where(Enquiry.status==status_filter)
    if search: stmt=stmt.where(or_(Enquiry.number.ilike(f"%{search.strip()}%"),Enquiry.company_name.ilike(f"%{search.strip()}%"),Enquiry.contact_person.ilike(f"%{search.strip()}%"),Enquiry.email.ilike(f"%{search.strip()}%"),Enquiry.phone.ilike(f"%{search.strip()}%")))
    total=db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    return {"items":list(db.scalars(stmt.order_by(Enquiry.updated_at.desc()).offset((page-1)*page_size).limit(page_size))),"total":total,"page":page,"page_size":page_size}

@app.get("/api/enquiries/matches")
def enquiry_matches(db:DB,user:Annotated[User,Depends(require(Role.ADMIN,Role.MARKETING,Role.MANAGEMENT))],company_name:str|None=None,email:str|None=None,phone:str|None=None):
    conditions=[]
    if company_name and len(company_name.strip())>1: conditions.append(func.lower(Enquiry.company_name)==company_name.strip().lower())
    if email: conditions.append(func.lower(Enquiry.email)==email.strip().lower())
    if phone: conditions.append(Enquiry.phone==phone.strip())
    if not conditions: raise HTTPException(422,"Provide company name, email, or phone to search for possible duplicates.")
    stmt=select(Enquiry).where(or_(*conditions))
    if user.role==Role.MARKETING: stmt=stmt.where(or_(Enquiry.created_by==user.id,Enquiry.assigned_marketing_user_id==user.id))
    return list(db.scalars(stmt.order_by(Enquiry.created_at.desc()).limit(20)))

@app.get("/api/marketing/assignees")
def marketing_assignees(db:DB,user:Annotated[User,Depends(require(Role.ADMIN,Role.MARKETING))]):
    return [{"id":row.id,"full_name":row.full_name} for row in db.scalars(select(User).where(User.role==Role.MARKETING,User.status==AccountStatus.ACTIVE).order_by(User.full_name))]

@app.post("/api/enquiries",response_model=EnquiryOut,status_code=201)
def create_enquiry(data:EnquiryCreate,db:DB,user:Annotated[User,Depends(require(Role.ADMIN,Role.MARKETING))]):
    values=data.model_dump()
    if values.get("msme_id") and not db.get(MSME,values["msme_id"]): raise HTTPException(422,"MSME does not exist.")
    marketer_id=values.get("assigned_marketing_user_id") or (user.id if user.role==Role.MARKETING else None)
    if marketer_id:
        marketer=db.get(User,marketer_id)
        if not marketer or marketer.role!=Role.MARKETING or marketer.status!=AccountStatus.ACTIVE: raise HTTPException(422,"Assigned marketer must be an active Marketing user.")
    values["assigned_marketing_user_id"]=marketer_id
    source=None
    if values.get("source_ticket_id"):
        source=_get_visible_ticket(db,values["source_ticket_id"],user)
        if source.enquiry_id: raise HTTPException(409,"This ticket already has an enquiry linked.")
    row=Enquiry(**values,number=next_business_number(db,"enquiry"),created_by=user.id);db.add(row)
    try: db.flush()
    except IntegrityError:
        db.rollback();raise HTTPException(409,"The source ticket already has an enquiry linked.")
    if source:
        source.enquiry_id=row.id
        db.add(TicketRelationship(ticket_id=source.id,related_enquiry_id=row.id,relationship_type="CREATED_FROM",created_by=user.id))
    audit(db,user.id,"ENQUIRY_CREATED","ENQUIRY",None,row.number,entity_ref=str(row.id))
    if marketer_id and marketer_id!=user.id: notify(db,marketer_id,f"New enquiry {row.number} assigned to you.",f"/marketing/enquiries/{row.id}")
    db.commit();db.refresh(row);return row

@app.get("/api/enquiries/{enquiry_id}",response_model=EnquiryOut)
def get_enquiry(enquiry_id:UUID,db:DB,user:Annotated[User,Depends(current_user)]):
    row=db.get(Enquiry,enquiry_id)
    if not row or not _visible_enquiry(row,user): raise HTTPException(404,"Enquiry not found.")
    return row

@app.patch("/api/enquiries/{enquiry_id}",response_model=EnquiryOut)
def update_enquiry(enquiry_id:UUID,data:EnquiryUpdate,db:DB,user:Annotated[User,Depends(require(Role.ADMIN,Role.MARKETING))]):
    row=db.get(Enquiry,enquiry_id)
    if not row or not _visible_enquiry(row,user): raise HTTPException(404,"Enquiry not found.")
    changes=data.model_dump(exclude_unset=True)
    if "assigned_marketing_user_id" in changes and changes["assigned_marketing_user_id"] is not None:
        assignee=db.get(User,changes["assigned_marketing_user_id"])
        if not assignee or assignee.role!=Role.MARKETING or assignee.status!=AccountStatus.ACTIVE: raise HTTPException(422,"Assigned marketer must be an active Marketing user.")
    if changes.get("msme_id") and not db.get(MSME,changes["msme_id"]): raise HTTPException(422,"MSME does not exist.")
    before={key:getattr(row,key) for key in changes}
    for key,value in changes.items(): setattr(row,key,value)
    row.updated_at=datetime.now(timezone.utc);audit(db,user.id,"ENQUIRY_UPDATED","ENQUIRY",None,f"{row.number}: {before} -> {changes}",entity_ref=str(row.id))
    if row.assigned_marketing_user_id and row.assigned_marketing_user_id!=user.id: notify(db,row.assigned_marketing_user_id,f"Enquiry {row.number} was updated.",f"/marketing/enquiries/{row.id}")
    db.commit();db.refresh(row);return row

@app.get("/api/enquiries/{enquiry_id}/documents",response_model=list[EnquiryDocumentOut])
def list_enquiry_documents(enquiry_id:UUID,db:DB,user:Annotated[User,Depends(current_user)]):
    enquiry=db.get(Enquiry,enquiry_id)
    if not enquiry or not _visible_enquiry(enquiry,user): raise HTTPException(404,"Enquiry not found.")
    return list(db.scalars(select(EnquiryDocument).where(EnquiryDocument.enquiry_id==enquiry_id).order_by(EnquiryDocument.uploaded_at.desc()).limit(100)))

@app.post("/api/enquiries/{enquiry_id}/documents",response_model=EnquiryDocumentOut,status_code=201)
def upload_enquiry_document(enquiry_id:UUID,data:RecordFileUpload,db:DB,user:Annotated[User,Depends(require(Role.ADMIN,Role.MARKETING))]):
    enquiry=db.get(Enquiry,enquiry_id)
    if not enquiry or not _visible_enquiry(enquiry,user): raise HTTPException(404,"Enquiry not found.")
    if not data.document_type: raise HTTPException(422,"Document type is required.")
    key,size=_save_encoded_file(data.file_name,data.mime_type,data.content_base64)
    row=EnquiryDocument(enquiry_id=enquiry_id,file_name=data.file_name,storage_key=key,file_size=size,mime_type=data.mime_type,document_type=data.document_type,remarks=data.remarks,uploaded_by=user.id)
    db.add(row);db.flush();audit(db,user.id,"ENQUIRY_DOCUMENT_UPLOADED","ENQUIRY",None,f"{enquiry.number}: {data.document_type} {data.file_name}",entity_ref=str(enquiry.id));db.commit();db.refresh(row);return row

@app.get("/api/enquiry-documents/{document_id}/download")
def download_enquiry_document(document_id:int,db:DB,user:Annotated[User,Depends(current_user)]):
    row=db.get(EnquiryDocument,document_id)
    if not row: raise HTTPException(404,"Document not found.")
    enquiry=db.get(Enquiry,row.enquiry_id)
    if not enquiry or not _visible_enquiry(enquiry,user): raise HTTPException(404,"Document not found.")
    return _download_attachment(row.file_name,row.mime_type,row.storage_key)

@app.get("/api/enquiries/{enquiry_id}/follow-ups",response_model=list[EnquiryFollowUpOut])
def list_enquiry_followups(enquiry_id:UUID,db:DB,user:Annotated[User,Depends(current_user)]):
    row=db.get(Enquiry,enquiry_id)
    if not row or not _visible_enquiry(row,user): raise HTTPException(404,"Enquiry not found.")
    return list(db.scalars(select(EnquiryFollowUp).where(EnquiryFollowUp.enquiry_id==enquiry_id).order_by(EnquiryFollowUp.follow_up_date.desc())))

@app.get("/api/marketing/follow-ups")
def marketing_followup_queue(db:DB,user:Annotated[User,Depends(require(Role.ADMIN,Role.MARKETING))],bucket:str=Query(default="all",pattern="^(all|upcoming|overdue)$"),search:str|None=None,page:int=Query(default=1,ge=1),page_size:int=Query(default=50,ge=1,le=100)):
    today=datetime.now(timezone.utc).date()
    stmt=select(EnquiryFollowUp,Enquiry).join(Enquiry,Enquiry.id==EnquiryFollowUp.enquiry_id).where(EnquiryFollowUp.next_follow_up_date.is_not(None),EnquiryFollowUp.status!="CANCELLED",Enquiry.status.not_in(("CONVERTED","LOST","CANCELLED")))
    if user.role==Role.MARKETING: stmt=stmt.where(or_(Enquiry.created_by==user.id,Enquiry.assigned_marketing_user_id==user.id))
    if bucket=="overdue": stmt=stmt.where(EnquiryFollowUp.next_follow_up_date<datetime.combine(today,datetime.min.time(),tzinfo=timezone.utc))
    elif bucket=="upcoming": stmt=stmt.where(EnquiryFollowUp.next_follow_up_date>=datetime.combine(today,datetime.min.time(),tzinfo=timezone.utc))
    if search:
        term=f"%{search.strip()}%"
        stmt=stmt.where(or_(Enquiry.number.ilike(term),Enquiry.company_name.ilike(term),Enquiry.contact_person.ilike(term),EnquiryFollowUp.notes.ilike(term)))
    total=db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows=db.execute(stmt.order_by(EnquiryFollowUp.next_follow_up_date.asc()).offset((page-1)*page_size).limit(page_size)).all()
    items=[]
    for followup,enquiry in rows:
        items.append({"id":followup.id,"enquiry_id":str(enquiry.id),"enquiry_number":enquiry.number,"company_name":enquiry.company_name,"contact_person":enquiry.contact_person,"phone":enquiry.phone,"status":enquiry.status,"follow_up_type":followup.follow_up_type,"notes":followup.notes,"next_follow_up_date":followup.next_follow_up_date,"responsible_user_id":followup.responsible_user_id,"overdue":followup.next_follow_up_date.date()<today})
    return {"items":items,"total":total,"page":page,"page_size":page_size}

@app.get("/api/enquiries/{enquiry_id}/related-records")
def enquiry_related_records(enquiry_id:UUID,db:DB,user:Annotated[User,Depends(current_user)]):
    enquiry=db.get(Enquiry,enquiry_id)
    if not enquiry or not _visible_enquiry(enquiry,user): raise HTTPException(404,"Enquiry not found.")
    tickets=list(db.scalars(select(Ticket).where(Ticket.enquiry_id==enquiry_id).order_by(Ticket.created_at.desc()).limit(100)))
    cards=list(db.scalars(select(JobCard).where(JobCard.source_enquiry_id==enquiry_id).order_by(JobCard.created_at.desc())))
    return {"tickets":[_ticket_out(ticket,enquiry.number,db.get(JobCard,ticket.job_card_id).number if ticket.job_card_id else None) for ticket in tickets],"job_cards":[JobCardOut.model_validate(card).model_dump() for card in cards]}

@app.post("/api/enquiries/{enquiry_id}/link-job-card",response_model=JobCardOut)
def link_enquiry_job_card(enquiry_id:UUID,data:EnquiryLinkJobCard,db:DB,user:Annotated[User,Depends(require(Role.ADMIN,Role.MARKETING))]):
    enquiry=db.get(Enquiry,enquiry_id)
    if not enquiry or not _visible_enquiry(enquiry,user): raise HTTPException(404,"Enquiry not found.")
    card=db.get(JobCard,data.job_card_id)
    if not card or card.deleted_at: raise HTTPException(404,"Job Card not found.")
    mutable_card(card)
    if enquiry.status not in ("QUALIFIED","FINALISED","CONVERTED"): raise HTTPException(409,"Only a qualified or finalised enquiry can be linked to a Job Card.")
    if card.source_enquiry_id and card.source_enquiry_id!=enquiry.id: raise HTTPException(409,"This Job Card is already linked to a different enquiry.")
    other=db.scalar(select(JobCard.id).where(JobCard.source_enquiry_id==enquiry.id,JobCard.id!=card.id))
    if other: raise HTTPException(409,"This enquiry is already linked to another Job Card.")
    if db.scalar(select(Ticket.id).where(Ticket.enquiry_id==enquiry.id,Ticket.job_card_id.is_not(None),Ticket.job_card_id!=card.id)): raise HTTPException(409,"A related ticket is already linked to a different Job Card.")
    for related_ticket in db.scalars(select(Ticket).where(Ticket.enquiry_id==enquiry.id)):
        related_ticket.job_card_id=card.id
        if not db.scalar(select(TicketRelationship.id).where(TicketRelationship.ticket_id==related_ticket.id,TicketRelationship.related_job_card_id==card.id)):
            db.add(TicketRelationship(ticket_id=related_ticket.id,related_job_card_id=card.id,relationship_type="REFERENCE",created_by=user.id))
    if enquiry.source_ticket_id:
        ticket=db.get(Ticket,enquiry.source_ticket_id)
        if ticket and ticket.job_card_id and ticket.job_card_id!=card.id: raise HTTPException(409,"The source ticket is already linked to another Job Card.")
        if ticket:
            ticket.job_card_id=card.id
            if not db.scalar(select(TicketRelationship.id).where(TicketRelationship.ticket_id==ticket.id,TicketRelationship.related_job_card_id==card.id)):
                db.add(TicketRelationship(ticket_id=ticket.id,related_job_card_id=card.id,relationship_type="CREATED_FROM",created_by=user.id))
    card.source_enquiry_id=enquiry.id;enquiry.status="CONVERTED";enquiry.updated_at=datetime.now(timezone.utc)
    audit(db,user.id,"ENQUIRY_LINKED_TO_JOB_CARD","ENQUIRY",None,f"{enquiry.number} -> {card.number}",entity_ref=str(enquiry.id))
    db.commit();db.refresh(card);return card

@app.post("/api/enquiries/{enquiry_id}/follow-ups",response_model=EnquiryFollowUpOut,status_code=201)
def create_enquiry_followup(enquiry_id:UUID,data:EnquiryFollowUpCreate,db:DB,user:Annotated[User,Depends(require(Role.ADMIN,Role.MARKETING))]):
    enquiry=db.get(Enquiry,enquiry_id)
    if not enquiry or not _visible_enquiry(enquiry,user): raise HTTPException(404,"Enquiry not found.")
    values=data.model_dump();attachment_name=values.pop("attachment_name");attachment_mime_type=values.pop("attachment_mime_type");attachment_base64=values.pop("attachment_base64")
    if any((attachment_name,attachment_mime_type,attachment_base64)) and not all((attachment_name,attachment_mime_type,attachment_base64)): raise HTTPException(422,"Follow-up attachment name, type, and content are all required.")
    attachment_key=None
    if attachment_name: attachment_key,_=_save_encoded_file(attachment_name,attachment_mime_type,attachment_base64)
    row=EnquiryFollowUp(enquiry_id=enquiry_id,responsible_user_id=user.id,attachment_name=attachment_name,attachment_mime_type=attachment_mime_type,attachment_key=attachment_key,**values);db.add(row)
    if enquiry.status in ("NEW","CONTACTED"):
        enquiry.status="FOLLOW_UP" if data.next_follow_up_date else ("CONTACTED" if enquiry.status=="NEW" else enquiry.status)
    enquiry.follow_up_date=data.next_follow_up_date.date() if data.next_follow_up_date else None;enquiry.updated_at=datetime.now(timezone.utc)
    audit(db,user.id,"ENQUIRY_FOLLOW_UP_ADDED","ENQUIRY",None,enquiry.number,entity_ref=str(enquiry.id))
    if enquiry.assigned_marketing_user_id and enquiry.assigned_marketing_user_id!=user.id: notify(db,enquiry.assigned_marketing_user_id,f"Follow-up recorded for enquiry {enquiry.number}.",f"/marketing/enquiries/{enquiry.id}")
    db.commit();db.refresh(row);return row

@app.get("/api/enquiry-follow-ups/{followup_id}/attachment")
def download_enquiry_followup_attachment(followup_id:int,db:DB,user:Annotated[User,Depends(current_user)]):
    row=db.get(EnquiryFollowUp,followup_id)
    if not row or not row.attachment_key: raise HTTPException(404,"Follow-up attachment not found.")
    enquiry=db.get(Enquiry,row.enquiry_id)
    if not enquiry or not _visible_enquiry(enquiry,user): raise HTTPException(404,"Follow-up attachment not found.")
    return _download_attachment(row.attachment_name,row.attachment_mime_type,row.attachment_key)

@app.on_event("startup")
def bootstrap():
    # Migrations own deployed schemas; create_all and demo identities are local/test only.
    if ENV in ("development","test"):
        Base.metadata.create_all(engine)
        seed()
    elif ENV=="production":
        disable_demo_accounts()

@app.post("/api/auth/login",response_model=TokenOut)
def login(data:Login,db:DB):
    user=db.scalar(select(User).where(User.email==str(data.email).lower()))
    if not user:
        passwords.hash(data.password); audit(db,None,"LOGIN_FAILED","USER",None,"Unknown email"); db.commit(); raise HTTPException(401,"Invalid email or password.")
    if user.status!=AccountStatus.ACTIVE:
        audit(db,user.id,"LOGIN_DENIED","USER",user.id,user.status.value); db.commit(); raise HTTPException(403,f"Your account is {user.status.value.lower().replace('_',' ')}. Please contact the administrator.")
    try: valid=passwords.verify(user.password_hash,data.password)
    except Exception: valid=False
    if not valid:
        user.failed_attempts+=1
        if user.failed_attempts>=5: user.status=AccountStatus.LOCKED
        audit(db,user.id,"LOGIN_FAILED","USER",user.id,f"failed_attempts={user.failed_attempts}"); db.commit(); raise HTTPException(401,"Invalid email or password.")
    user.failed_attempts=0; user.last_login_at=datetime.now(timezone.utc); token=issue_session(db,user); audit(db,user.id,"LOGIN","USER",user.id); db.commit(); return token

@app.post("/api/auth/register",response_model=UserOut,status_code=201)
def register(data:Register,db:DB):
    if data.password!=data.confirm_password: raise HTTPException(422,"Passwords do not match.")
    check_password_rules(data.password)
    if db.scalar(select(User).where(User.email==data.email)): raise HTTPException(409,"An account with that email already exists.")
    user=User(full_name=data.full_name,email=data.email,mobile=data.mobile,consultant_id=data.consultant_id,role=Role.END_USER,status=AccountStatus.PENDING_ACTIVATION,password_hash=passwords.hash(data.password)); db.add(user); db.flush(); audit(db,user.id,"USER_REGISTERED","USER",user.id); db.commit(); db.refresh(user); return user

@app.post("/api/auth/refresh",response_model=TokenOut)
def refresh(data:RefreshRequest,db:DB):
    session=db.scalar(select(AuthSession).where(AuthSession.refresh_token_hash==token_hash(data.refresh_token)))
    expiry=session.refresh_expires_at.replace(tzinfo=timezone.utc) if session and session.refresh_expires_at and session.refresh_expires_at.tzinfo is None else (session.refresh_expires_at if session else None)
    if not session or session.revoked or not expiry or expiry<datetime.now(timezone.utc): raise HTTPException(401,"Your session has expired. Please sign in again.")
    user=db.get(User,session.user_id)
    if not user or user.status!=AccountStatus.ACTIVE: raise HTTPException(403,"Your account is not active.")
    session.revoked=True; token=issue_session(db,user); audit(db,user.id,"SESSION_REFRESHED","USER",user.id); db.commit(); return token

@app.post("/api/auth/forgot-password")
def forgot_password(data:ForgotPassword,db:DB):
    user=db.scalar(select(User).where(User.email==data.email))
    response={"success":True,"message":"If an account exists, a password-reset link has been sent."}
    if not user: return response
    raw=secrets.token_urlsafe(48); user.reset_token_hash=token_hash(raw); user.reset_expires_at=datetime.now(timezone.utc)+timedelta(minutes=30); audit(db,user.id,"PASSWORD_RESET_REQUESTED","USER",user.id); db.commit()
    if ENV in ("development","test"):
        response["development_reset_token"]=raw
    else:
        public_url=os.getenv("PUBLIC_APP_URL","http://localhost:5173").rstrip("/")
        try: send_password_reset(user.email,f"{public_url}/reset-password?token={raw}")
        except Exception:
            logger.exception("Password reset mail delivery failed")
            audit(db,user.id,"PASSWORD_RESET_EMAIL_FAILED","USER",user.id,"SMTP delivery failed");db.commit()
    return response

@app.post("/api/auth/reset-password")
def reset_password(data:ResetPassword,db:DB):
    if data.new_password!=data.confirm_password: raise HTTPException(422,"Passwords do not match.")
    check_password_rules(data.new_password); user=db.scalar(select(User).where(User.reset_token_hash==token_hash(data.token)))
    expiry=user.reset_expires_at.replace(tzinfo=timezone.utc) if user and user.reset_expires_at and user.reset_expires_at.tzinfo is None else (user.reset_expires_at if user else None)
    if not user or not expiry or expiry<datetime.now(timezone.utc): raise HTTPException(400,"The password-reset token is invalid or expired.")
    user.password_hash=passwords.hash(data.new_password); user.reset_token_hash=None; user.reset_expires_at=None
    for session in db.scalars(select(AuthSession).where(AuthSession.user_id==user.id)): session.revoked=True
    audit(db,user.id,"PASSWORD_RESET","USER",user.id); db.commit(); return {"success":True,"message":"Password reset successfully. Please sign in."}

@app.post("/api/auth/logout",status_code=204)
def logout(db:DB, credentials:Annotated[HTTPAuthorizationCredentials,Depends(HTTPBearer())]):
    claims=decode_token(credentials.credentials); session=db.get(AuthSession,claims["sid"])
    if session: session.revoked=True; audit(db,int(claims["sub"]),"LOGOUT","USER",int(claims["sub"])); db.commit()

@app.get("/api/auth/me",response_model=UserOut)
def me(user:Annotated[User,Depends(current_user)]): return user

@app.patch("/api/auth/me",response_model=UserOut)
def update_profile(data:ProfileUpdate,db:DB,user:Annotated[User,Depends(current_user)]):
    user.full_name=data.full_name; user.mobile=data.mobile; user.consultant_id=data.consultant_id; audit(db,user.id,"PROFILE_UPDATED","USER",user.id); db.commit(); db.refresh(user); return user

@app.post("/api/auth/change-password")
def change_password(data:ChangePassword,db:DB,user:Annotated[User,Depends(current_user)]):
    if data.new_password!=data.confirm_password: raise HTTPException(422,"Passwords do not match.")
    try: valid=passwords.verify(user.password_hash,data.current_password)
    except Exception: valid=False
    if not valid: raise HTTPException(400,"Current password is incorrect.")
    check_password_rules(data.new_password); user.password_hash=passwords.hash(data.new_password)
    for session in db.scalars(select(AuthSession).where(AuthSession.user_id==user.id)): session.revoked=True
    audit(db,user.id,"PASSWORD_CHANGED","USER",user.id); db.commit(); return {"success":True,"message":"Password changed. Please sign in again."}

@app.post("/api/users",response_model=UserOut,status_code=201)
def create_user(data:UserCreate,db:DB,admin:Annotated[User,Depends(require(Role.ADMIN))]):
    if db.scalar(select(User).where(User.email==str(data.email).lower())): raise HTTPException(409,"Email already exists.")
    check_password_rules(data.password)
    if data.role in (Role.ADMIN,Role.MANAGEMENT) and admin.role!=Role.ADMIN: raise HTTPException(403,"Only Admin can create privileged accounts.")
    if data.department_id and not db.get(Department,data.department_id): raise HTTPException(422,"Department does not exist.")
    user=User(full_name=data.full_name,email=str(data.email).lower(),mobile=data.mobile,consultant_id=data.consultant_id,role=data.role,department_id=data.department_id,status=AccountStatus.ACTIVE if data.role!=Role.END_USER else AccountStatus.PENDING_ACTIVATION,password_hash=passwords.hash(data.password)); db.add(user); db.flush(); audit(db,admin.id,"USER_CREATED","USER",user.id,user.email); db.commit(); db.refresh(user); return user

@app.get("/api/users",response_model=list[UserOut])
def list_users(db:DB,admin:Annotated[User,Depends(require(Role.ADMIN))]): return list(db.scalars(select(User).order_by(User.full_name)))

@app.patch("/api/users/{user_id}",response_model=UserOut)
def update_user(user_id:int,data:UserAdminUpdate,db:DB,admin:Annotated[User,Depends(require(Role.ADMIN))]):
    user=db.get(User,user_id)
    if not user: raise HTTPException(404,"User not found.")
    updates=data.model_dump(exclude_unset=True)
    if updates.get("email"):
        updates["email"]=updates["email"].strip().lower()
        if "@" not in updates["email"] or db.scalar(select(User.id).where(User.email==updates["email"],User.id!=user_id)): raise HTTPException(409,"Enter a valid, unused email address.")
    if updates.get("department_id") and not db.get(Department,updates["department_id"]): raise HTTPException(422,"Department does not exist.")
    if not updates: return user
    if user.id==admin.id and "role" in updates and updates["role"]!=Role.ADMIN: raise HTTPException(422,"You cannot remove your own Admin role.")
    changes=[]
    for field,value in updates.items():
        previous=getattr(user,field)
        if previous!=value:
            setattr(user,field,value); changes.append(f"{field}:{previous}->{value}")
    if "role" in updates:
        for session in db.scalars(select(AuthSession).where(AuthSession.user_id==user.id)): session.revoked=True
    if changes: audit(db,admin.id,"USER_UPDATED","USER",user.id,"; ".join(changes))
    db.commit(); db.refresh(user); return user

@app.post("/api/users/{user_id}/reset-password")
def admin_reset_password(user_id:int,data:AdminPasswordReset,db:DB,admin:Annotated[User,Depends(require(Role.ADMIN))]):
    user=db.get(User,user_id)
    if not user: raise HTTPException(404,"User not found.")
    check_password_rules(data.new_password); user.password_hash=passwords.hash(data.new_password)
    for session in db.scalars(select(AuthSession).where(AuthSession.user_id==user.id)): session.revoked=True
    audit(db,admin.id,"USER_PASSWORD_RESET","USER",user.id,"Admin reset password")
    db.commit(); return {"success":True,"message":"Password reset; all existing sessions were revoked."}

@app.post("/api/users/{user_id}/unlock",response_model=UserOut)
def unlock(user_id:int,db:DB,admin:Annotated[User,Depends(require(Role.ADMIN))]):
    user=db.get(User,user_id)
    if not user: raise HTTPException(404,"User not found.")
    if user.status!=AccountStatus.LOCKED: raise HTTPException(409,"Only locked accounts can be unlocked.")
    user.status=AccountStatus.ACTIVE; user.failed_attempts=0
    audit(db,admin.id,"USER_UNLOCKED","USER",user.id,"LOCKED->ACTIVE")
    notify(db,user.id,"Your account has been unlocked.","/login")
    db.commit(); db.refresh(user); return user

@app.post("/api/users/{user_id}/activate",response_model=UserOut)
def activate(user_id:int,db:DB,admin:Annotated[User,Depends(require(Role.ADMIN))]):
    user=db.get(User,user_id)
    if not user: raise HTTPException(404,"User not found.")
    previous=user.status; user.status=AccountStatus.ACTIVE; audit(db,admin.id,"USER_ACTIVATED","USER",user.id,f"{previous.value}->ACTIVE"); notify(db,user.id,"Your account has been activated.","/profile"); db.commit(); return user

@app.post("/api/users/{user_id}/deactivate",response_model=UserOut)
def deactivate(user_id:int,db:DB,admin:Annotated[User,Depends(require(Role.ADMIN))]):
    user=db.get(User,user_id)
    if not user: raise HTTPException(404,"User not found.")
    if user.id==admin.id: raise HTTPException(422,"You cannot deactivate your own account.")
    user.status=AccountStatus.INACTIVE
    for session in db.scalars(select(AuthSession).where(AuthSession.user_id==user.id)): session.revoked=True
    audit(db,admin.id,"USER_DEACTIVATED","USER",user.id,"ACTIVE->INACTIVE"); db.commit(); return user

@app.post("/api/msmes",response_model=MSMEOut,status_code=201)
def create_msme(data:MSMECreate,db:DB,admin:Annotated[User,Depends(require(Role.ADMIN))]):
    if db.scalar(select(MSME).where((MSME.name==data.name)|(MSME.msme_code==data.msme_code))): raise HTTPException(409,"MSME name or code already exists.")
    msme=MSME(**data.model_dump()); db.add(msme)
    try: db.flush()
    except IntegrityError: db.rollback();raise HTTPException(409,"MSME name or code already exists.")
    audit(db,admin.id,"MSME_CREATED","MSME",msme.id,msme.msme_code); db.commit(); db.refresh(msme); return msme

@app.patch("/api/msmes/{msme_id}",response_model=MSMEOut)
def update_msme(msme_id:int,data:MSMEUpdate,db:DB,admin:Annotated[User,Depends(require(Role.ADMIN))]):
    msme=db.get(MSME,msme_id)
    if not msme or msme.deleted_at: raise HTTPException(404,"MSME not found.")
    updates=data.model_dump(exclude_unset=True)
    if "name" in updates and db.scalar(select(MSME).where(MSME.name==updates["name"],MSME.id!=msme_id)): raise HTTPException(409,"MSME name already exists.")
    if "msme_code" in updates and db.scalar(select(MSME).where(MSME.msme_code==updates["msme_code"],MSME.id!=msme_id)): raise HTTPException(409,"MSME code already exists.")
    changes=[]
    for field,value in updates.items():
        previous=getattr(msme,field)
        if previous!=value: setattr(msme,field,value); changes.append(f"{field}:{previous}->{value}")
    if changes: audit(db,admin.id,"MSME_UPDATED","MSME",msme.id,"; ".join(changes))
    try: db.commit()
    except IntegrityError: db.rollback();raise HTTPException(409,"MSME name or code already exists.")
    db.refresh(msme); return msme

@app.get("/api/msmes",response_model=list[MSMEOut])
def list_msmes(db:DB,user:Annotated[User,Depends(require(Role.ADMIN,Role.MANAGEMENT))],search:str|None=None,certificate_level:str|None=None,status_filter:str|None=Query(default=None,alias="status")):
    query=select(MSME).where(MSME.deleted_at==None)
    if search:
        term=f"%{search}%"; query=query.where((MSME.name.ilike(term))|(MSME.msme_code.ilike(term))|(MSME.zed_id.ilike(term))|(MSME.city.ilike(term))|(MSME.state.ilike(term))|(MSME.address.ilike(term))|(MSME.unit_name.ilike(term))|(MSME.contact_person.ilike(term))|(MSME.email.ilike(term))|(MSME.phone.ilike(term))|(MSME.industry.ilike(term)))
    if certificate_level: query=query.where(MSME.certificate_level==certificate_level)
    if status_filter: query=query.where(MSME.status==status_filter)
    return list(db.scalars(query.order_by(MSME.name)))

@app.post("/api/master-data",response_model=MasterDataOut,status_code=201)
def add_master_data(data:MasterDataIn,db:DB,admin:Annotated[User,Depends(require(Role.ADMIN))]):
    exists=db.scalar(select(MasterData).where(MasterData.category==data.category,MasterData.code==data.code))
    if exists: raise HTTPException(409,"Master-data code already exists in this category.")
    item=MasterData(**data.model_dump()); db.add(item); db.flush(); audit(db,admin.id,"MASTER_DATA_CREATED","MASTER_DATA",item.id,f"{item.category}:{item.code}"); db.commit(); db.refresh(item); return item
@app.get("/api/master-data",response_model=list[MasterDataOut])
def get_master_data(db:DB,user:Annotated[User,Depends(current_user)],category:str|None=None,active_only:bool=True):
    query=select(MasterData)
    if category: query=query.where(MasterData.category==category)
    if active_only: query=query.where(MasterData.active==True)
    return list(db.scalars(query.order_by(MasterData.category,MasterData.label)))
@app.put("/api/settings/{key}",response_model=SettingOut)
def set_setting(key:str,data:SettingIn,db:DB,admin:Annotated[User,Depends(require(Role.ADMIN))]):
    if key=="manday_hours":
        try: value=Decimal(data.value)
        except Exception: raise HTTPException(422,"Hours per manday must be a positive number.")
        if value<=0 or value>24: raise HTTPException(422,"Hours per manday must be greater than 0 and no more than 24.")
    setting=db.get(SystemSetting,key)
    if setting: setting.value=data.value; setting.updated_by=admin.id; setting.updated_at=datetime.now(timezone.utc)
    else: setting=SystemSetting(key=key,value=data.value,updated_by=admin.id); db.add(setting)
    audit(db,admin.id,"SETTING_UPDATED","SETTING",None,key); db.commit(); db.refresh(setting); return setting
@app.get("/api/settings/{key}",response_model=SettingOut)
def get_setting(key:str,db:DB,user:Annotated[User,Depends(require(Role.ADMIN))]):
    setting=db.get(SystemSetting,key)
    if not setting and key=="manday_hours": return {"key":key,"value":"8"}
    if not setting: raise HTTPException(404,"Setting not found.")
    return setting

def active_consultant(db:Session,user_id:int):
    user=db.get(User,user_id)
    if not user or user.role!=Role.END_USER or user.status!=AccountStatus.ACTIVE: raise HTTPException(422,"Assignment requires an active End User/Consultant.")
    return user

def active_reviewer(db:Session,user_id:int,role:Role):
    user=db.get(User,user_id)
    if not user or user.role!=role or user.status!=AccountStatus.ACTIVE: raise HTTPException(422,f"Assignment requires an active {role.value.replace('_',' ').title()}.")
    return user

def default_hours_per_manday(db:Session)->Decimal:
    setting=db.get(SystemSetting,"manday_hours")
    if not setting: return Decimal(8)
    try: value=Decimal(setting.value)
    except Exception: return Decimal(8)
    return value if value>0 else Decimal(8)

def _package_totals_valid(data:PackageConfig):
    percent=data.project_payment_percent+sum((m.payment_percent for m in data.milestones),Decimal(0))
    amounts=sum((m.amount for m in data.milestones),Decimal(0))+(data.commercial_amount*data.project_payment_percent/Decimal(100))
    if percent!=Decimal(100): raise HTTPException(422,"Project and milestone payment percentages must total 100%.")
    if abs(amounts-data.commercial_amount)>Decimal("0.01"): raise HTTPException(422,"Project and milestone payment amounts must add up to the package commercial amount.")
    planned=sum((m.planned_mandays for m in data.milestones),Decimal(0))
    if planned!=data.planned_mandays: raise HTTPException(422,"Milestone planned mandays must add up to the package manday total.")

@app.get("/api/packages",response_model=list[PackageOut])
def list_packages(db:DB,user:Annotated[User,Depends(current_user)],include_inactive:bool=False):
    query=select(ServicePackage).order_by(ServicePackage.code)
    if not include_inactive or user.role!=Role.ADMIN: query=query.where(ServicePackage.active==True)
    return [PackageOut(**item.__dict__) for item in db.scalars(query)]

@app.put("/api/packages/{code}",response_model=PackageOut)
def save_package(code:str,data:PackageConfig,db:DB,admin:Annotated[User,Depends(require(Role.ADMIN))]):
    if code.upper()!=data.code: raise HTTPException(422,"Package path code must match the body code.")
    _package_totals_valid(data)
    item=db.scalar(select(ServicePackage).where(ServicePackage.code==data.code))
    values=data.model_dump(mode="json",exclude={"code"})
    if item:
        for field,value in values.items(): setattr(item,field,value)
    else: item=ServicePackage(code=data.code,updated_by=admin.id,**values);db.add(item)
    item.updated_by=admin.id
    db.flush();audit(db,admin.id,"PACKAGE_SAVED","SERVICE_PACKAGE",item.id,item.code);db.commit();db.refresh(item)
    return PackageOut(**item.__dict__)

@app.post("/api/job-cards",response_model=JobCardOut,status_code=201)
def create_job_card(data:JobCardCreate,db:DB,admin:Annotated[User,Depends(require(Role.ADMIN))]):
    package=db.scalar(select(ServicePackage).where(ServicePackage.code==data.package_code,ServicePackage.active==True)) if data.package_code else None
    if data.package_code and not package: raise HTTPException(422,"Selected service package is not active or does not exist.")
    if not package and data.required_md is None: raise HTTPException(422,"Planned mandays are required when a service package is not selected.")
    required_md=Decimal(str(package.planned_mandays)) if package else data.required_md
    package_scope=package.scope if package else data.scope
    if data.project_end_date < data.project_start_date: raise HTTPException(422,"Project end date cannot be before the start date.")
    msme=db.get(MSME,data.msme_id)
    if not msme or msme.deleted_at or msme.status!="ACTIVE": raise HTTPException(422,"Selected MSME is not active.")
    source_ticket=db.get(Ticket,data.source_ticket_id) if data.source_ticket_id else None
    if data.source_ticket_id and not source_ticket: raise HTTPException(404,"Source ticket not found.")
    if source_ticket: _get_visible_ticket(db,source_ticket.id,admin)
    source_enquiry=db.get(Enquiry,data.source_enquiry_id) if data.source_enquiry_id else None
    if data.source_enquiry_id and not source_enquiry: raise HTTPException(404,"Source enquiry not found.")
    if source_enquiry:
        if not _visible_enquiry(source_enquiry,admin): raise HTTPException(404,"Source enquiry not found.")
        if source_enquiry.status not in ("QUALIFIED","FINALISED"): raise HTTPException(409,"Only a qualified or finalised enquiry can be converted into a Job Card.")
        if db.scalar(select(JobCard.id).where(JobCard.source_enquiry_id==source_enquiry.id)): raise HTTPException(409,"This enquiry is already linked to a Job Card.")
        if db.scalar(select(Ticket.id).where(Ticket.enquiry_id==source_enquiry.id,Ticket.job_card_id.is_not(None))): raise HTTPException(409,"A ticket for this enquiry is already linked to a Job Card; link this enquiry to that Job Card instead.")
        if source_enquiry.source_ticket_id:
            enquiry_ticket=db.get(Ticket,source_enquiry.source_ticket_id)
            if not source_ticket: source_ticket=enquiry_ticket
            elif source_ticket and source_ticket.id!=enquiry_ticket.id: raise HTTPException(409,"The selected source ticket does not match the enquiry's source ticket.")
            if source_ticket: _get_visible_ticket(db,source_ticket.id,admin)
    if source_ticket and source_ticket.enquiry_id:
        ticket_enquiry=db.get(Enquiry,source_ticket.enquiry_id)
        if source_enquiry and source_enquiry.id!=ticket_enquiry.id: raise HTTPException(409,"The source ticket is linked to a different enquiry.")
        if not source_enquiry: source_enquiry=ticket_enquiry
        if source_enquiry:
            if not _visible_enquiry(source_enquiry,admin): raise HTTPException(404,"Source enquiry not found.")
            if source_enquiry.status not in ("QUALIFIED","FINALISED"): raise HTTPException(409,"Only a qualified or finalised enquiry can be converted into a Job Card.")
            if db.scalar(select(JobCard.id).where(JobCard.source_enquiry_id==source_enquiry.id)): raise HTTPException(409,"This enquiry is already linked to a Job Card.")
    if source_ticket and source_ticket.job_card_id: raise HTTPException(409,"The source ticket is already linked to a Job Card.")
    ids=[(data.lead_consultant_id,"LEAD_CONSULTANT"),(data.co_consultant_id,"CO_CONSULTANT"),(data.subject_expert_id,"SUBJECT_EXPERT")]
    seen=set()
    for user_id,_ in ids:
        if user_id:
            if user_id in seen: raise HTTPException(422,"A consultant cannot occupy more than one role on a Job Card.")
            seen.add(user_id); active_consultant(db,user_id)
    if not data.unit_representative_id or not data.director_id: raise HTTPException(422,"A Unit Representative and Director must be assigned before creating a Job Card.")
    active_reviewer(db,data.unit_representative_id,Role.UNIT_REPRESENTATIVE); active_reviewer(db,data.director_id,Role.DIRECTOR)
    hours_per_manday=data.hours_per_manday or default_hours_per_manday(db)
    card=JobCard(number=next_business_number(db,"job_card"),source_ticket_id=source_ticket.id if source_ticket else None,source_enquiry_id=source_enquiry.id if source_enquiry else None,project_name=data.project_name,package_code=package.code if package else data.package_code,scope=package_scope,commercial_amount=package.commercial_amount if package else None,project_payment_percent=package.project_payment_percent if package else None,msme_id=data.msme_id,location=data.location,project_start_date=data.project_start_date,project_end_date=data.project_end_date,required_md=required_md,hours_per_manday=hours_per_manday,required_hours=required_md*hours_per_manday,unit_representative_id=data.unit_representative_id,director_id=data.director_id,remarks=data.remarks,created_by=admin.id,payment_gate_enabled=True,status="AWAITING_PAYMENT")
    db.add(card)
    try: db.flush()
    except IntegrityError:
        db.rollback();raise HTTPException(409,"This source ticket or enquiry already has a Job Card linked.")
    if package:
        for configured in package.milestones:
            db.add(Milestone(job_card_id=card.id,code=configured["code"],name=configured["name"],planned_visits=configured["planned_visits"],planned_mandays=configured["planned_mandays"],amount=configured["amount"],payment_percent=configured["payment_percent"],activities=configured.get("activities",[]),description=package.scope))
    if source_enquiry:
        source_enquiry.status="CONVERTED";source_enquiry.updated_at=datetime.now(timezone.utc)
        audit(db,admin.id,"ENQUIRY_CONVERTED","ENQUIRY",None,f"{source_enquiry.number} -> {card.number}",entity_ref=str(source_enquiry.id))
        for related_ticket in db.scalars(select(Ticket).where(Ticket.enquiry_id==source_enquiry.id)):
            related_ticket.job_card_id=card.id
            if not db.scalar(select(TicketRelationship.id).where(TicketRelationship.ticket_id==related_ticket.id,TicketRelationship.related_job_card_id==card.id)):
                db.add(TicketRelationship(ticket_id=related_ticket.id,related_job_card_id=card.id,relationship_type="REFERENCE",created_by=admin.id))
    if source_ticket:
        source_ticket.job_card_id=card.id
        if source_enquiry and source_ticket.enquiry_id!=source_enquiry.id: source_ticket.enquiry_id=source_enquiry.id
        db.add(TicketRelationship(ticket_id=source_ticket.id,related_job_card_id=card.id,relationship_type="CREATED_FROM",created_by=admin.id))
    for user_id,kind in ids:
        if user_id:
            db.add(Assignment(job_card_id=card.id,user_id=user_id,assignment_type=kind,assigned_by=admin.id,allocation_start_date=card.project_start_date,allocation_end_date=card.project_end_date)); notify(db,user_id,f"You were assigned as {kind.replace('_',' ').title()} on Job Card {card.number}.",f"/job-cards/{card.id}")
    for user_id,kind in ((data.unit_representative_id,"UNIT_REPRESENTATIVE"),(data.director_id,"DIRECTOR")):
        db.add(Assignment(job_card_id=card.id,user_id=user_id,assignment_type=kind,assigned_by=admin.id));notify(db,user_id,f"You were assigned as {kind.replace('_',' ').title()} on Job Card {card.number}.",f"/job-cards/{card.id}")
    for marketer in db.scalars(select(User).where(User.role==Role.MARKETING,User.status==AccountStatus.ACTIVE)):
        notify(db,marketer.id,f"New Job Card {card.number} is awaiting its initial payment invoice.",f"/marketing?job_card={card.id}")
    audit(db,admin.id,"JOB_CARD_CREATED","JOB_CARD",card.id,card.number)
    if package: audit(db,admin.id,"PACKAGE_SNAPSHOT_APPLIED","JOB_CARD",card.id,f"{package.code}; {len(package.milestones)} milestones")
    db.commit(); db.refresh(card); return _job_card_out_for_user(card,admin)

@app.get("/api/job-cards",response_model=list[JobCardListOut])
def list_job_cards(db:DB,user:Annotated[User,Depends(current_user)],status_filter:str|None=Query(default=None,alias="status"),msme_id:int|None=None,consultant_id:int|None=None,search:str|None=None,location:str|None=None,zed_id:str|None=None,certificate_level:str|None=None,start_date:date|None=None,end_date:date|None=None,delayed:bool|None=None,overrun:bool|None=None,page:int=Query(default=1,ge=1),page_size:int=Query(default=25,ge=1,le=100)):
    query=select(JobCard).where(JobCard.deleted_at==None)
    if user.role==Role.END_USER: query=query.join(Assignment,Assignment.job_card_id==JobCard.id).where(Assignment.user_id==user.id,Assignment.active==True)
    elif user.role==Role.UNIT_REPRESENTATIVE: query=query.where(JobCard.unit_representative_id==user.id,JobCard.status=="PENDING_APPROVAL")
    elif user.role==Role.DIRECTOR: query=query.where(JobCard.director_id==user.id,JobCard.status=="PENDING_APPROVAL")
    elif consultant_id: query=query.join(Assignment,Assignment.job_card_id==JobCard.id).where(Assignment.user_id==consultant_id,Assignment.active==True)
    if status_filter:
        if user.role==Role.ADMIN and status_filter in {"AWAITING_PAYMENT","LOCKED","INVOICE_RAISED","PARTIALLY_PAID","PAID","OVERDUE","PAYMENT_PENDING"}: raise HTTPException(403,"Payment status is not available to System Admin.")
        query=query.where(JobCard.status==status_filter)
    if msme_id: query=query.where(JobCard.msme_id==msme_id)
    if start_date: query=query.where(JobCard.project_start_date>=start_date)
    if end_date: query=query.where(JobCard.project_end_date<=end_date)
    if zed_id or certificate_level or search:
        query=query.join(MSME,MSME.id==JobCard.msme_id)
        if zed_id: query=query.where(MSME.zed_id.ilike(f"%{zed_id}%"))
        if certificate_level: query=query.where(MSME.certificate_level==certificate_level)
        if search: query=query.where((JobCard.number.ilike(f"%{search}%"))|(MSME.name.ilike(f"%{search}%"))|(MSME.zed_id.ilike(f"%{search}%"))|(JobCard.location.ilike(f"%{search}%")))
    if location: query=query.where(JobCard.location.ilike(f"%{location}%"))
    query=query.order_by(JobCard.created_at.desc())
    if delayed is None and overrun is None and user.role not in (Role.UNIT_REPRESENTATIVE,Role.DIRECTOR):
        cards=list(db.scalars(query.offset((page-1)*page_size).limit(page_size)))
        return [JobCardListOut(**{**_job_card_out_for_user(card,user).model_dump(),**manday_summary(db,card)}) for card in cards]
    cards=list(db.scalars(query))
    if user.role==Role.UNIT_REPRESENTATIVE:
        ready=[]
        for card in cards:
            latest=int(db.scalar(select(func.coalesce(func.max(Approval.cycle),0)).where(Approval.job_card_id==card.id)) or 1)
            consultant_approved=db.scalar(select(Approval.id).where(Approval.job_card_id==card.id,Approval.cycle==latest,Approval.stage=="CONSULTANT_CONFIRMATION",Approval.decision=="APPROVED"))
            unit_done=db.scalar(select(Approval.id).where(Approval.job_card_id==card.id,Approval.cycle==latest,Approval.stage=="UNIT_REPRESENTATIVE"))
            if consultant_approved and not unit_done: ready.append(card)
        cards=ready
    elif user.role==Role.DIRECTOR:
        ready=[]
        for card in cards:
            latest=int(db.scalar(select(func.coalesce(func.max(Approval.cycle),0)).where(Approval.job_card_id==card.id)) or 1)
            unit_approved=db.scalar(select(Approval.id).where(Approval.job_card_id==card.id,Approval.cycle==latest,Approval.stage=="UNIT_REPRESENTATIVE",Approval.decision=="APPROVED"))
            director_done=db.scalar(select(Approval.id).where(Approval.job_card_id==card.id,Approval.cycle==latest,Approval.stage=="DIRECTOR_REVIEW"))
            if unit_approved and not director_done: ready.append(card)
        cards=ready
    if delayed is not None or overrun is not None:
        cards=[card for card in cards if (delayed is None or (card_dashboard_row(db,card)["status"]=="DELAYED")==delayed) and (overrun is None or card_dashboard_row(db,card)["overrun"]==overrun)]
    return [JobCardListOut(**{**_job_card_out_for_user(card,user).model_dump(),**manday_summary(db,card)}) for card in cards[(page-1)*page_size:page*page_size]]

@app.get("/api/job-cards/{card_id}",response_model=JobCardOut)
def get_job_card(card_id:int,db:DB,user:Annotated[User,Depends(current_user)]):
    return _job_card_out_for_user(assigned_card(db,user,card_id),user)

@app.get("/api/job-cards/{card_id}/workspace")
def get_job_card_workspace(card_id:int,db:DB,user:Annotated[User,Depends(current_user)]):
    card=assigned_card(db,user,card_id);msme=db.get(MSME,card.msme_id)
    milestones=list(db.scalars(select(Milestone).where(Milestone.job_card_id==card.id).order_by(Milestone.code)))
    source_enquiry=db.get(Enquiry,card.source_enquiry_id) if card.source_enquiry_id else None
    source_ticket=db.get(Ticket,card.source_ticket_id) if card.source_ticket_id else None
    related_ids=set(db.scalars(select(Ticket.id).where(Ticket.job_card_id==card.id)))
    related_ids.update(db.scalars(select(TicketRelationship.ticket_id).where(TicketRelationship.related_job_card_id==card.id)))
    if source_ticket: related_ids.add(source_ticket.id)
    related_tickets=list(db.scalars(select(Ticket).where(Ticket.id.in_(related_ids)).order_by(Ticket.created_at.desc()))) if related_ids else []
    assignments=db.execute(select(Assignment,User).join(User,User.id==Assignment.user_id).where(Assignment.job_card_id==card.id,Assignment.active.is_(True)).order_by(Assignment.assignment_type)).all()
    activities=list(db.scalars(select(JobActivity).where(JobActivity.job_card_id==card.id).order_by(JobActivity.created_at.desc()).limit(250)))
    visits=list(db.scalars(select(Visit).where(Visit.job_card_id==card.id,Visit.deleted_at.is_(None)).order_by(Visit.visit_date.desc(),Visit.visit_number.desc()).limit(250)))
    documents=list(db.scalars(select(Document).where(Document.job_card_id==card.id,Document.deleted_at.is_(None)).order_by(Document.uploaded_at.desc()).limit(250)))
    record_documents=list(db.scalars(select(RecordDocument).where(RecordDocument.owner_job_card_id==card.id).order_by(RecordDocument.uploaded_at.desc()).limit(500)))
    if user.role==Role.ADMIN:
        record_documents=[row for row in record_documents if row.payment_id is None]
    payments=list(db.scalars(select(Payment).where(Payment.job_card_id==card.id).order_by(Payment.invoice_date.desc(),Payment.id.desc()))) if user.role!=Role.ADMIN else []
    actions=list(db.scalars(select(WorkflowAction).where(WorkflowAction.job_card_id==card.id).order_by(WorkflowAction.created_at.desc()).limit(250)))
    approvals=list(db.scalars(select(Approval).where(Approval.job_card_id==card.id).order_by(Approval.cycle,Approval.id)))
    reviews=[event for milestone in milestones for event in milestone_review_events(db,milestone.id)]
    closure=db.scalar(select(ProjectClosure).where(ProjectClosure.job_card_id==card.id))
    audit_rows=list(db.scalars(select(AuditLog).where(or_((AuditLog.entity_type=="JOB_CARD")&(AuditLog.entity_id==card.id),(AuditLog.entity_type=="MILESTONE")&(AuditLog.entity_id.in_([m.id for m in milestones] or [-1])),(AuditLog.entity_type=="VISIT")&(AuditLog.entity_id.in_([v.id for v in visits] or [-1])),(AuditLog.entity_type=="DOCUMENT")&(AuditLog.entity_id.in_([d.id for d in documents] or [-1])),(AuditLog.entity_type=="APPROVAL")&(AuditLog.entity_id.in_([a.id for a in approvals] or [-1])))).order_by(AuditLog.created_at.desc()).limit(250)))
    if user.role==Role.ADMIN:
        audit_rows=[row for row in audit_rows if row.action!="JOB_CARD_ACTIVATED"]
    ticket_rows=[{"id":str(t.id),"number":t.number,"title":t.title,"status":t.status,"type":t.ticket_type,"href":f"/marketing?search={quote(t.number)}"} for t in related_tickets]
    source_ticket_row=next((row for row in ticket_rows if row["id"]==str(source_ticket.id)),None) if source_ticket else None
    enquiry_row={"id":str(source_enquiry.id),"number":source_enquiry.number,"company_name":source_enquiry.company_name,"status":source_enquiry.status,"href":f"/marketing?view=enquiries&search={quote(source_enquiry.number)}"} if source_enquiry else None
    milestone_rows=[]
    for milestone in milestones:
        item={**MilestoneOut(**{**milestone.__dict__,**milestone_summary(db,milestone)}).model_dump(mode="json"),"reviews":[MilestoneReviewOut.model_validate(event).model_dump(mode="json") for event in reviews if event.milestone_id==milestone.id]}
        if user.role==Role.ADMIN:
            item["payment_status"]=None
            item["payment_date"]=None
            if item.get("status")=="LOCKED": item["status"]="PENDING_ACTIVATION"
        milestone_rows.append(item)
    return {"job_card":_job_card_out_for_user(card,user).model_dump(mode="json"),"client":MSMEOut.model_validate(msme).model_dump(mode="json"),"source_ticket":source_ticket_row,"source_enquiry":enquiry_row,"related_tickets":ticket_rows,"team":[{"user_id":u.id,"full_name":u.full_name,"assignment_type":a.assignment_type} for a,u in assignments],"mandays":manday_summary(db,card),"milestones":milestone_rows,"activities":[_job_activity_out(db,row).model_dump(mode="json") for row in activities],"visits":[VisitOut.model_validate(row).model_dump(mode="json") for row in visits],"documents":[DocumentOut.model_validate(row).model_dump(mode="json") for row in documents],"record_documents":[RecordDocumentOut.model_validate(row).model_dump(mode="json") for row in record_documents],"payments":[_payment_out(row) for row in payments],"actions":[_workflow_out(db,row).model_dump(mode="json") for row in actions],"approvals":[ApprovalOut.model_validate(row).model_dump(mode="json") for row in approvals],"closure":ClosureOut.model_validate(closure).model_dump(mode="json") if closure else None,"history":[AuditOut.model_validate(row).model_dump(mode="json") for row in audit_rows]}

@app.get("/api/job-cards/{card_id}/assignments")
def list_assignments(card_id:int,db:DB,user:Annotated[User,Depends(current_user)]):
    assigned_card(db,user,card_id)
    rows=db.execute(select(Assignment,User).join(User,User.id==Assignment.user_id).where(Assignment.job_card_id==card_id).order_by(Assignment.assigned_at.desc(),Assignment.id.desc())).all()
    return [{"id":a.id,"user_id":u.id,"full_name":u.full_name,"assignment_type":a.assignment_type,"assigned_at":a.assigned_at,"allocation_start_date":a.allocation_start_date,"allocation_end_date":a.allocation_end_date,"ended_at":a.ended_at,"active":a.active} for a,u in rows]

@app.get("/api/job-cards/{card_id}/record-documents",response_model=list[RecordDocumentOut])
def list_job_card_record_documents(card_id:int,db:DB,user:Annotated[User,Depends(current_user)]):
    assigned_card(db,user,card_id)
    return list(db.scalars(select(RecordDocument).where(RecordDocument.owner_job_card_id==card_id).order_by(RecordDocument.uploaded_at.desc(),RecordDocument.id.desc()).limit(500)))

@app.get("/api/job-cards/{card_id}/activity",response_model=list[AuditOut])
def job_card_activity(card_id:int,db:DB,user:Annotated[User,Depends(current_user)]):
    assigned_card(db,user,card_id)
    child_entities=(("VISIT",select(Visit.id).where(Visit.job_card_id==card_id)),("DOCUMENT",select(Document.id).where(Document.job_card_id==card_id)),("MILESTONE",select(Milestone.id).where(Milestone.job_card_id==card_id)),("APPROVAL",select(Approval.id).where(Approval.job_card_id==card_id)))
    conditions=[(AuditLog.entity_type=="JOB_CARD")&(AuditLog.entity_id==card_id)]
    conditions.extend((AuditLog.entity_type==kind)&(AuditLog.entity_id.in_(ids)) for kind,ids in child_entities)
    return list(db.scalars(select(AuditLog).where(or_(*conditions)).order_by(AuditLog.created_at.desc()).limit(200)))

def _job_activity_out(db:Session,row:JobActivity)->JobActivityOut:
    kind=db.get(ActivityType,row.activity_type_id);assignee=db.get(User,row.assigned_user_id);card=db.get(JobCard,row.job_card_id)
    hours=Decimal(str(card.hours_per_manday or 8)) if card else Decimal(8)
    mandays=(Decimal(row.duration_minutes)/Decimal(60)/hours).quantize(MD_PRECISION) if hours else Decimal(0)
    return JobActivityOut(id=row.id,job_card_id=row.job_card_id,milestone_id=row.milestone_id,visit_id=row.visit_id,activity_type_id=row.activity_type_id,activity_type_code=kind.code if kind else "",activity_type_name=kind.name if kind else "",name=row.name,description=row.description,assigned_user_id=row.assigned_user_id,assigned_user_name=assignee.full_name if assignee else "",start_date=row.start_date,end_date=row.end_date,duration_minutes=row.duration_minutes,mandays=mandays,status=row.status,remarks=row.remarks,created_by=row.created_by,created_at=row.created_at)

@app.get("/api/job-cards/{card_id}/activities",response_model=list[JobActivityOut])
def list_job_activities(card_id:int,db:DB,user:Annotated[User,Depends(current_user)],milestone_id:int|None=None,status_filter:str|None=Query(default=None,alias="status")):
    assigned_card(db,user,card_id);query=select(JobActivity).where(JobActivity.job_card_id==card_id)
    if milestone_id:query=query.where(JobActivity.milestone_id==milestone_id)
    if status_filter:query=query.where(JobActivity.status==status_filter)
    if user.role==Role.END_USER:query=query.where(JobActivity.assigned_user_id==user.id)
    return [_job_activity_out(db,row) for row in db.scalars(query.order_by(JobActivity.start_date,JobActivity.id.desc()).limit(250))]

@app.post("/api/job-cards/{card_id}/activities",response_model=JobActivityOut,status_code=201)
def create_job_activity(card_id:int,data:JobActivityCreate,db:DB,user:Annotated[User,Depends(require(Role.ADMIN,Role.END_USER))]):
    card=mutable_card(assigned_card(db,user,card_id));milestone=db.scalar(select(Milestone).where(Milestone.id==data.milestone_id,Milestone.job_card_id==card_id))
    if not milestone:raise HTTPException(422,"Choose a milestone belonging to this Job Card.")
    _require_work_payment(db,card,milestone)
    kind=db.scalar(select(ActivityType).where(ActivityType.id==data.activity_type_id,ActivityType.active.is_(True)))
    if not kind:raise HTTPException(422,"Choose an active activity type.")
    assignee_id=data.assigned_user_id if user.role==Role.ADMIN and data.assigned_user_id else user.id
    if user.role==Role.ADMIN and data.assigned_user_id is None:raise HTTPException(422,"Admin must select an assigned consultant.")
    if not db.scalar(select(Assignment.id).where(Assignment.job_card_id==card_id,Assignment.user_id==assignee_id,Assignment.active.is_(True))):raise HTTPException(422,"The assigned user must be an active member of this Job Card team.")
    if data.visit_id and not db.scalar(select(Visit.id).where(Visit.id==data.visit_id,Visit.job_card_id==card_id,Visit.milestone_id==milestone.id)):raise HTTPException(422,"The selected visit must belong to this Job Card milestone.")
    if data.status=="COMPLETED" and data.duration_minutes==0:raise HTTPException(422,"Record the activity duration before completing it.")
    row=JobActivity(job_card_id=card_id,milestone_id=milestone.id,visit_id=data.visit_id,activity_type_id=kind.id,name=data.name,description=data.description,assigned_user_id=assignee_id,start_date=data.start_date,end_date=data.end_date,duration_minutes=data.duration_minutes,status=data.status,remarks=data.remarks,created_by=user.id)
    db.add(row);db.flush();audit(db,user.id,"ACTIVITY_CREATED","JOB_ACTIVITY",row.id,f"{card.number}; {kind.code}; {row.name}")
    if assignee_id!=user.id:notify(db,assignee_id,f"Activity assigned on Job Card {card.number}: {row.name}.",f"/user/activities?job_card={card_id}")
    db.commit();db.refresh(row);return _job_activity_out(db,row)

@app.patch("/api/activities/{activity_id}",response_model=JobActivityOut)
def update_job_activity(activity_id:int,data:JobActivityUpdate,db:DB,user:Annotated[User,Depends(current_user)]):
    row=db.scalar(select(JobActivity).where(JobActivity.id==activity_id).with_for_update())
    if not row:raise HTTPException(404,"Activity not found.")
    card=mutable_card(assigned_card(db,user,row.job_card_id))
    if user.role!=Role.ADMIN and row.assigned_user_id!=user.id:raise HTTPException(403,"Only the assigned consultant or Admin can update this activity.")
    if row.status in ("COMPLETED","CANCELLED"):raise HTTPException(409,"A closed activity cannot be changed.")
    updates=data.model_dump(exclude_unset=True)
    start=updates.get("start_date",row.start_date);end=updates.get("end_date",row.end_date)
    if start and end and end<start:raise HTTPException(422,"Activity end date cannot precede its start date.")
    duration=updates.get("duration_minutes",row.duration_minutes)
    if updates.get("status")=="COMPLETED":
        milestone=db.get(Milestone,row.milestone_id);_require_work_payment(db,card,milestone)
        if duration==0:raise HTTPException(422,"Record the activity duration before completing it.")
    previous=row.status
    for field,value in updates.items():setattr(row,field,value)
    audit(db,user.id,"ACTIVITY_UPDATED","JOB_ACTIVITY",row.id,f"status {previous}->{row.status}; duration={row.duration_minutes}; remarks={row.remarks or ''}")
    db.commit();db.refresh(row);return _job_activity_out(db,row)

@app.get("/api/activities/{activity_id}/attachments",response_model=list[JobActivityAttachmentOut])
def list_job_activity_attachments(activity_id:int,db:DB,user:Annotated[User,Depends(current_user)]):
    row=db.get(JobActivity,activity_id)
    if not row:raise HTTPException(404,"Activity not found.")
    assigned_card(db,user,row.job_card_id)
    if user.role==Role.END_USER and row.assigned_user_id!=user.id:raise HTTPException(404,"Activity not found.")
    return list(db.scalars(select(JobActivityAttachment).where(JobActivityAttachment.activity_id==row.id).order_by(JobActivityAttachment.uploaded_at.desc())))

@app.post("/api/activities/{activity_id}/attachments",response_model=JobActivityAttachmentOut,status_code=201)
def upload_job_activity_attachment(activity_id:int,data:RecordFileUpload,db:DB,user:Annotated[User,Depends(require(Role.ADMIN,Role.END_USER))]):
    row=db.scalar(select(JobActivity).where(JobActivity.id==activity_id).with_for_update())
    if not row:raise HTTPException(404,"Activity not found.")
    card=mutable_card(assigned_card(db,user,row.job_card_id))
    if row.status in ("COMPLETED","CANCELLED"):raise HTTPException(409,"Files cannot be added to a closed activity.")
    if user.role==Role.END_USER and row.assigned_user_id!=user.id:raise HTTPException(403,"Only the assigned consultant can add files to this activity.")
    _require_work_payment(db,card,db.get(Milestone,row.milestone_id))
    key,size=_save_encoded_file(data.file_name,data.mime_type,data.content_base64)
    attachment=JobActivityAttachment(activity_id=row.id,file_name=data.file_name,storage_key=key,file_size=size,mime_type=data.mime_type,uploaded_by=user.id)
    db.add(attachment);db.flush();audit(db,user.id,"ACTIVITY_ATTACHMENT_UPLOADED","JOB_ACTIVITY",row.id,data.file_name);db.commit();db.refresh(attachment);return attachment

@app.get("/api/activity-attachments/{attachment_id}/download")
def download_job_activity_attachment(attachment_id:int,db:DB,user:Annotated[User,Depends(current_user)]):
    attachment=db.get(JobActivityAttachment,attachment_id)
    if not attachment:raise HTTPException(404,"Attachment not found.")
    row=db.get(JobActivity,attachment.activity_id)
    if not row:raise HTTPException(404,"Attachment not found.")
    assigned_card(db,user,row.job_card_id)
    if user.role==Role.END_USER and row.assigned_user_id!=user.id:raise HTTPException(404,"Attachment not found.")
    return _download_attachment(attachment.file_name,attachment.mime_type,attachment.storage_key)

@app.patch("/api/job-cards/{card_id}",response_model=JobCardOut)
def update_job_card(card_id:int,data:JobCardUpdate,db:DB,admin:Annotated[User,Depends(require(Role.ADMIN))]):
    card=mutable_card(assigned_card(db,admin,card_id)); updates=data.model_dump(exclude_none=True)
    start=updates.get("project_start_date",card.project_start_date); end=updates.get("project_end_date",card.project_end_date)
    if end<start: raise HTTPException(422,"Project end date cannot be before the start date.")
    if "msme_id" in updates:
        msme=db.get(MSME,updates["msme_id"])
        if not msme or msme.deleted_at or msme.status!="ACTIVE": raise HTTPException(422,"Selected MSME is not active.")
    if "unit_representative_id" in updates and updates["unit_representative_id"]!=card.unit_representative_id: active_reviewer(db,updates["unit_representative_id"],Role.UNIT_REPRESENTATIVE)
    if "director_id" in updates and updates["director_id"]!=card.director_id: active_reviewer(db,updates["director_id"],Role.DIRECTOR)
    if "number" in updates and db.scalar(select(JobCard).where(JobCard.number==updates["number"],JobCard.id!=card_id)): raise HTTPException(409,"Job Card number already exists.")
    if "required_md" in updates or "hours_per_manday" in updates:
        required_md=updates.get("required_md",Decimal(str(card.required_md)))
        hours_per_manday=updates.get("hours_per_manday",Decimal(str(card.hours_per_manday or 8)))
        updates["required_hours"]=required_md*hours_per_manday
    changes=[]
    for field,value in updates.items():
        previous=getattr(card,field)
        if previous!=value:
            setattr(card,field,value); changes.append(f"{field}:{previous}->{value}")
            role_type={"unit_representative_id":"UNIT_REPRESENTATIVE","director_id":"DIRECTOR"}.get(field)
            if role_type:
                for assignment in db.scalars(select(Assignment).where(Assignment.job_card_id==card_id,Assignment.assignment_type==role_type,Assignment.active==True)):
                    assignment.active=False;assignment.ended_at=datetime.now(timezone.utc)
                db.add(Assignment(job_card_id=card.id,user_id=value,assignment_type=role_type,assigned_by=admin.id));notify(db,value,f"You were assigned as {role_type.replace('_',' ').title()} on Job Card {card.number}.",f"/job-cards/{card.id}")
    if changes: audit(db,admin.id,"JOB_CARD_UPDATED","JOB_CARD",card.id,"; ".join(changes))
    db.commit(); db.refresh(card); return card
@app.post("/api/job-cards/{card_id}/reassign",response_model=JobCardOut)
def reassign(card_id:int,data:ReassignmentIn,db:DB,admin:Annotated[User,Depends(require(Role.ADMIN))]):
    card=mutable_card(assigned_card(db,admin,card_id)); active_consultant(db,data.user_id)
    duplicate=db.scalar(select(Assignment).where(Assignment.job_card_id==card_id,Assignment.user_id==data.user_id,Assignment.assignment_type!=data.assignment_type,Assignment.active==True))
    if duplicate: raise HTTPException(422,"A consultant cannot occupy more than one role on a Job Card.")
    previous_users=[]
    for assignment in db.scalars(select(Assignment).where(Assignment.job_card_id==card_id,Assignment.assignment_type==data.assignment_type,Assignment.active==True)):
        previous_users.append(str(assignment.user_id))
        assignment.active=False; assignment.ended_at=datetime.now(timezone.utc)
    db.add(Assignment(job_card_id=card_id,user_id=data.user_id,assignment_type=data.assignment_type,assigned_by=admin.id,allocation_start_date=data.allocation_start_date or card.project_start_date,allocation_end_date=data.allocation_end_date or card.project_end_date)); notify(db,data.user_id,f"You were assigned as {data.assignment_type.replace('_',' ').title()} on Job Card {card.number}.",f"/job-cards/{card.id}")
    old_ids=[int(value) for value in previous_users]
    if old_ids:
        for planned in db.scalars(select(PlannedVisit).where(PlannedVisit.job_card_id==card_id,PlannedVisit.consultant_id.in_(old_ids),PlannedVisit.status=="PLANNED")):
            planned.consultant_id=data.user_id;planned.change_reason=f"Admin reassignment: {data.reason}";planned.updated_at=datetime.now(timezone.utc);notify(db,data.user_id,f"A planned visit for Job Card {card.number} was reassigned to you.",f"/job-cards/{card.id}")
        for old_id in old_ids: notify(db,old_id,f"Your planned visits for Job Card {card.number} were reassigned by Admin. Reason: {data.reason}",f"/user/visits")
    audit(db,admin.id,"JOB_CARD_REASSIGNED","JOB_CARD",card.id,f"{data.assignment_type}:{','.join(previous_users) or 'none'}->{data.user_id}; allocation={data.allocation_start_date or card.project_start_date}..{data.allocation_end_date or card.project_end_date}; reason={data.reason}"); db.commit(); db.refresh(card); return card
@app.delete("/api/job-cards/{card_id}",status_code=204)
def archive_job_card(card_id:int,db:DB,admin:Annotated[User,Depends(require(Role.ADMIN))]):
    card=mutable_card(assigned_card(db,admin,card_id)); card.deleted_at=datetime.now(timezone.utc); card.deleted_by=admin.id; audit(db,admin.id,"JOB_CARD_ARCHIVED","JOB_CARD",card.id); db.commit()

@app.post("/api/job-cards/{card_id}/reopen",response_model=JobCardOut)
def reopen_job_card(card_id:int,data:ReopenIn,db:DB,admin:Annotated[User,Depends(require(Role.ADMIN))]):
    card=assigned_card(db,admin,card_id)
    if card.status!="COMPLETED": raise HTTPException(409,"Only completed Job Cards can be reopened.")
    card.status="IN_PROGRESS"
    audit(db,admin.id,"JOB_CARD_REOPENED","JOB_CARD",card.id,data.reason)
    notify(db,document_owner(db,card.id),f"Job Card {card.number} was reopened: {data.reason}",f"/job-cards/{card.id}")
    db.commit(); db.refresh(card); return card

def assigned_card(db:Session, user:User, card_id:int):
    card=db.get(JobCard,card_id)
    if not card or card.deleted_at: raise HTTPException(404,"Job Card not found.")
    if user.role==Role.END_USER and not db.scalar(select(Assignment).where(Assignment.job_card_id==card_id,Assignment.user_id==user.id,Assignment.active==True)): raise HTTPException(403,"You are not assigned to this Job Card.")
    if user.role==Role.UNIT_REPRESENTATIVE and card.unit_representative_id!=user.id: raise HTTPException(403,"You are not assigned to this Job Card.")
    if user.role==Role.DIRECTOR and card.director_id!=user.id: raise HTTPException(403,"You are not assigned to this Job Card.")
    return card

def mutable_card(card:JobCard):
    if card.status in ("CLOSURE_PENDING","COMPLETED"): raise HTTPException(409,"This Job Card is locked for project closure. An Admin must reopen it before operational changes can be made.")
    return card

@app.post("/api/job-cards/{card_id}/milestones",response_model=MilestoneOut,status_code=201)
def create_milestone(card_id:int,data:MilestoneCreate,db:DB,admin:Annotated[User,Depends(require(Role.ADMIN))]):
    mutable_card(assigned_card(db,admin,card_id))
    if "payment_date" in data.model_fields_set: raise HTTPException(403,"System Admin cannot view or change payment confirmation dates.")
    if data.closing_date and data.start_date and data.closing_date<data.start_date: raise HTTPException(422,"Milestone closing date cannot be before its start date.")
    if data.payment_date and data.closing_date and data.payment_date<data.closing_date: raise HTTPException(422,"Milestone payment date cannot be before its closing date.")
    if data.portal_report_submission_date and data.start_date and data.portal_report_submission_date<data.start_date: raise HTTPException(422,"Portal report submission date cannot be before the milestone start date.")
    if db.scalar(select(Milestone).where(Milestone.job_card_id==card_id,Milestone.code==data.code)): raise HTTPException(409,"Milestone code already exists on this Job Card.")
    if data.responsible_consultant_id:
        active_consultant(db,data.responsible_consultant_id)
        if not db.scalar(select(Assignment.id).where(Assignment.job_card_id==card_id,Assignment.user_id==data.responsible_consultant_id,Assignment.active.is_(True))): raise HTTPException(422,"The milestone consultant must be assigned to this Job Card.")
    milestone=Milestone(job_card_id=card_id,**data.model_dump()); db.add(milestone)
    try: db.flush()
    except IntegrityError: db.rollback();raise HTTPException(409,"Milestone code already exists on this Job Card.")
    audit(db,admin.id,"MILESTONE_CREATED","MILESTONE",milestone.id,milestone.code); db.commit(); db.refresh(milestone); return _milestone_out_for_user(db,milestone,admin)

@app.get("/api/job-cards/{card_id}/milestones",response_model=list[MilestoneOut])
def list_milestones(card_id:int,db:DB,user:Annotated[User,Depends(current_user)]):
    assigned_card(db,user,card_id); return [_milestone_out_for_user(db,m,user) for m in db.scalars(select(Milestone).where(Milestone.job_card_id==card_id).order_by(Milestone.code))]

def milestone_review_events(db:Session,milestone_id:int,cycle:int|None=None):
    query=select(MilestoneReviewHistory).where(MilestoneReviewHistory.milestone_id==milestone_id)
    if cycle is not None: query=query.where(MilestoneReviewHistory.cycle==cycle)
    return list(db.scalars(query.order_by(MilestoneReviewHistory.id)))

def milestone_review_record(db:Session,milestone:Milestone,cycle:int,stage:str,decision:str,comment:str|None,user:User):
    row=MilestoneReviewHistory(milestone_id=milestone.id,cycle=cycle,stage=stage,decision=decision,comment=comment,decided_by=user.id)
    db.add(row);audit(db,user.id,f"MILESTONE_{stage}_{decision}","MILESTONE",milestone.id,f"cycle={cycle}; {comment or ''}")
    card=db.get(JobCard,milestone.job_card_id)
    recipient=card.unit_representative_id if stage=="MANAGEMENT" and decision=="APPROVED" else document_owner(db,card.id)
    if recipient: notify(db,recipient,f"Milestone {milestone.code} on Job Card {card.number}: {decision.lower()}.{(' '+comment) if comment else ''}",f"/job-cards/{card.id}")
    return row

@app.post("/api/milestones/{milestone_id}/submit-review",response_model=MilestoneReviewOut,status_code=201)
def submit_milestone_review(milestone_id:int,db:DB,user:Annotated[User,Depends(require(Role.END_USER))]):
    milestone=db.get(Milestone,milestone_id)
    if not milestone: raise HTTPException(404,"Milestone not found.")
    card=mutable_card(assigned_card(db,user,milestone.job_card_id))
    if user.id!=document_owner(db,card.id): raise HTTPException(403,"Only the assigned lead consultant can submit milestone reviews.")
    summary=milestone_summary(db,milestone)
    if summary["actual_visits"]<milestone.planned_visits: raise HTTPException(422,"Complete the planned submitted visits before requesting milestone review.")
    if summary["status"] in ("LOCKED","COMPLETED"): raise HTTPException(409,"This milestone is payment locked or already complete.")
    events=milestone_review_events(db,milestone.id)
    cycle=max((event.cycle for event in events),default=0)
    current=[event for event in events if event.cycle==cycle]
    if current and not any(e.stage in ("MANAGEMENT","UNIT") and e.decision in ("RETURNED","REJECTED") for e in current): raise HTTPException(409,"A milestone review is already in progress or complete.")
    cycle+=1
    row=milestone_review_record(db,milestone,cycle,"CONSULTANT","SUBMITTED",None,user);db.commit();db.refresh(row);return row

@app.get("/api/milestones/management-review-queue")
def management_milestone_review_queue(db:DB,user:Annotated[User,Depends(require(Role.ADMIN,Role.MANAGEMENT))]):
    result=[]
    for event in db.scalars(select(MilestoneReviewHistory).where(MilestoneReviewHistory.stage=="CONSULTANT",MilestoneReviewHistory.decision=="SUBMITTED").order_by(MilestoneReviewHistory.created_at)):
        events=milestone_review_events(db,event.milestone_id,event.cycle)
        if any(e.stage=="MANAGEMENT" for e in events): continue
        milestone=db.get(Milestone,event.milestone_id);card=db.get(JobCard,milestone.job_card_id)
        result.append({"milestone_id":milestone.id,"job_card_id":card.id,"job_card_number":card.number,"code":milestone.code,"name":milestone.name,"planned_visits":milestone.planned_visits,"actual_visits":milestone_summary(db,milestone)["actual_visits"],"cycle":event.cycle,"submitted_at":event.created_at})
    return result

@app.post("/api/milestones/{milestone_id}/management-review",response_model=MilestoneReviewOut,status_code=201)
def decide_milestone_management_review(milestone_id:int,data:MilestoneReviewDecision,db:DB,user:Annotated[User,Depends(require(Role.ADMIN,Role.MANAGEMENT))]):
    milestone=db.get(Milestone,milestone_id)
    if not milestone: raise HTTPException(404,"Milestone not found.")
    events=milestone_review_events(db,milestone_id);cycle=max((event.cycle for event in events),default=0);current=[e for e in events if e.cycle==cycle]
    if not any(e.stage=="CONSULTANT" and e.decision=="SUBMITTED" for e in current): raise HTTPException(409,"Milestone has not been submitted for review.")
    if any(e.stage=="MANAGEMENT" for e in current): raise HTTPException(409,"Management has already reviewed this cycle.")
    if data.decision in ("RETURNED","REJECTED") and not (data.comment and data.comment.strip()): raise HTTPException(422,"A reason is required when returning or rejecting a milestone.")
    row=milestone_review_record(db,milestone,cycle,"MANAGEMENT",data.decision,data.comment,user);db.commit();db.refresh(row);return row

@app.get("/api/milestones/unit-confirmation-queue")
def unit_milestone_confirmation_queue(db:DB,user:Annotated[User,Depends(require(Role.UNIT_REPRESENTATIVE))]):
    result=[]
    for event in db.scalars(select(MilestoneReviewHistory).where(MilestoneReviewHistory.stage=="MANAGEMENT",MilestoneReviewHistory.decision=="APPROVED").order_by(MilestoneReviewHistory.created_at)):
        milestone=db.get(Milestone,event.milestone_id);card=db.get(JobCard,milestone.job_card_id)
        if card.unit_representative_id!=user.id: continue
        events=milestone_review_events(db,milestone.id,event.cycle)
        if any(e.stage=="UNIT" for e in events): continue
        result.append({"milestone_id":milestone.id,"job_card_id":card.id,"job_card_number":card.number,"code":milestone.code,"name":milestone.name,"planned_visits":milestone.planned_visits,"actual_visits":milestone_summary(db,milestone)["actual_visits"],"cycle":event.cycle,"submitted_at":event.created_at})
    return result

@app.post("/api/milestones/{milestone_id}/unit-confirmation",response_model=MilestoneReviewOut,status_code=201)
def decide_milestone_unit_confirmation(milestone_id:int,data:MilestoneReviewDecision,db:DB,user:Annotated[User,Depends(require(Role.UNIT_REPRESENTATIVE))]):
    milestone=db.get(Milestone,milestone_id)
    if not milestone: raise HTTPException(404,"Milestone not found.")
    card=assigned_card(db,user,milestone.job_card_id)
    if card.unit_representative_id!=user.id: raise HTTPException(403,"Only the assigned Unit Representative can confirm this milestone.")
    events=milestone_review_events(db,milestone_id);cycle=max((event.cycle for event in events),default=0);current=[e for e in events if e.cycle==cycle]
    if not any(e.stage=="MANAGEMENT" and e.decision=="APPROVED" for e in current): raise HTTPException(409,"Management approval is required before Unit Representative confirmation.")
    if any(e.stage=="UNIT" for e in current): raise HTTPException(409,"This milestone already has a Unit Representative decision.")
    if data.decision not in ("APPROVED","RETURNED","REJECTED"): raise HTTPException(422,"Invalid confirmation decision.")
    if data.decision in ("RETURNED","REJECTED") and not (data.comment and data.comment.strip()): raise HTTPException(422,"A reason is required when returning or rejecting a milestone.")
    row=milestone_review_record(db,milestone,cycle,"UNIT",data.decision,data.comment,user);db.commit();db.refresh(row);return row

@app.patch("/api/milestones/{milestone_id}",response_model=MilestoneOut)
def update_milestone(milestone_id:int,data:MilestoneUpdate,db:DB,admin:Annotated[User,Depends(require(Role.ADMIN))]):
    milestone=db.get(Milestone,milestone_id)
    if not milestone: raise HTTPException(404,"Milestone not found.")
    card=mutable_card(assigned_card(db,admin,milestone.job_card_id)); updates=data.model_dump(exclude_unset=True)
    if "payment_date" in updates: raise HTTPException(403,"System Admin cannot view or change payment confirmation dates.")
    start=updates.get("start_date",milestone.start_date); closing=updates.get("closing_date",milestone.closing_date); payment=updates.get("payment_date",milestone.payment_date); portal=updates.get("portal_report_submission_date",milestone.portal_report_submission_date)
    if closing and start and closing<start: raise HTTPException(422,"Milestone closing date cannot be before its start date.")
    if payment and closing and payment<closing: raise HTTPException(422,"Milestone payment date cannot be before its closing date.")
    if portal and start and portal<start: raise HTTPException(422,"Portal report submission date cannot be before the milestone start date.")
    consultant_id=updates.get("responsible_consultant_id",milestone.responsible_consultant_id)
    if consultant_id:
        active_consultant(db,consultant_id)
        if not db.scalar(select(Assignment.id).where(Assignment.job_card_id==milestone.job_card_id,Assignment.user_id==consultant_id,Assignment.active.is_(True))): raise HTTPException(422,"The milestone consultant must be assigned to this Job Card.")
    changes=[]
    for field,value in updates.items():
        previous=getattr(milestone,field)
        if previous!=value: setattr(milestone,field,value); changes.append(f"{field}:{previous}->{value}")
    if changes: audit(db,admin.id,"MILESTONE_UPDATED","MILESTONE",milestone.id,"; ".join(changes))
    db.commit();db.refresh(milestone);return _milestone_out_for_user(db,milestone,admin)

def visit_output(visit:Visit): return VisitOut.model_validate(visit)
RATINGS={"A":"Excellent","B":"Very Good","C":"Good","D":"Poor","E":"Not Satisfactory"}
VISIT_TEAM_FIELDS=(("consultant_id","LEAD_CONSULTANT"),("co_consultant_id","CO_CONSULTANT"),("subject_expert_id","SUBJECT_EXPERT"))
def visit_duration(data:VisitCreate)->int:
    if data.start_time and data.end_time:
        return int((datetime.combine(date.today(),data.end_time)-datetime.combine(date.today(),data.start_time)).total_seconds()//60)
    return data.hours*60+data.minutes
def visit_team_values(db:Session,card_id:int,data:VisitCreate,existing:Visit|None=None):
    values={}
    for field,assignment_type in VISIT_TEAM_FIELDS:
        explicit=field in data.model_fields_set
        selected=getattr(data,field) if explicit else (getattr(existing,field) if existing else None)
        if not explicit and not existing:
            selected=db.scalar(select(Assignment.user_id).where(Assignment.job_card_id==card_id,Assignment.assignment_type==assignment_type,Assignment.active==True))
        if selected is not None:
            active=db.scalar(select(Assignment.id).where(Assignment.job_card_id==card_id,Assignment.assignment_type==assignment_type,Assignment.user_id==selected,Assignment.active==True))
            same_snapshot=existing is not None and selected==getattr(existing,field)
            if not active and not same_snapshot: raise HTTPException(422,f"The selected {assignment_type.replace('_',' ').title()} is not actively assigned to this Job Card.")
        values[field]=selected
    return values
def planner_conflicts(db:Session,consultant_id:int,visit_date:date,start_time:TimeOfDay,duration:int,exclude_id:int|None=None):
    start=start_time.hour*60+start_time.minute;end=start+duration;rows=db.scalars(select(PlannedVisit).where(PlannedVisit.consultant_id==consultant_id,PlannedVisit.visit_date==visit_date,PlannedVisit.status=="PLANNED",*([PlannedVisit.id!=exclude_id] if exclude_id else [])))
    conflicts=[]
    for row in rows:
        other_start=row.start_time.hour*60+row.start_time.minute;other_end=other_start+row.expected_duration_minutes
        if start<other_end and other_start<end: conflicts.append({"id":row.id,"job_card_id":row.job_card_id,"start_time":row.start_time,"expected_duration_minutes":row.expected_duration_minutes,"objective":row.objective})
    return conflicts

@app.get("/api/planner/conflicts")
def get_planner_conflicts(db:DB,user:Annotated[User,Depends(require(Role.END_USER))],visit_date:date,start_time:TimeOfDay,duration_minutes:int=Query(gt=0,le=1440)):
    return {"conflicts":planner_conflicts(db,user.id,visit_date,start_time,duration_minutes)}

@app.get("/api/planned-visits",response_model=list[PlannedVisitOut])
def list_planned_visits(db:DB,user:Annotated[User,Depends(require(Role.END_USER,Role.ADMIN,Role.MANAGEMENT))],consultant_id:int|None=None,msme_id:int|None=None,job_card_id:int|None=None,milestone_id:int|None=None,start_date:date|None=None,end_date:date|None=None):
    query=select(PlannedVisit,JobCard.number,MSME.name,Milestone.code,Milestone.name,User.full_name).join(JobCard,JobCard.id==PlannedVisit.job_card_id).join(MSME,MSME.id==JobCard.msme_id).join(Milestone,Milestone.id==PlannedVisit.milestone_id).join(User,User.id==PlannedVisit.consultant_id).where(JobCard.deleted_at.is_(None))
    if user.role==Role.END_USER: query=query.where(PlannedVisit.consultant_id==user.id)
    elif consultant_id: query=query.where(PlannedVisit.consultant_id==consultant_id)
    if msme_id: query=query.where(JobCard.msme_id==msme_id)
    if job_card_id: query=query.where(PlannedVisit.job_card_id==job_card_id)
    if milestone_id: query=query.where(PlannedVisit.milestone_id==milestone_id)
    if start_date: query=query.where(PlannedVisit.visit_date>=start_date)
    if end_date: query=query.where(PlannedVisit.visit_date<=end_date)
    return [{**PlannedVisitOut.model_validate(row).model_dump(),"job_card_number":card_number,"msme_name":msme_name,"milestone_code":milestone_code,"milestone_name":milestone_name,"consultant_name":consultant_name} for row,card_number,msme_name,milestone_code,milestone_name,consultant_name in db.execute(query.order_by(PlannedVisit.visit_date,PlannedVisit.start_time).limit(500))]

def planner_notify(db:Session,card:JobCard,message:str):
    recipients=set(db.scalars(select(User.id).where(User.role.in_((Role.ADMIN,Role.MANAGEMENT)),User.status==AccountStatus.ACTIVE)))
    recipients.update(db.scalars(select(Assignment.user_id).where(Assignment.job_card_id==card.id,Assignment.active.is_(True))))
    for recipient in recipients: notify(db,recipient,message,f"/job-cards/{card.id}")

@app.post("/api/planned-visits",response_model=PlannedVisitOut,status_code=201)
def create_planned_visit(data:PlannedVisitCreate,db:DB,user:Annotated[User,Depends(require(Role.END_USER))]):
    card=mutable_card(assigned_card(db,user,data.job_card_id));milestone=db.get(Milestone,data.milestone_id)
    if not milestone or milestone.job_card_id!=card.id: raise HTTPException(422,"The selected milestone does not belong to this Job Card.")
    assignment=db.scalar(select(Assignment.id).where(Assignment.job_card_id==card.id,Assignment.user_id==user.id,Assignment.active.is_(True)))
    if not assignment: raise HTTPException(403,"You are not actively assigned to this Job Card.")
    if not card.project_start_date<=data.visit_date<=card.project_end_date: raise HTTPException(422,"Planned date must fall within the project dates.")
    conflicts=planner_conflicts(db,user.id,data.visit_date,data.start_time,data.expected_duration_minutes)
    if conflicts and not data.allow_conflict: raise HTTPException(409,{"message":"This time conflicts with another planned visit.","conflicts":conflicts})
    fields=data.model_dump(exclude={"allow_conflict"});row=PlannedVisit(**fields,consultant_id=user.id,created_by=user.id);db.add(row);db.flush();audit(db,user.id,"PLANNED_VISIT_CREATED","PLANNED_VISIT",row.id,f"{card.number} {row.visit_date} {row.start_time}; conflict_override={bool(conflicts)}");planner_notify(db,card,f"Consultant {user.full_name} planned a site visit for {card.number} on {row.visit_date}.");db.commit();db.refresh(row);return row

@app.patch("/api/planned-visits/{visit_id}",response_model=PlannedVisitOut)
def update_planned_visit(visit_id:int,data:PlannedVisitUpdate,db:DB,user:Annotated[User,Depends(current_user)]):
    row=db.get(PlannedVisit,visit_id)
    if not row or row.status=="CANCELLED": raise HTTPException(404,"Planned visit not found.")
    card=mutable_card(assigned_card(db,user,row.job_card_id))
    if user.role not in (Role.ADMIN,Role.MANAGEMENT) and (user.role!=Role.END_USER or row.consultant_id!=user.id): raise HTTPException(403,"Only the assigned consultant or authorized Admin/Management can update this plan.")
    changes=data.model_dump(exclude_unset=True);reason=changes.pop("reason",None);allow_conflict=changes.pop("allow_conflict",False);next_status=changes.get("status",row.status)
    changed_plan=any(key in changes and getattr(row,key)!=value for key,value in changes.items() if key not in ("status","actual_date","actual_start_time","actual_duration_minutes","outcome"))
    if (changed_plan or next_status=="CANCELLED") and not (reason and reason.strip()): raise HTTPException(422,"A reason is required when rescheduling or cancelling a visit.")
    if "status" in changes and changes["status"]=="COMPLETED":
        if not all(changes.get(k,getattr(row,k)) is not None for k in ("actual_date","actual_start_time","actual_duration_minutes")): raise HTTPException(422,"Actual date, start time and duration are required to complete a visit.")
        if not (changes.get("outcome",row.outcome) or "").strip(): raise HTTPException(422,"Record visit findings or outcome before completing the planned visit.")
        _require_work_payment(db,card,db.get(Milestone,row.milestone_id))
    target_date=changes.get("visit_date",row.visit_date);target_time=changes.get("start_time",row.start_time);target_duration=changes.get("expected_duration_minutes",row.expected_duration_minutes)
    if not card.project_start_date<=target_date<=card.project_end_date: raise HTTPException(422,"Planned date must fall within the project dates.")
    if next_status=="PLANNED":
        conflicts=planner_conflicts(db,row.consultant_id,target_date,target_time,target_duration,row.id)
        if conflicts and not allow_conflict: raise HTTPException(409,{"message":"This time conflicts with another planned visit.","conflicts":conflicts})
    for key,value in changes.items(): setattr(row,key,value)
    if reason: row.change_reason=reason
    audit(db,user.id,"PLANNED_VISIT_UPDATED","PLANNED_VISIT",row.id,f"status={row.status}; reason={reason or 'details updated'}; conflict_override={allow_conflict}")
    planner_notify(db,card,f"The planned visit for {card.number} on {row.visit_date} is now {row.status.lower()}. {reason or ''}".strip());db.commit();db.refresh(row);return row

@app.post("/api/job-cards/{card_id}/visits/draft",response_model=VisitOut,status_code=201)
def save_draft(card_id:int,data:VisitCreate,db:DB,user:Annotated[User,Depends(require(Role.END_USER))]):
    card=mutable_card(assigned_card(db,user,card_id)); milestone=db.get(Milestone,data.milestone_id)
    if not milestone or milestone.job_card_id!=card_id: raise HTTPException(422,"The selected milestone is not available for this Job Card.")
    if not card.project_start_date<=data.visit_date<=card.project_end_date: raise HTTPException(422,"Visit date must fall within the project dates.")
    team=visit_team_values(db,card_id,data)
    _require_work_payment(db,card,milestone)
    attachment={}
    if any((data.attachment_name,data.attachment_mime_type,data.attachment_base64)):
        if not all((data.attachment_name,data.attachment_mime_type,data.attachment_base64)): raise HTTPException(422,"Visit attachment name, type, and content are all required.")
        try: attachment={"attachment_name":data.attachment_name,"attachment_mime_type":data.attachment_mime_type,"attachment_key":save_file(data.attachment_name,data.attachment_mime_type,base64.b64decode(data.attachment_base64,validate=True))}
        except Exception: raise HTTPException(422,"The visit attachment is invalid.")
    next_number=int(db.scalar(select(func.coalesce(func.max(Visit.visit_number),0)+1).where(Visit.job_card_id==card_id)) or 1)
    visit=Visit(job_card_id=card_id,milestone_id=milestone.id,visit_number=next_number,visit_date=data.visit_date,start_time=data.start_time,end_time=data.end_time,location=data.location or card.location,findings=data.findings,working_minutes=visit_duration(data),work_details=data.work_details,rating_code=data.rating_code,rating_description=RATINGS[data.rating_code],remarks=data.remarks,created_by=user.id,status="DRAFT",**team,**attachment); db.add(visit); db.flush(); audit(db,user.id,"VISIT_DRAFT_CREATED","VISIT",visit.id,f"Visit {next_number}"); db.commit(); db.refresh(visit); return visit

@app.patch("/api/visits/{visit_id}",response_model=VisitOut)
def edit_visit(visit_id:int,data:VisitUpdate,db:DB,user:Annotated[User,Depends(require(Role.END_USER))]):
    visit=db.get(Visit,visit_id)
    if not visit or visit.created_by!=user.id or visit.status not in ("DRAFT","RETURNED","REJECTED"): raise HTTPException(409,"Only your draft or returned visits can be edited.")
    card=mutable_card(assigned_card(db,user,visit.job_card_id)); milestone=db.get(Milestone,data.milestone_id)
    if not milestone or milestone.job_card_id!=card.id or not card.project_start_date<=data.visit_date<=card.project_end_date: raise HTTPException(422,"Invalid milestone or visit date.")
    _require_work_payment(db,card,milestone)
    old={"milestone_id":visit.milestone_id,"visit_date":str(visit.visit_date),"working_minutes":visit.working_minutes,"rating_code":visit.rating_code,"location":visit.location,**{field:getattr(visit,field) for field,_ in VISIT_TEAM_FIELDS}};work_details_changed=visit.work_details!=data.work_details;team=visit_team_values(db,card.id,data,visit)
    visit.milestone_id=data.milestone_id; visit.visit_date=data.visit_date;visit.start_time=data.start_time;visit.end_time=data.end_time;visit.location=data.location or card.location;visit.findings=data.findings; visit.working_minutes=visit_duration(data); visit.work_details=data.work_details; visit.rating_code=data.rating_code; visit.rating_description=RATINGS[data.rating_code]; visit.remarks=data.remarks
    for field,value in team.items(): setattr(visit,field,value)
    if any((data.attachment_name,data.attachment_mime_type,data.attachment_base64)):
        if not all((data.attachment_name,data.attachment_mime_type,data.attachment_base64)): raise HTTPException(422,"Visit attachment name, type, and content are all required.")
        try: visit.attachment_name=data.attachment_name;visit.attachment_mime_type=data.attachment_mime_type;visit.attachment_key=save_file(data.attachment_name,data.attachment_mime_type,base64.b64decode(data.attachment_base64,validate=True))
        except Exception: raise HTTPException(422,"The visit attachment is invalid.")
    changes=[]
    for field,previous in old.items():
        current=getattr(visit,field)
        if str(previous)!=str(current): changes.append(f"{field}:{previous}->{current}")
    if work_details_changed: changes.append("work_details updated")
    audit(db,user.id,"VISIT_UPDATED","VISIT",visit.id,"; ".join(changes) or "No field changes")
    db.commit(); db.refresh(visit); return visit

@app.get("/api/visits/review-queue")
def visit_review_queue(db:DB,user:Annotated[User,Depends(require(Role.MANAGEMENT))],page:int=Query(default=1,ge=1),page_size:int=Query(default=25,ge=1,le=100)):
    base=select(Visit,JobCard.number).join(JobCard,JobCard.id==Visit.job_card_id).where(Visit.status=="SUBMITTED",Visit.deleted_at==None,JobCard.deleted_at==None)
    total=int(db.scalar(select(func.count()).select_from(base.subquery())) or 0)
    rows=db.execute(base.order_by(Visit.created_at,Visit.id).offset((page-1)*page_size).limit(page_size))
    items=[{**VisitOut.model_validate(visit).model_dump(),"job_card_number":number} for visit,number in rows]
    return {"items":items,"total":total,"page":page,"page_size":page_size}

@app.get("/api/visits/{visit_id}/review-history")
def visit_review_history(visit_id:int,db:DB,user:Annotated[User,Depends(current_user)]):
    visit=db.get(Visit,visit_id)
    if not visit or visit.deleted_at: raise HTTPException(404,"Visit not found.")
    assigned_card(db,user,visit.job_card_id)
    return list(db.scalars(select(VisitReviewHistory).where(VisitReviewHistory.visit_id==visit_id).order_by(VisitReviewHistory.cycle,VisitReviewHistory.id)))

def record_visit_review(db:Session,visit:Visit,decision:str,comment:str|None,user:User):
    if visit.status!="SUBMITTED": raise HTTPException(409,"Only submitted visits can be reviewed.")
    if decision not in ("APPROVED","RETURNED","REJECTED"): raise HTTPException(422,"Invalid visit review decision.")
    if decision in ("RETURNED","REJECTED") and not (comment and comment.strip()): raise HTTPException(422,"A reason is required when returning or rejecting a visit.")
    cycle=int(db.scalar(select(func.coalesce(func.max(VisitReviewHistory.cycle),0)).where(VisitReviewHistory.visit_id==visit.id)) or 0)+1
    db.add(VisitReviewHistory(visit_id=visit.id,cycle=cycle,decision=decision,comment=comment,decided_by=user.id))
    previous=visit.status;visit.status="APPROVED" if decision=="APPROVED" else "RETURNED"
    visit.rejection_reason=comment if decision in ("RETURNED","REJECTED") else None;visit.rejected_by=user.id if decision in ("RETURNED","REJECTED") else None;visit.rejected_at=datetime.now(timezone.utc) if decision in ("RETURNED","REJECTED") else None
    card=db.get(JobCard,visit.job_card_id);audit(db,user.id,f"VISIT_{decision}","VISIT",visit.id,f"{previous}->{visit.status}; {comment or ''}")
    notify(db,visit.created_by,f"Visit {visit.visit_number} was {decision.lower()}.{(' '+comment) if comment else ''}",f"/job-cards/{card.id}")
    db.commit();db.refresh(visit);return visit

@app.post("/api/visits/{visit_id}/review",response_model=VisitOut)
def decide_visit_review(visit_id:int,data:VisitReviewDecision,db:DB,user:Annotated[User,Depends(require(Role.MANAGEMENT,Role.ADMIN))]):
    visit=db.get(Visit,visit_id)
    if not visit or visit.deleted_at: raise HTTPException(404,"Visit not found.")
    assigned_card(db,user,visit.job_card_id)
    return record_visit_review(db,visit,data.decision,data.comment,user)

@app.post("/api/visits/{visit_id}/submit",response_model=VisitOut)
def submit_visit(visit_id:int,db:DB,user:Annotated[User,Depends(require(Role.END_USER))]):
    visit=db.get(Visit,visit_id)
    if not visit or visit.created_by!=user.id: raise HTTPException(404,"Draft visit not found.")
    if visit.status!="DRAFT": raise HTTPException(409,"Only draft visits can be submitted.")
    card=mutable_card(assigned_card(db,user,visit.job_card_id))
    if not card.project_start_date<=visit.visit_date<=card.project_end_date: raise HTTPException(422,"Visit date must fall within the project dates.")
    if visit.working_minutes<=0: raise HTTPException(422,"A submitted visit must have a positive duration.")
    _require_work_payment(db,card,db.get(Milestone,visit.milestone_id))
    visit.status="SUBMITTED"; card.status="IN_PROGRESS"; audit(db,user.id,"VISIT_SUBMITTED","VISIT",visit.id,f"Visit {visit.visit_number}"); notify(db,card.created_by,f"Visit {visit.visit_number} was submitted for Job Card {card.number}.",f"/job-cards/{card.id}"); db.commit(); db.refresh(visit); return visit

@app.get("/api/job-cards/{card_id}/visits",response_model=list[VisitOut])
def list_visits(card_id:int,db:DB,user:Annotated[User,Depends(current_user)]):
    assigned_card(db,user,card_id); return list(db.scalars(select(Visit).where(Visit.job_card_id==card_id,Visit.deleted_at==None).order_by(Visit.visit_number.desc())))

@app.get("/api/visits",response_model=list[VisitOut])
def list_user_visits(db:DB,user:Annotated[User,Depends(require(Role.ADMIN,Role.MANAGEMENT,Role.END_USER))],status_filter:str|None=Query(default=None,alias="status"),page:int=Query(default=1,ge=1),page_size:int=Query(default=25,ge=1,le=100)):
    query=select(Visit,JobCard.number).join(JobCard,JobCard.id==Visit.job_card_id).where(Visit.deleted_at==None,JobCard.deleted_at==None)
    if user.role==Role.END_USER: query=query.where(Visit.created_by==user.id)
    if status_filter: query=query.where(Visit.status==status_filter)
    return [{**VisitOut.model_validate(v).model_dump(),"job_card_number":number} for v,number in db.execute(query.order_by(Visit.visit_date.desc(),Visit.visit_number.desc()).offset((page-1)*page_size).limit(page_size))]

@app.get("/api/visits/{visit_id}/attachment")
def download_visit_attachment(visit_id:int,db:DB,user:Annotated[User,Depends(current_user)]):
    visit=db.get(Visit,visit_id)
    if not visit or visit.deleted_at or not visit.attachment_key: raise HTTPException(404,"Visit attachment not found.")
    assigned_card(db,user,visit.job_card_id)
    from .storage import ROOT
    path=ROOT/visit.attachment_key
    if not path.is_file(): raise HTTPException(404,"Visit attachment file not found.")
    return Response(content=path.read_bytes(),media_type=visit.attachment_mime_type,headers={"Content-Disposition":f'attachment; filename="{visit.attachment_name}"'})

@app.post("/api/visits/{visit_id}/reject",response_model=VisitOut)
def reject_visit(visit_id:int,data:VisitRejectIn,db:DB,user:Annotated[User,Depends(require(Role.MANAGEMENT))]):
    visit=db.get(Visit,visit_id)
    if not visit or visit.deleted_at: raise HTTPException(404,"Visit not found.")
    assigned_card(db,user,visit.job_card_id)
    return record_visit_review(db,visit,"REJECTED",data.reason,user)
@app.post("/api/visits/{visit_id}/resubmit",response_model=VisitOut)
def resubmit_visit(visit_id:int,db:DB,user:Annotated[User,Depends(require(Role.END_USER))]):
    visit=db.get(Visit,visit_id)
    if not visit or visit.deleted_at or visit.created_by!=user.id: raise HTTPException(404,"Returned visit not found.")
    if visit.status not in ("RETURNED","REJECTED"): raise HTTPException(409,"Only returned visits can be resubmitted.")
    card=mutable_card(assigned_card(db,user,visit.job_card_id))
    _require_work_payment(db,card,db.get(Milestone,visit.milestone_id))
    if not card.project_start_date<=visit.visit_date<=card.project_end_date or visit.working_minutes<=0: raise HTTPException(422,"Correct the visit date and enter a positive duration before resubmitting.")
    visit.status="SUBMITTED"; visit.rejection_reason=None; visit.rejected_by=None; visit.rejected_at=None; card.status="IN_PROGRESS"; audit(db,user.id,"VISIT_RESUBMITTED","VISIT",visit.id); notify(db,card.created_by,f"Visit {visit.visit_number} was resubmitted for Job Card {card.number}.",f"/job-cards/{card.id}"); db.commit(); db.refresh(visit); return visit
@app.delete("/api/visits/{visit_id}",status_code=204)
def archive_visit(visit_id:int,db:DB,user:Annotated[User,Depends(require(Role.END_USER))]):
    visit=db.get(Visit,visit_id)
    if not visit or visit.created_by!=user.id or visit.status!="DRAFT": raise HTTPException(404,"Draft visit not found.")
    mutable_card(assigned_card(db,user,visit.job_card_id))
    visit.deleted_at=datetime.now(timezone.utc); visit.deleted_by=user.id; audit(db,user.id,"VISIT_ARCHIVED","VISIT",visit.id); db.commit()

@app.get("/api/job-cards/{card_id}/mandays")
def get_mandays(card_id:int,db:DB,user:Annotated[User,Depends(current_user)]): return manday_summary(db,assigned_card(db,user,card_id))

@app.post("/api/job-cards/{card_id}/documents",response_model=DocumentOut,status_code=201)
def upload_document(card_id:int,data:DocumentUpload,db:DB,user:Annotated[User,Depends(require(Role.END_USER))]):
    card=mutable_card(assigned_card(db,user,card_id));_require_work_payment(db,card)
    try: content=base64.b64decode(data.content_base64,validate=True); storage_key=save_file(data.file_name,data.mime_type,content)
    except ValueError as exc: raise HTTPException(422,str(exc))
    except Exception: raise HTTPException(422,"The uploaded file is invalid.")
    prior=int(db.scalar(select(func.coalesce(func.max(Document.version),0)).where(Document.job_card_id==card_id,Document.document_type==data.document_type)) or 0)
    document=Document(job_card_id=card_id,document_type=data.document_type,file_name=data.file_name,storage_key=storage_key,file_size=len(content),mime_type=data.mime_type,version=prior+1,status="RESUBMITTED" if prior else "SUBMITTED",remarks=data.remarks,uploaded_by=user.id)
    db.add(document); db.flush(); audit(db,user.id,"DOCUMENT_UPLOADED","DOCUMENT",document.id,f"{data.document_type} v{document.version}"); notify(db,card_created_by(db,card_id),f"{data.document_type} was submitted for review.",f"/job-cards/{card_id}"); db.commit(); db.refresh(document); return document

@app.get("/api/job-cards/{card_id}/documents",response_model=list[DocumentOut])
def list_documents(card_id:int,db:DB,user:Annotated[User,Depends(current_user)]):
    assigned_card(db,user,card_id); return list(db.scalars(select(Document).where(Document.job_card_id==card_id,Document.deleted_at==None).order_by(Document.document_type,Document.version.desc())))

@app.get("/api/documents",response_model=list[DocumentOut])
def list_user_documents(db:DB,user:Annotated[User,Depends(require(Role.ADMIN,Role.MANAGEMENT,Role.END_USER))],status_filter:str|None=Query(default=None,alias="status"),page:int=Query(default=1,ge=1),page_size:int=Query(default=25,ge=1,le=100)):
    query=select(Document,JobCard.number).join(JobCard,JobCard.id==Document.job_card_id).where(Document.deleted_at==None,JobCard.deleted_at==None)
    if user.role==Role.END_USER: query=query.where(Document.uploaded_by==user.id)
    if status_filter: query=query.where(Document.status==status_filter)
    return [{**DocumentOut.model_validate(d).model_dump(),"job_card_number":number} for d,number in db.execute(query.order_by(Document.uploaded_at.desc()).offset((page-1)*page_size).limit(page_size))]

@app.get("/api/documents/review-queue")
def document_review_queue(db:DB,user:Annotated[User,Depends(require(Role.ADMIN,Role.MANAGEMENT))],page:int=Query(default=1,ge=1),page_size:int=Query(default=25,ge=1,le=100)):
    latest=(select(Document.id.label("id"),func.row_number().over(partition_by=(Document.job_card_id,Document.document_type),order_by=(Document.version.desc(),Document.id.desc())).label("rank"))
        .where(Document.deleted_at==None).subquery())
    conditions=(JobCard.deleted_at==None,Document.status.in_(("SUBMITTED","RESUBMITTED")),Document.id.in_(select(latest.c.id).where(latest.c.rank==1)))
    total=int(db.scalar(select(func.count(Document.id)).join(JobCard,JobCard.id==Document.job_card_id).where(*conditions)) or 0)
    query=(select(Document,JobCard.number).join(JobCard,JobCard.id==Document.job_card_id)
        .where(*conditions)
        .order_by(Document.uploaded_at.desc(),Document.id.desc()).offset((page-1)*page_size).limit(page_size))
    items=[{**DocumentOut.model_validate(document).model_dump(),"job_card_number":number} for document,number in db.execute(query)]
    return {"items":items,"total":total,"page":page,"page_size":page_size}

@app.get("/api/documents/{document_id}/download")
def download_document(document_id:int,db:DB,user:Annotated[User,Depends(current_user)]):
    document=db.get(Document,document_id)
    if not document or document.deleted_at: raise HTTPException(404,"Document not found.")
    assigned_card(db,user,document.job_card_id)
    from .storage import ROOT
    path=ROOT/document.storage_key
    if not path.is_file(): raise HTTPException(404,"Document file not found.")
    return Response(content=path.read_bytes(),media_type=document.mime_type,headers={"Content-Disposition":f'attachment; filename="{document.file_name}"'})
@app.delete("/api/documents/{document_id}",status_code=204)
def archive_document(document_id:int,db:DB,user:Annotated[User,Depends(require(Role.END_USER))]):
    document=db.get(Document,document_id)
    if not document or document.deleted_at or document.uploaded_by!=user.id: raise HTTPException(404,"Document not found.")
    mutable_card(assigned_card(db,user,document.job_card_id))
    if document.status=="APPROVED": raise HTTPException(409,"Approved documents cannot be archived.")
    document.deleted_at=datetime.now(timezone.utc); document.deleted_by=user.id; audit(db,user.id,"DOCUMENT_ARCHIVED","DOCUMENT",document.id); db.commit()

@app.post("/api/documents/{document_id}/review",response_model=DocumentOut)
def review_document(document_id:int,data:DocumentReview,db:DB,user:Annotated[User,Depends(require(Role.ADMIN,Role.MANAGEMENT))]):
    document=db.get(Document,document_id)
    if not document or document.deleted_at: raise HTTPException(404,"Document not found.")
    if document.status not in ("SUBMITTED","RESUBMITTED"): raise HTTPException(409,"Only submitted document versions can be reviewed.")
    mutable_card(assigned_card(db,user,document.job_card_id))
    latest_id=db.scalar(select(Document.id).where(Document.job_card_id==document.job_card_id,Document.document_type==document.document_type,Document.deleted_at==None).order_by(Document.version.desc(),Document.id.desc()).limit(1))
    if latest_id!=document.id: raise HTTPException(409,"Only the latest document version can be reviewed.")
    previous=document.status
    document.status=data.decision; document.remarks=data.comment;document.reviewed_by=user.id;document.reviewed_at=datetime.now(timezone.utc); audit(db,user.id,f"DOCUMENT_{data.decision}","DOCUMENT",document.id,f"status:{previous}->{data.decision}; comment:{data.comment}"); owner=document_owner(db,document.job_card_id); notify(db,owner,f"Your {document.document_type} was {data.decision.lower()}. {data.comment}",f"/job-cards/{document.job_card_id}"); db.commit(); db.refresh(document); return document

STAGES=("CONSULTANT_CONFIRMATION","UNIT_REPRESENTATIVE","DIRECTOR_REVIEW")
STAGE_ROLES={"CONSULTANT_CONFIRMATION":Role.END_USER,"UNIT_REPRESENTATIVE":Role.UNIT_REPRESENTATIVE,"DIRECTOR_REVIEW":Role.DIRECTOR}
def required_documents_ready(db:Session,card_id:int):
    for document_type in ("Case Study","Technology Recommendation"):
        latest=db.scalar(select(Document).where(Document.job_card_id==card_id,Document.document_type==document_type,Document.deleted_at==None).order_by(Document.version.desc(),Document.id.desc()).limit(1))
        if not latest or latest.status!="APPROVED": return False
    return True
def milestones_complete(db:Session,card_id:int):
    items=list(db.scalars(select(Milestone).where(Milestone.job_card_id==card_id)))
    return bool(items) and all(milestone_summary(db,item)["status"]=="COMPLETED" for item in items)
def approval_owner(db:Session,card:JobCard,stage:str):
    if stage=="CONSULTANT_CONFIRMATION": return document_owner(db,card.id)
    return card.unit_representative_id if stage=="UNIT_REPRESENTATIVE" else card.director_id
@app.post("/api/job-cards/{card_id}/approvals/{stage}",response_model=ApprovalOut,status_code=201)
def decide_approval(card_id:int,stage:str,data:ApprovalAction,db:DB,user:Annotated[User,Depends(current_user)]):
    if stage not in STAGES: raise HTTPException(422,"Invalid approval stage.")
    card=mutable_card(assigned_card(db,user,card_id))
    if user.role!=STAGE_ROLES[stage] or user.id!=approval_owner(db,card,stage): raise HTTPException(403,"You are not the assigned approver for this stage.")
    if stage=="CONSULTANT_CONFIRMATION":
        if data.decision!="APPROVED": raise HTTPException(422,"Consultant confirmation can only confirm the submission.")
        _require_all_project_payments(db,card)
        if not milestones_complete(db,card_id) or not required_documents_ready(db,card_id): raise HTTPException(422,"All milestones and required approved documents are needed before consultant confirmation.")
    current_cycle=int(db.scalar(select(func.coalesce(func.max(Approval.cycle),0)).where(Approval.job_card_id==card_id)) or 0) or 1
    if stage=="CONSULTANT_CONFIRMATION" and db.scalar(select(Approval.id).where(Approval.job_card_id==card_id,Approval.cycle==current_cycle,Approval.stage==stage)) and card.status in ("REJECTED","IN_PROGRESS"): current_cycle+=1
    previous=STAGES.index(stage)-1
    if previous>=0 and not db.scalar(select(Approval).where(Approval.job_card_id==card_id,Approval.cycle==current_cycle,Approval.stage==STAGES[previous],Approval.decision=="APPROVED")): raise HTTPException(422,"The previous approval stage must be approved in this review cycle.")
    if db.scalar(select(Approval).where(Approval.job_card_id==card_id,Approval.cycle==current_cycle,Approval.stage==stage)): raise HTTPException(409,"This approval stage has already been decided in the current review cycle.")
    if data.decision=="REJECTED" and not data.comment: raise HTTPException(422,"A rejection reason is required.")
    approval=Approval(job_card_id=card_id,cycle=current_cycle,stage=stage,decision=data.decision,comment=data.comment,decided_by=user.id); db.add(approval)
    card.status="REJECTED" if data.decision=="REJECTED" else ("CLOSURE_PENDING" if stage=="DIRECTOR_REVIEW" else "PENDING_APPROVAL")
    db.flush(); audit(db,user.id,f"APPROVAL_{data.decision}","APPROVAL",approval.id,stage)
    if data.decision=="REJECTED":
        notify(db,document_owner(db,card_id),f"{stage.replace('_',' ').title()} rejected Job Card {card.number}. {data.comment}",f"/job-cards/{card_id}")
    elif stage=="CONSULTANT_CONFIRMATION":
        notify(db,approval_owner(db,card,"UNIT_REPRESENTATIVE"),f"Unit Representative approval is required for Job Card {card.number}.",f"/job-cards/{card_id}")
    elif stage=="UNIT_REPRESENTATIVE":
        notify(db,approval_owner(db,card,"DIRECTOR_REVIEW"),f"Director approval is required for Job Card {card.number}.",f"/job-cards/{card_id}")
    else:
        notify(db,document_owner(db,card_id),f"Job Card {card.number} received Director approval and is ready for closure activities.",f"/job-cards/{card_id}")
        notify(db,card.created_by,f"Job Card {card.number} received final Director approval and is awaiting client closure records.",f"/job-cards/{card_id}")
    db.commit(); db.refresh(approval); return approval

@app.get("/api/job-cards/{card_id}/approvals",response_model=list[ApprovalOut])
def list_approvals(card_id:int,db:DB,user:Annotated[User,Depends(current_user)]):
    assigned_card(db,user,card_id); return list(db.scalars(select(Approval).where(Approval.job_card_id==card_id).order_by(Approval.id)))

def _workflow_out(db:Session,action:WorkflowAction)->WorkflowActionOut:
    department=db.get(Department,action.department_id);assignee=db.get(User,action.assigned_user_id) if action.assigned_user_id else None
    due=action.due_at.replace(tzinfo=timezone.utc) if action.due_at and action.due_at.tzinfo is None else action.due_at
    state="OVERDUE" if action.status not in ("COMPLETED","RETURNED","REJECTED","CANCELLED") and due and due<datetime.now(timezone.utc) else action.status
    return WorkflowActionOut(**{**action.__dict__,"due_at":due,"department_name":department.name if department else "","assigned_user_name":assignee.full_name if assignee else None,"status":state})

def _action_visible(action:WorkflowAction,user:User)->bool:
    if user.role in (Role.ADMIN,Role.MANAGEMENT): return True
    return action.created_by==user.id or action.assigned_user_id==user.id or (user.department_id is not None and user.department_id==action.department_id)

def _action_step(db:Session,template:WorkflowTemplate,step_index:int,links:dict,created_by:int,assigned_user_id:int|None,previous:WorkflowAction|None=None)->WorkflowAction:
    step=template.steps[step_index];department=db.scalar(select(Department).where(Department.code==step["department_code"],Department.active==True))
    if not department: raise HTTPException(409,f"Workflow department {step['department_code']} is inactive or missing.")
    assignee=db.get(User,assigned_user_id) if assigned_user_id else None
    if assigned_user_id and (not assignee or assignee.status!=AccountStatus.ACTIVE or assignee.department_id!=department.id or (step.get("role") and assignee.role!=step["role"])):
        raise HTTPException(422,"The assigned user must be active and match the workflow department and role.")
    due_at=datetime.now(timezone.utc)+timedelta(days=step["due_days"]) if step.get("due_days") else None
    action=WorkflowAction(action_number=next_business_number(db,"action"),template_code=template.code,workflow_step=step_index+1,
        ticket_id=links.get("ticket_id"),enquiry_id=links.get("enquiry_id"),job_card_id=links.get("job_card_id"),milestone_id=links.get("milestone_id"),
        action_type=step["action_type"],title=step["title"],description=step.get("description"),department_id=department.id,assigned_role=step.get("role"),assigned_user_id=assigned_user_id,
        priority="NORMAL",due_at=due_at,status="OPEN",parent_action_id=(previous.parent_action_id or previous.id) if previous else None,previous_action_id=previous.id if previous else None,created_by=created_by)
    db.add(action);db.flush()
    if previous: previous.next_action_id=action.id
    recipients=[assignee] if assignee else list(db.scalars(select(User).where(User.department_id==department.id,User.status==AccountStatus.ACTIVE,*( [User.role==step["role"]] if step.get("role") else [] ))))
    for recipient in recipients:
        if recipient: notify(db,recipient.id,f"Action {action.action_number} is ready: {action.title}.","/actions")
    return action

def _start_triggered_workflows(db:Session,event:str,card:JobCard,actor:User,milestone:Milestone|None=None):
    for template in db.scalars(select(WorkflowTemplate).where(WorkflowTemplate.active.is_(True),WorkflowTemplate.trigger_event==event)):
        duplicate=select(WorkflowAction.id).where(WorkflowAction.template_code==template.code,WorkflowAction.job_card_id==card.id,WorkflowAction.milestone_id==(milestone.id if milestone else None))
        if db.scalar(duplicate):continue
        action=_action_step(db,template,0,{"job_card_id":card.id,"milestone_id":milestone.id if milestone else None},actor.id,None)
        audit(db,actor.id,"WORKFLOW_TRIGGERED","ACTION",None,f"{template.code}; event={event}; action={action.action_number}")

@app.get("/api/workflow-templates",response_model=list[WorkflowOut])
def list_workflow_templates(db:DB,user:Annotated[User,Depends(current_user)]):
    query=select(WorkflowTemplate).order_by(WorkflowTemplate.code)
    if user.role!=Role.ADMIN: query=query.where(WorkflowTemplate.active==True)
    return [WorkflowOut(**item.__dict__) for item in db.scalars(query)]

@app.put("/api/workflow-templates/{code}",response_model=WorkflowOut)
def save_workflow_template(code:str,data:WorkflowConfig,db:DB,admin:Annotated[User,Depends(require(Role.ADMIN))]):
    if code.upper()!=data.code: raise HTTPException(422,"Workflow path code must match the body code.")
    for step in data.steps:
        if not db.scalar(select(Department.id).where(Department.code==step.department_code,Department.active==True)): raise HTTPException(422,f"Active department {step.department_code} does not exist.")
    item=db.scalar(select(WorkflowTemplate).where(WorkflowTemplate.code==data.code));values=data.model_dump(mode="json",exclude={"code"})
    if item:
        if db.scalar(select(WorkflowAction.id).where(WorkflowAction.template_code==item.code,WorkflowAction.status.not_in(("COMPLETED","RETURNED","REJECTED","CANCELLED")))): raise HTTPException(409,"A workflow with open actions cannot be reconfigured.")
        for field,value in values.items():setattr(item,field,value)
    else:item=WorkflowTemplate(code=data.code,updated_by=admin.id,**values);db.add(item)
    item.updated_by=admin.id;db.flush();audit(db,admin.id,"WORKFLOW_TEMPLATE_SAVED","WORKFLOW_TEMPLATE",item.id,item.code);db.commit();db.refresh(item);return WorkflowOut(**item.__dict__)

@app.post("/api/actions/start",response_model=WorkflowActionOut,status_code=201)
def start_workflow_action(data:ActionStart,db:DB,user:Annotated[User,Depends(require(Role.ADMIN,Role.MARKETING))]):
    template=db.scalar(select(WorkflowTemplate).where(WorkflowTemplate.code==data.template_code,WorkflowTemplate.active==True))
    if not template: raise HTTPException(404,"Active workflow template not found.")
    if not any((data.ticket_id,data.enquiry_id,data.job_card_id)): raise HTTPException(422,"Link an action to a ticket, enquiry, or Job Card.")
    ticket=db.get(Ticket,data.ticket_id) if data.ticket_id else None
    enquiry=db.get(Enquiry,data.enquiry_id) if data.enquiry_id else None
    card=db.get(JobCard,data.job_card_id) if data.job_card_id else None
    if data.ticket_id and not ticket: raise HTTPException(404,"Ticket not found.")
    if ticket:_get_visible_ticket(db,ticket.id,user)
    if data.enquiry_id and (not enquiry or not _visible_enquiry(enquiry,user)): raise HTTPException(404,"Enquiry not found.")
    if data.job_card_id: assigned_card(db,user,data.job_card_id)
    if ticket and enquiry and ticket.enquiry_id!=enquiry.id: raise HTTPException(422,"Ticket and Enquiry links do not match.")
    if ticket and card and ticket.job_card_id not in (None,card.id): raise HTTPException(422,"Ticket and Job Card links do not match.")
    if enquiry and card and db.scalar(select(JobCard.id).where(JobCard.id==card.id,JobCard.source_enquiry_id==enquiry.id)) is None and not db.scalar(select(Ticket.id).where(Ticket.enquiry_id==enquiry.id,Ticket.job_card_id==card.id)): raise HTTPException(422,"Enquiry and Job Card links do not match.")
    if data.milestone_id and (not card or not db.scalar(select(Milestone.id).where(Milestone.id==data.milestone_id,Milestone.job_card_id==card.id))): raise HTTPException(422,"Milestone must belong to the linked Job Card.")
    links={"ticket_id":ticket.id if ticket else None,"enquiry_id":enquiry.id if enquiry else None,"job_card_id":card.id if card else None,"milestone_id":data.milestone_id}
    action=_action_step(db,template,0,links,user.id,data.assigned_user_id);audit(db,user.id,"ACTION_CREATED","ACTION",None,action.action_number);db.commit();db.refresh(action);return _workflow_out(db,action)

@app.get("/api/actions",response_model=list[WorkflowActionOut])
def list_workflow_actions(db:DB,user:Annotated[User,Depends(current_user)],status_filter:str|None=Query(default=None,alias="status")):
    query=select(WorkflowAction).order_by(WorkflowAction.created_at.desc())
    if user.role not in (Role.ADMIN,Role.MANAGEMENT): query=query.where(or_(WorkflowAction.assigned_user_id==user.id,WorkflowAction.created_by==user.id,WorkflowAction.department_id==user.department_id if user.department_id else False))
    if status_filter: query=query.where(WorkflowAction.status==status_filter)
    return [_workflow_out(db,a) for a in db.scalars(query.limit(250))]

@app.get("/api/actions/{action_id}/attachments",response_model=list[WorkflowAttachmentOut])
def list_action_attachments(action_id:UUID,db:DB,user:Annotated[User,Depends(current_user)]):
    action=db.get(WorkflowAction,action_id)
    if not action or not _action_visible(action,user):raise HTTPException(404,"Action not found.")
    return list(db.scalars(select(WorkflowActionAttachment).where(WorkflowActionAttachment.action_id==action_id).order_by(WorkflowActionAttachment.uploaded_at.desc())))

@app.post("/api/actions/{action_id}/attachments",response_model=WorkflowAttachmentOut,status_code=201)
def upload_action_attachment(action_id:UUID,data:RecordFileUpload,db:DB,user:Annotated[User,Depends(current_user)]):
    action=db.scalar(select(WorkflowAction).where(WorkflowAction.id==action_id).with_for_update())
    if not action or not _action_visible(action,user):raise HTTPException(404,"Action not found.")
    if action.status in ("COMPLETED","RETURNED","REJECTED","CANCELLED"):raise HTTPException(409,"Attachments cannot be added to a completed action.")
    if user.role!=Role.ADMIN and action.assigned_user_id!=user.id and action.created_by!=user.id:raise HTTPException(403,"Claim this action before adding attachments.")
    key,size=_save_encoded_file(data.file_name,data.mime_type,data.content_base64)
    item=WorkflowActionAttachment(action_id=action.id,file_name=data.file_name,storage_key=key,file_size=size,mime_type=data.mime_type,uploaded_by=user.id)
    db.add(item);db.flush();audit(db,user.id,"ACTION_ATTACHMENT_UPLOADED","ACTION",None,f"{action.action_number}: {item.file_name}");db.commit();db.refresh(item);return item

@app.get("/api/action-attachments/{attachment_id}/download")
def download_action_attachment(attachment_id:int,db:DB,user:Annotated[User,Depends(current_user)]):
    item=db.get(WorkflowActionAttachment,attachment_id)
    if not item:raise HTTPException(404,"Attachment not found.")
    action=db.get(WorkflowAction,item.action_id)
    if not action or not _action_visible(action,user):raise HTTPException(404,"Attachment not found.")
    return _download_attachment(item.file_name,item.mime_type,item.storage_key)

@app.post("/api/actions/{action_id}/claim",response_model=WorkflowActionOut)
def claim_workflow_action(action_id:UUID,db:DB,user:Annotated[User,Depends(current_user)]):
    action=db.scalar(select(WorkflowAction).where(WorkflowAction.id==action_id).with_for_update())
    if not action or not _action_visible(action,user): raise HTTPException(404,"Action not found.")
    if action.status not in ("OPEN","WAITING") or action.assigned_user_id not in (None,user.id): raise HTTPException(409,"This action is no longer available to claim.")
    if action.assigned_user_id==user.id: return _workflow_out(db,action)
    if user.department_id!=action.department_id or (action.assigned_role and user.role.value!=action.assigned_role): raise HTTPException(403,"You are not eligible to claim this department action.")
    action.assigned_user_id=user.id;action.status="IN_PROGRESS";action.started_at=datetime.now(timezone.utc);audit(db,user.id,"ACTION_CLAIMED","ACTION",None,action.action_number);db.commit();db.refresh(action);return _workflow_out(db,action)

@app.patch("/api/actions/{action_id}",response_model=WorkflowActionOut)
def update_workflow_action(action_id:UUID,data:ActionStatusUpdate,db:DB,user:Annotated[User,Depends(current_user)]):
    action=db.scalar(select(WorkflowAction).where(WorkflowAction.id==action_id).with_for_update())
    if not action or not _action_visible(action,user):raise HTTPException(404,"Action not found.")
    if action.status in ("COMPLETED","RETURNED","REJECTED","CANCELLED"):raise HTTPException(409,"This action is closed.")
    if user.role!=Role.ADMIN and action.assigned_user_id!=user.id:raise HTTPException(403,"Claim this action before changing its status.")
    previous=action.status;action.status=data.status
    if data.remarks is not None:action.remarks=data.remarks
    if data.status=="IN_PROGRESS" and action.started_at is None:action.started_at=datetime.now(timezone.utc)
    audit(db,user.id,"ACTION_STATUS_UPDATED","ACTION",None,f"{action.action_number}: {previous} -> {data.status}; {data.remarks or ''}")
    db.commit();db.refresh(action);return _workflow_out(db,action)

@app.post("/api/actions/{action_id}/transfer",response_model=WorkflowActionOut)
def transfer_workflow_action(action_id:UUID,data:ActionTransfer,db:DB,user:Annotated[User,Depends(require(Role.ADMIN,Role.MANAGEMENT,Role.MARKETING))]):
    action=db.scalar(select(WorkflowAction).where(WorkflowAction.id==action_id).with_for_update())
    if not action: raise HTTPException(404,"Action not found.")
    if action.status in ("COMPLETED","RETURNED","REJECTED","CANCELLED"): raise HTTPException(409,"Closed actions cannot be transferred.")
    department=db.scalar(select(Department).where(Department.id==data.department_id,Department.active.is_(True)))
    if not department: raise HTTPException(422,"Transfer target department is inactive or does not exist.")
    assignee=db.get(User,data.assigned_user_id) if data.assigned_user_id else None
    if data.assigned_user_id and (not assignee or assignee.status!=AccountStatus.ACTIVE or assignee.department_id!=department.id or (action.assigned_role and assignee.role.value!=action.assigned_role)):
        raise HTTPException(422,"Transfer target must be an active user in the selected department and match the action role.")
    old_department=action.department_id;old_assignee=action.assigned_user_id
    action.department_id=department.id;action.assigned_user_id=assignee.id if assignee else None
    audit(db,user.id,"ACTION_TRANSFERRED","ACTION",None,f"{action.action_number}: department {old_department}->{department.id}; user {old_assignee}->{action.assigned_user_id}; reason={data.reason}")
    recipients=[assignee] if assignee else list(db.scalars(select(User).where(User.department_id==department.id,User.status==AccountStatus.ACTIVE,*([User.role==action.assigned_role] if action.assigned_role else []))))
    for recipient in recipients:
        if recipient: notify(db,recipient.id,f"Action {action.action_number} was transferred to your queue: {action.title}. Reason: {data.reason}","/actions")
    if old_assignee and old_assignee!=action.assigned_user_id: notify(db,old_assignee,f"Action {action.action_number} was transferred to another owner. Reason: {data.reason}","/actions")
    db.commit();db.refresh(action);return _workflow_out(db,action)

@app.post("/api/actions/{action_id}/decision",response_model=WorkflowActionOut)
def decide_workflow_action(action_id:UUID,data:ActionDecision,db:DB,user:Annotated[User,Depends(current_user)]):
    action=db.scalar(select(WorkflowAction).where(WorkflowAction.id==action_id).with_for_update())
    if not action or not _action_visible(action,user): raise HTTPException(404,"Action not found.")
    if action.status in ("COMPLETED","RETURNED","REJECTED","CANCELLED"): raise HTTPException(409,"This action has already been completed.")
    if data.decision in ("RETURNED","REJECTED","CANCELLED") and not data.remarks: raise HTTPException(422,"A reason is required for this action decision.")
    if user.role!=Role.ADMIN and action.assigned_user_id!=user.id: raise HTTPException(403,"Claim this action before completing it.")
    action.status=data.decision;action.remarks=data.remarks;action.completed_at=datetime.now(timezone.utc)
    if data.decision=="COMPLETED":
        template=db.scalar(select(WorkflowTemplate).where(WorkflowTemplate.code==action.template_code))
        if template and action.workflow_step<len(template.steps):
            next_action=_action_step(db,template,action.workflow_step,{"ticket_id":action.ticket_id,"enquiry_id":action.enquiry_id,"job_card_id":action.job_card_id,"milestone_id":action.milestone_id},user.id,None,action)
        else:next_action=None
    elif data.decision=="RETURNED":
        template=db.scalar(select(WorkflowTemplate).where(WorkflowTemplate.code==action.template_code))
        if template:
            return_target=template.steps[action.workflow_step-1].get("return_to_step") or max(action.workflow_step-1,1)
            next_action=_action_step(db,template,return_target-1,{"ticket_id":action.ticket_id,"enquiry_id":action.enquiry_id,"job_card_id":action.job_card_id,"milestone_id":action.milestone_id},user.id,None,action)
            next_action.title=f"Correction: {next_action.title}"
            next_action.description=f"Returned from {action.action_number}: {data.remarks}\n{next_action.description or ''}"[:10000]
    else:next_action=None
    db.flush();audit(db,user.id,f"ACTION_{data.decision}","ACTION",None,f"{action.action_number}; {data.remarks or ''}")
    db.commit();db.refresh(action);return _workflow_out(db,action)

RECORD_DOCUMENT_TARGETS={"TICKET":"ticket_id","ENQUIRY":"enquiry_id","JOB_CARD":"job_card_id","MILESTONE":"milestone_id","ACTION":"action_id","VISIT":"visit_id","PAYMENT":"payment_id","MILESTONE_REVIEW":"milestone_review_id","VISIT_REVIEW":"visit_review_id","APPROVAL":"approval_id"}
def resolve_record_document_target(db:Session,user:User,record_type:str,record_id:str):
    kind=record_type.upper();column=RECORD_DOCUMENT_TARGETS.get(kind)
    if not column: raise HTTPException(422,"Unsupported document record type.")
    try: key=UUID(record_id) if kind in ("TICKET","ENQUIRY","ACTION") else int(record_id)
    except ValueError: raise HTTPException(404,"Related record not found.")
    owner_card_id=None
    if kind=="TICKET":
        row=_get_visible_ticket(db,key,user);owner_card_id=row.job_card_id
    elif kind=="ENQUIRY":
        row=db.get(Enquiry,key)
        if not row or not _visible_enquiry(row,user): raise HTTPException(404,"Enquiry not found.")
        linked=db.scalar(select(JobCard.id).where(JobCard.source_enquiry_id==row.id));owner_card_id=linked
    elif kind=="JOB_CARD":
        row=assigned_card(db,user,key);owner_card_id=row.id
    elif kind=="MILESTONE":
        row=db.get(Milestone,key)
        if not row: raise HTTPException(404,"Milestone not found.")
        assigned_card(db,user,row.job_card_id);owner_card_id=row.job_card_id
    elif kind=="ACTION":
        row=db.get(WorkflowAction,key)
        if not row or not _action_visible(row,user): raise HTTPException(404,"Action not found.")
        owner_card_id=row.job_card_id
    elif kind=="VISIT":
        row=db.get(Visit,key)
        if not row or row.deleted_at: raise HTTPException(404,"Visit not found.")
        assigned_card(db,user,row.job_card_id);owner_card_id=row.job_card_id
    elif kind=="PAYMENT":
        if user.role==Role.ADMIN: raise HTTPException(403,"Payment records are restricted to Marketing / Finance and Management.")
        row=db.get(Payment,key)
        if not row: raise HTTPException(404,"Payment not found.")
        assigned_card(db,user,row.job_card_id);owner_card_id=row.job_card_id
    elif kind=="MILESTONE_REVIEW":
        row=db.get(MilestoneReviewHistory,key);milestone=db.get(Milestone,row.milestone_id) if row else None
        if not row or not milestone: raise HTTPException(404,"Milestone review not found.")
        assigned_card(db,user,milestone.job_card_id);owner_card_id=milestone.job_card_id
    elif kind=="VISIT_REVIEW":
        row=db.get(VisitReviewHistory,key);visit=db.get(Visit,row.visit_id) if row else None
        if not row or not visit: raise HTTPException(404,"Visit review not found.")
        assigned_card(db,user,visit.job_card_id);owner_card_id=visit.job_card_id
    else:
        row=db.get(Approval,key)
        if not row: raise HTTPException(404,"Approval not found.")
        assigned_card(db,user,row.job_card_id);owner_card_id=row.job_card_id
    return kind,column,key,row,owner_card_id

@app.get("/api/records/{record_type}/{record_id}/documents",response_model=list[RecordDocumentOut])
def list_record_documents(record_type:str,record_id:str,db:DB,user:Annotated[User,Depends(current_user)]):
    _,column,key,_,_=resolve_record_document_target(db,user,record_type,record_id)
    return list(db.scalars(select(RecordDocument).where(getattr(RecordDocument,column)==key).order_by(RecordDocument.document_type,RecordDocument.version.desc())))

@app.post("/api/records/{record_type}/{record_id}/documents",response_model=RecordDocumentOut,status_code=201)
def upload_record_document(record_type:str,record_id:str,data:RecordDocumentUpload,db:DB,user:Annotated[User,Depends(current_user)]):
    kind,column,key,row,owner_card_id=resolve_record_document_target(db,user,record_type,record_id)
    if kind=="PAYMENT" and user.role!=Role.MARKETING: raise HTTPException(403,"Only Marketing / Finance can attach payment documents.")
    if user.role not in (Role.ADMIN,Role.MANAGEMENT,Role.MARKETING,Role.END_USER): raise HTTPException(403,"You cannot upload documents to this record.")
    if data.status=="DRAFT" and user.role!=Role.END_USER: raise HTTPException(403,"Only consultants can save consultant document drafts.")
    if owner_card_id: _require_work_payment(db,db.get(JobCard,owner_card_id),db.get(Milestone,row.id) if kind=="MILESTONE" else None)
    try: content=base64.b64decode(data.content_base64,validate=True);storage_key=save_file(data.file_name,data.mime_type,content)
    except ValueError as exc: raise HTTPException(422,str(exc))
    except Exception: raise HTTPException(422,"The uploaded file is invalid.")
    prior=int(db.scalar(select(func.coalesce(func.max(RecordDocument.version),0)).where(getattr(RecordDocument,column)==key,RecordDocument.document_type==data.document_type)) or 0)
    fields={column:key,"owner_job_card_id":owner_card_id,"document_type":data.document_type,"file_name":data.file_name,"storage_key":storage_key,"file_size":len(content),"mime_type":data.mime_type,"version":prior+1,"status":data.status,"remarks":data.remarks,"uploaded_by":user.id}
    document=RecordDocument(**fields);db.add(document);db.flush();audit(db,user.id,"RECORD_DOCUMENT_UPLOADED","DOCUMENT",document.id,f"{kind}:{record_id}; {data.document_type} v{document.version}")
    if owner_card_id and data.status=="SUBMITTED":
        owner=document_owner(db,owner_card_id) or card_created_by(db,owner_card_id)
        if owner!=user.id: notify(db,owner,f"{data.document_type} was attached to {kind.lower().replace('_',' ')} {record_id}.",f"/job-cards/{owner_card_id}")
    db.commit();db.refresh(document);return document

@app.post("/api/record-documents/{document_id}/submit",response_model=RecordDocumentOut)
def submit_record_document(document_id:int,db:DB,user:Annotated[User,Depends(require(Role.END_USER))]):
    document=db.get(RecordDocument,document_id)
    if not document or document.uploaded_by!=user.id or document.status not in ("DRAFT","RETURNED"): raise HTTPException(404,"Draft or returned document not found.")
    if document.owner_job_card_id:
        card=mutable_card(assigned_card(db,user,document.owner_job_card_id));milestone=db.get(Milestone,document.milestone_id) if document.milestone_id else None;_require_work_payment(db,card,milestone)
    document.status="SUBMITTED";document.reviewed_by=None;document.reviewed_at=None;audit(db,user.id,"RECORD_DOCUMENT_SUBMITTED","DOCUMENT",document.id,f"{document.document_type} v{document.version}")
    if document.owner_job_card_id:
        owner=document_owner(db,document.owner_job_card_id) or card_created_by(db,document.owner_job_card_id)
        if owner and owner!=user.id: notify(db,owner,f"{document.document_type} was submitted for review.",f"/job-cards/{document.owner_job_card_id}")
    db.commit();db.refresh(document);return document

@app.post("/api/record-documents/{document_id}/review",response_model=RecordDocumentOut)
def review_record_document(document_id:int,data:RecordDocumentReview,db:DB,user:Annotated[User,Depends(require(Role.ADMIN,Role.MANAGEMENT))]):
    document=db.get(RecordDocument,document_id)
    if not document: raise HTTPException(404,"Document not found.")
    if document.owner_job_card_id: assigned_card(db,user,document.owner_job_card_id)
    if document.status not in ("SUBMITTED","UNDER_REVIEW"): raise HTTPException(409,"This document is not awaiting review.")
    document.status=data.decision;document.remarks=data.comment;document.reviewed_by=user.id;document.reviewed_at=datetime.now(timezone.utc);audit(db,user.id,f"RECORD_DOCUMENT_{data.decision}","DOCUMENT",document.id,data.comment)
    if document.owner_job_card_id:
        owner=document_owner(db,document.owner_job_card_id) or card_created_by(db,document.owner_job_card_id)
        if owner: notify(db,owner,f"{document.document_type} was {data.decision.lower()}: {data.comment}",f"/job-cards/{document.owner_job_card_id}")
    db.commit();db.refresh(document);return document

@app.get("/api/record-documents/{document_id}/download")
def download_record_document(document_id:int,db:DB,user:Annotated[User,Depends(current_user)]):
    document=db.get(RecordDocument,document_id)
    if not document: raise HTTPException(404,"Document not found.")
    target_kind=next((kind for kind,column in RECORD_DOCUMENT_TARGETS.items() if getattr(document,column) is not None),None)
    if not target_kind: raise HTTPException(404,"Document target not found.")
    resolve_record_document_target(db,user,target_kind,str(getattr(document,RECORD_DOCUMENT_TARGETS[target_kind])))
    from pathlib import Path
    path=STORAGE_ROOT/document.storage_key
    if not path.is_file(): raise HTTPException(404,"Document file not found.")
    return Response(content=path.read_bytes(),media_type=document.mime_type,headers={"Content-Disposition":f'attachment; filename="{document.file_name}"'})

@app.get("/api/job-cards/{card_id}/closure",response_model=ClosureOut)
def get_project_closure(card_id:int,db:DB,user:Annotated[User,Depends(current_user)]):
    assigned_card(db,user,card_id)
    item=db.scalar(select(ProjectClosure).where(ProjectClosure.job_card_id==card_id))
    if not item: raise HTTPException(404,"Closure record has not been prepared.")
    return item

@app.put("/api/job-cards/{card_id}/closure",response_model=ClosureOut)
def prepare_project_closure(card_id:int,data:ClosureUpsert,db:DB,user:Annotated[User,Depends(require(Role.ADMIN,Role.MARKETING))]):
    card=assigned_card(db,user,card_id)
    if card.status in ("CLOSURE_PENDING","COMPLETED","CANCELLED"): raise HTTPException(409,"Closure records must be prepared before final director approval; this Job Card is already locked or cancelled.")
    item=db.scalar(select(ProjectClosure).where(ProjectClosure.job_card_id==card_id))
    values=data.model_dump()
    if item and item.closed_at: raise HTTPException(409,"Closed project records cannot be edited.")
    if not item:
        item=ProjectClosure(job_card_id=card_id,prepared_by=user.id,**values);db.add(item)
    else:
        for field,value in values.items(): setattr(item,field,value)
        item.prepared_by=user.id
    if not item.confirmed_at: item.confirmed_at=datetime.now(timezone.utc)
    db.flush();audit(db,user.id,"PROJECT_CLOSURE_PREPARED","PROJECT_CLOSURE",item.id,card.number)
    db.commit();db.refresh(item);return item

@app.post("/api/job-cards/{card_id}/closure/complete",response_model=ClosureOut)
def complete_project_closure(card_id:int,db:DB,user:Annotated[User,Depends(require(Role.ADMIN,Role.MARKETING))]):
    card=assigned_card(db,user,card_id)
    if card.status!="CLOSURE_PENDING": raise HTTPException(409,"Director approval is required before project closure.")
    item=db.scalar(select(ProjectClosure).where(ProjectClosure.job_card_id==card_id))
    if not item or not all((item.client_confirmation,item.problem,item.requirement,item.solution,item.implementation,item.benefits,item.results,item.feedback,item.feedback_rating)):
        raise HTTPException(422,"Client confirmation, complete case study, and client feedback are required before closure.")
    if item.closed_at: raise HTTPException(409,"This project is already closed.")
    client=db.get(MSME,card.msme_id)
    cert_number=next_business_number(db,"certificate")
    certificate=to_pdf("D'Mastru Project Completion Certificate",[{
        "Certificate":cert_number,"Client":client.name if client else "","Project":card.project_name or card.number,
        "Job Card":card.number,"Package":card.package_code or "Not specified",
        "Project Period":f"{card.project_start_date} to {card.project_end_date}","Completion Date":date.today(),
        "Authorized Signatory":"D'Mastru Authorized Signatory"
    }])
    try: certificate_key=save_file(f"{cert_number}.pdf","application/pdf",certificate)
    except ValueError as exc: raise HTTPException(422,str(exc))
    item.certificate_number=cert_number;item.certificate_key=certificate_key;item.closed_at=datetime.now(timezone.utc)
    card.status="COMPLETED"
    db.flush();audit(db,user.id,"PROJECT_CLOSED","PROJECT_CLOSURE",item.id,f"{card.number}; certificate {cert_number}")
    notify(db,card.created_by,f"Job Card {card.number} is closed. Certificate {cert_number} is available.",f"/job-cards/{card_id}")
    db.commit();db.refresh(item);return item

@app.get("/api/job-cards/{card_id}/closure/certificate")
def download_completion_certificate(card_id:int,db:DB,user:Annotated[User,Depends(current_user)]):
    assigned_card(db,user,card_id)
    item=db.scalar(select(ProjectClosure).where(ProjectClosure.job_card_id==card_id))
    if not item or not item.certificate_key: raise HTTPException(404,"Completion certificate is not available.")
    from pathlib import Path
    path=STORAGE_ROOT/item.certificate_key
    if not path.is_file(): raise HTTPException(404,"Certificate file is missing from configured storage.")
    from fastapi.responses import FileResponse
    return FileResponse(path,media_type="application/pdf",filename=f"{item.certificate_number}.pdf")

def document_owner(db:Session,card_id:int):
    return db.scalar(select(Assignment.user_id).where(Assignment.job_card_id==card_id,Assignment.assignment_type=="LEAD_CONSULTANT",Assignment.active==True))
def card_created_by(db:Session,card_id:int): return db.get(JobCard,card_id).created_by
@app.get("/api/notifications",response_model=list[NotificationOut])
def list_notifications(db:DB,user:Annotated[User,Depends(current_user)]):
    now=datetime.now(timezone.utc);today=now.date();tomorrow=today+timedelta(days=1)
    actions=db.scalars(select(WorkflowAction).where(WorkflowAction.assigned_user_id==user.id,WorkflowAction.status.in_(("OPEN","IN_PROGRESS","WAITING","ON_HOLD")),WorkflowAction.due_at.is_not(None))).all()
    for action in actions:
        due=action.due_at if action.due_at.tzinfo else action.due_at.replace(tzinfo=timezone.utc)
        if due<now: state="OVERDUE"
        elif due<=now+timedelta(days=1): state="UPCOMING"
        else: continue
        notify_once(db,user.id,f"Task {action.action_number} is {state.lower()} (due {due.isoformat()}).","/actions",f"task:action:{action.id}:{state}:{due.date().isoformat()}")
    activities=db.scalars(select(JobActivity).where(JobActivity.assigned_user_id==user.id,JobActivity.status.in_(("PLANNED","IN_PROGRESS")),JobActivity.end_date.is_not(None))).all()
    for activity in activities:
        if activity.end_date<today: state="OVERDUE"
        elif activity.end_date<=tomorrow: state="UPCOMING"
        else: continue
        notify_once(db,user.id,f"Activity {activity.name} is {state.lower()} (due {activity.end_date.isoformat()}).",f"/user/activities?job_card={activity.job_card_id}",f"task:activity:{activity.id}:{state}:{activity.end_date.isoformat()}")
    db.commit()
    query=select(Notification).where(Notification.user_id==user.id)
    if user.role==Role.ADMIN: query=query.where(~or_(Notification.message.ilike("%payment%"),Notification.message.ilike("%invoice%")))
    return list(db.scalars(query.order_by(Notification.created_at.desc()).limit(100)))
@app.post("/api/notifications/{notification_id}/read",response_model=NotificationOut)
def mark_notification_read(notification_id:int,db:DB,user:Annotated[User,Depends(current_user)]):
    item=db.get(Notification,notification_id)
    if not item or item.user_id!=user.id or (user.role==Role.ADMIN and ("payment" in item.message.lower() or "invoice" in item.message.lower())): raise HTTPException(404,"Notification not found.")
    item.read_at=datetime.now(timezone.utc); db.commit(); db.refresh(item); return item
@app.get("/api/activity",response_model=list[AuditOut])
def my_activity(db:DB,user:Annotated[User,Depends(current_user)]):
    query=select(AuditLog).where(AuditLog.actor_id==user.id)
    if user.role==Role.ADMIN: query=query.where(AuditLog.entity_type!="PAYMENT",AuditLog.action!="JOB_CARD_ACTIVATED")
    return list(db.scalars(query.order_by(AuditLog.created_at.desc())))
@app.get("/api/audit-logs",response_model=list[AuditOut])
def audit_logs(db:DB,user:Annotated[User,Depends(require(Role.ADMIN,Role.MANAGEMENT))],action:str|None=None,entity_type:str|None=None,search:str|None=None,page:int=Query(default=1,ge=1),page_size:int=Query(default=100,ge=1,le=200)):
    query=select(AuditLog)
    if user.role==Role.ADMIN: query=query.where(AuditLog.entity_type!="PAYMENT",AuditLog.action!="JOB_CARD_ACTIVATED",~AuditLog.action.like("PAYMENT_%"))
    if action: query=query.where(AuditLog.action==action)
    if entity_type: query=query.where(AuditLog.entity_type==entity_type)
    if search:
        term=f"%{search}%";query=query.where(AuditLog.details.ilike(term)|AuditLog.action.ilike(term)|AuditLog.entity_type.ilike(term)|AuditLog.entity_ref.ilike(term))
    return list(db.scalars(query.order_by(AuditLog.created_at.desc()).offset((page-1)*page_size).limit(page_size)))

REPORTS={"projects":project_rows,"consultants":consultant_rows,"milestones":milestone_rows,"visits":visit_rows,"documents":document_rows,"approvals":approval_rows,"payments":payment_rows,"tasks":task_rows}
def report_data(kind:str,db:Session,status_filter:str|None=None,search:str|None=None,delayed:bool|None=None,overrun:bool|None=None,start_date:str|None=None,end_date:str|None=None,consultant_id:int|None=None,msme_id:int|None=None,job_card_id:int|None=None,milestone_id:int|None=None,project_status:str|None=None):
    builder=REPORTS.get(kind)
    if not builder: raise HTTPException(404,"Report type not found.")
    rows=builder(db)
    result=[]
    for row in rows:
        if status_filter and row.get("Status") is not None and row.get("Status")!=status_filter: continue
        if consultant_id is not None and str(row.get("Consultant ID",""))!=str(consultant_id): continue
        if msme_id is not None and str(row.get("MSME ID",""))!=str(msme_id): continue
        if job_card_id is not None and str(row.get("Job Card ID",""))!=str(job_card_id): continue
        if milestone_id is not None and str(row.get("Milestone ID",""))!=str(milestone_id): continue
        if project_status and row.get("Project Status",row.get("Status"))!=project_status: continue
        if search and search.lower() not in " ".join(map(str,row.values())).lower(): continue
        if delayed is not None and kind=="projects" and ((int(row["Delay Days"])>0)!=delayed): continue
        if overrun is not None and kind=="projects" and row["Overrun"]!=overrun: continue
        report_date=str(row.get("Date",row.get("Start Date",row.get("Invoice Date",""))))[:10]
        if start_date and report_date and report_date<start_date: continue
        if end_date and report_date and report_date>end_date: continue
        result.append(row)
    return result
@app.get("/api/reports/{kind}")
def get_report(kind:str,db:DB,user:Annotated[User,Depends(require(Role.ADMIN,Role.MANAGEMENT,Role.MARKETING))],status_filter:str|None=Query(default=None,alias="status"),search:str|None=None,delayed:bool|None=None,overrun:bool|None=None,start_date:str|None=None,end_date:str|None=None,consultant_id:int|None=None,msme_id:int|None=None,job_card_id:int|None=None,milestone_id:int|None=None,project_status:str|None=None,page:int=Query(default=1,ge=1),page_size:int=Query(default=25,ge=1,le=100)):
    if kind=="payments" and user.role==Role.ADMIN: raise HTTPException(403,"Payment records are restricted to Marketing / Finance and Management.")
    if user.role==Role.MARKETING and kind!="payments": raise HTTPException(403,"Marketing / Finance can access payment reports only.")
    if user.role==Role.ADMIN and (status_filter in {"AWAITING_PAYMENT","LOCKED","INVOICE_RAISED","PARTIALLY_PAID","PAID","OVERDUE","PAYMENT_PENDING"} or project_status=="AWAITING_PAYMENT"): raise HTTPException(403,"Payment status is not available to System Admin.")
    rows=report_data(kind,db,status_filter,search,delayed,overrun,start_date,end_date,consultant_id,msme_id,job_card_id,milestone_id,project_status)
    if user.role==Role.ADMIN:
        for row in rows:
            for field in ("Status","Project Status"):
                if row.get(field)=="AWAITING_PAYMENT": row[field]="PENDING_ACTIVATION"
            if row.get("Status")=="LOCKED": row["Status"]="PENDING_ACTIVATION"
            row.pop("Payment Date",None)
    return {"items":rows[(page-1)*page_size:page*page_size],"total":len(rows),"page":page,"page_size":page_size}
@app.get("/api/reports/{kind}/export/{format}")
def export_report(kind:str,format:str,db:DB,user:Annotated[User,Depends(require(Role.ADMIN,Role.MANAGEMENT,Role.MARKETING))],status_filter:str|None=Query(default=None,alias="status"),search:str|None=None,delayed:bool|None=None,overrun:bool|None=None,start_date:str|None=None,end_date:str|None=None,consultant_id:int|None=None,msme_id:int|None=None,job_card_id:int|None=None,milestone_id:int|None=None,project_status:str|None=None):
    if kind=="payments" and user.role==Role.ADMIN: raise HTTPException(403,"Payment records are restricted to Marketing / Finance and Management.")
    if user.role==Role.MARKETING and kind!="payments": raise HTTPException(403,"Marketing / Finance can export payment reports only.")
    if user.role==Role.ADMIN and (status_filter in {"AWAITING_PAYMENT","LOCKED","INVOICE_RAISED","PARTIALLY_PAID","PAID","OVERDUE","PAYMENT_PENDING"} or project_status=="AWAITING_PAYMENT"): raise HTTPException(403,"Payment status is not available to System Admin.")
    rows=report_data(kind,db,status_filter,search,delayed,overrun,start_date,end_date,consultant_id,msme_id,job_card_id,milestone_id,project_status)
    if user.role==Role.ADMIN:
        for row in rows:
            for field in ("Status","Project Status"):
                if row.get(field)=="AWAITING_PAYMENT": row[field]="PENDING_ACTIVATION"
            if row.get("Status")=="LOCKED": row["Status"]="PENDING_ACTIVATION"
            row.pop("Payment Date",None)
    if format=="csv": content=to_csv(rows); media="text/csv"; ext="csv"
    elif format=="xlsx": content=to_xlsx(rows); media="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"; ext="xlsx"
    elif format=="pdf": content=to_pdf(f"D'Mastru {kind.title()} Report",rows); media="application/pdf"; ext="pdf"
    else: raise HTTPException(404,"Supported formats are csv, xlsx, and pdf.")
    audit(db,user.id,"REPORT_EXPORTED","REPORT",None,f"{kind}.{ext}"); db.commit()
    return Response(content=content,media_type=media,headers={"Content-Disposition":f'attachment; filename="{kind}-report.{ext}"'})

def dashboard_cards(db:Session, user:User):
    query=select(JobCard).where(JobCard.deleted_at==None)
    if user.role==Role.END_USER: query=query.join(Assignment,Assignment.job_card_id==JobCard.id).where(Assignment.user_id==user.id,Assignment.active==True)
    return [card_dashboard_row(db,c) for c in db.scalars(query)]
def dashboard_payload(db:Session,user:User):
    cards=dashboard_cards(db,user)
    completed=sum((c["completed_md"] for c in cards),Decimal(0)); required=sum((Decimal(str(db.get(JobCard,c["id"]).required_md)) for c in cards),Decimal(0))
    active=sum(c["status"] in ("ASSIGNED","IN_PROGRESS") for c in cards); delayed=[c for c in cards if c["status"]=="DELAYED"]
    pending_visits=int(db.scalar(select(func.count(Visit.id)).where(Visit.status=="SUBMITTED",Visit.deleted_at==None)) or 0)
    pending_documents=int(db.scalar(select(func.count(Document.id)).where(Document.status.in_(("SUBMITTED","RESUBMITTED","UNDER_REVIEW")),Document.deleted_at==None)) or 0)+int(db.scalar(select(func.count(RecordDocument.id)).where(RecordDocument.status.in_(("SUBMITTED","UNDER_REVIEW")))) or 0)
    pending_approvals=int(db.scalar(select(func.count(Approval.id)).where(Approval.decision.in_(("PENDING","SUBMITTED")))) or 0)+int(db.scalar(select(func.count(JobCard.id)).where(JobCard.status=="PENDING_APPROVAL",JobCard.deleted_at==None)) or 0)
    milestone_due=sum(1 for m in db.scalars(select(Milestone).where(Milestone.closing_date<=date.today())) if not (milestone_summary(db,m)["actual_visits"]>=m.planned_visits and milestone_summary(db,m)["review_status"]=="APPROVED"))
    visible_ids=[row["id"] for row in cards]
    recent_job_cards=[]
    milestone_rows=[]
    if visible_ids:
        recent=list(db.scalars(select(JobCard).where(JobCard.id.in_(visible_ids)).order_by(JobCard.created_at.desc()).limit(5)))
        row_by_id={row["id"]:row for row in cards}
        recent_job_cards=[{"id":card.id,"number":card.number,"status":row_by_id[card.id]["status"],"location":card.location,"project_start_date":card.project_start_date,"project_end_date":card.project_end_date,"progress":row_by_id[card.id]["progress"]} for card in recent]
        query=(select(Milestone,JobCard.number,func.count(Visit.id))
            .join(JobCard,JobCard.id==Milestone.job_card_id)
            .outerjoin(Visit,(Visit.milestone_id==Milestone.id)&(Visit.status=="SUBMITTED")&(Visit.deleted_at==None))
            .where(Milestone.job_card_id.in_(visible_ids),JobCard.deleted_at==None)
            .group_by(Milestone.id,JobCard.number)
            .order_by(Milestone.closing_date.asc(),Milestone.id.desc()).limit(8))
        for milestone,card_number,completed_visits in db.execute(query):
            progress=min((Decimal(completed_visits)*100/Decimal(milestone.planned_visits)).quantize(Decimal(".01")),Decimal(100)) if milestone.planned_visits else Decimal(0)
            milestone_rows.append({"id":milestone.id,"job_card_number":card_number,"code":milestone.code,"name":milestone.name,"planned_visits":milestone.planned_visits,"completed_visits":completed_visits,"progress":progress,"status":"COMPLETED" if completed_visits>=milestone.planned_visits else ("IN_PROGRESS" if completed_visits else "NOT_STARTED"),"start_date":milestone.start_date,"closing_date":milestone.closing_date,"payment_date":milestone.payment_date,"portal_report_submission_date":milestone.portal_report_submission_date})
    payment_kpis={}
    if user.role==Role.MANAGEMENT:
        open_payment_states=("PENDING","INVOICE_RAISED","PARTIALLY_PAID","OVERDUE")
        pending_amount=db.scalar(select(func.coalesce(func.sum(Payment.amount_due-Payment.amount_paid),0)).where(Payment.status.in_(open_payment_states))) or 0
        initial_pending=int(db.scalar(select(func.count(JobCard.id)).where(JobCard.deleted_at.is_(None),~select(Payment.id).where(Payment.job_card_id==JobCard.id,Payment.milestone_id.is_(None),Payment.status=="PAID").exists())) or 0)
        milestone_payment_pending=int(db.scalar(select(func.count(Milestone.id)).join(JobCard,JobCard.id==Milestone.job_card_id).where(JobCard.deleted_at.is_(None),~select(Payment.id).where(Payment.milestone_id==Milestone.id,Payment.status=="PAID").exists())) or 0)
        payment_kpis={"initial_payments_pending":initial_pending,"milestone_payments_pending":milestone_payment_pending,"pending_invoices":int(db.scalar(select(func.count(Payment.id)).where(Payment.status.in_(open_payment_states))) or 0),"pending_payment_amount":pending_amount,"paid_amount":db.scalar(select(func.coalesce(func.sum(Payment.amount_paid),0))) or 0}
    open_actions=int(db.scalar(select(func.count(WorkflowAction.id)).where(WorkflowAction.status.in_(("OPEN","IN_PROGRESS","WAITING","ON_HOLD")))) or 0)
    overdue_tasks=int(db.scalar(select(func.count(WorkflowAction.id)).where(WorkflowAction.status.in_(("OPEN","IN_PROGRESS","WAITING","ON_HOLD")),WorkflowAction.due_at<datetime.now(timezone.utc))) or 0)+int(db.scalar(select(func.count(JobActivity.id)).where(JobActivity.status.in_(("PLANNED","IN_PROGRESS")),JobActivity.end_date<date.today())) or 0)
    pending_tasks=open_actions+int(db.scalar(select(func.count(JobActivity.id)).where(JobActivity.status.in_(("PLANNED","IN_PROGRESS")))) or 0)
    planned_upcoming=int(db.scalar(select(func.count(PlannedVisit.id)).where(PlannedVisit.status=="PLANNED",PlannedVisit.visit_date>=date.today())) or 0)
    completed_site_visits=int(db.scalar(select(func.count(Visit.id)).where(Visit.status.in_(("APPROVED","COMPLETED")),Visit.deleted_at.is_(None))) or 0)+int(db.scalar(select(func.count(PlannedVisit.id)).where(PlannedVisit.status=="COMPLETED")) or 0)
    milestone_progress={}
    for code in ("M1","M2","M3"):
        ms=list(db.scalars(select(Milestone).join(JobCard,JobCard.id==Milestone.job_card_id).where(Milestone.code==code,JobCard.deleted_at.is_(None))))
        summaries=[milestone_summary(db,m) for m in ms]
        progresses=[summary["progress"] for summary in summaries]
        milestone_progress[code]={"total":len(ms),"progress":(sum(progresses,Decimal(0))/Decimal(len(progresses))).quantize(Decimal(".01")) if progresses else Decimal(0),"completed":sum(1 for m,summary in zip(ms,summaries) if summary["actual_visits"]>=m.planned_visits and summary["review_status"]=="APPROVED")}
    consultant_workload=consultant_rows(db)
    msme_progress=[]
    for msme_id in sorted({row["msme_id"] for row in cards}):
        owned=[row for row in cards if row["msme_id"]==msme_id];company=db.get(MSME,msme_id)
        msme_progress.append({"msme_id":msme_id,"msme":company.name if company else "","job_cards":len(owned),"progress":(sum((Decimal(str(row["progress"])) for row in owned),Decimal(0))/Decimal(len(owned))).quantize(Decimal(".01")) if owned else Decimal(0),"overruns":sum(1 for row in owned if row["overrun"])})
    kpis={"total_enquiries":int(db.scalar(select(func.count(Enquiry.id))) or 0),"open_enquiries":int(db.scalar(select(func.count(Enquiry.id)).where(Enquiry.status.not_in(("CONVERTED","LOST","CANCELLED")))) or 0),"converted_enquiries":int(db.scalar(select(func.count(Enquiry.id)).where(Enquiry.status=="CONVERTED")) or 0),"conversion_percent":(Decimal(int(db.scalar(select(func.count(Enquiry.id)).where(Enquiry.status=="CONVERTED")) or 0)*100/ max(int(db.scalar(select(func.count(Enquiry.id))) or 0),1))).quantize(Decimal(".01")),"total_projects":len(cards),"active_projects":active,"completed_projects":sum(c["status"]=="COMPLETED" for c in cards),"on_hold_projects":sum(c["status"]=="ON_HOLD" for c in cards),"delayed_projects":len(delayed),"overrun_projects":sum(c["overrun"] for c in cards),"pending_visits":pending_visits,"planned_upcoming_visits":planned_upcoming,"completed_site_visits":completed_site_visits,"pending_documents":pending_documents,"pending_approvals":pending_approvals,"pending_tasks":pending_tasks,"overdue_tasks":overdue_tasks,"milestones_due":milestone_due,**payment_kpis,"open_actions":open_actions,"closure_pending":int(db.scalar(select(func.count(JobCard.id)).where(JobCard.status=="CLOSURE_PENDING")) or 0),"required_md":required,"completed_md":completed.quantize(MD_PRECISION),"remaining_md":(required-completed).quantize(MD_PRECISION)}
    if user.role==Role.ADMIN:
        for card in cards:
            if card["status"]=="AWAITING_PAYMENT": card["status"]="PENDING_ACTIVATION"
        for card in recent_job_cards:
            if card["status"]=="AWAITING_PAYMENT": card["status"]="PENDING_ACTIVATION"
        for milestone in milestone_rows: milestone.pop("payment_date",None)
    return {"role":user.role,"kpis":kpis,"manday_chart":{"completed_md":completed.quantize(MD_PRECISION),"remaining_md":max(required-completed,Decimal(0)).quantize(MD_PRECISION)},"status_chart":{"active":active,"completed":sum(c["status"]=="COMPLETED" for c in cards),"delayed":len(delayed),"on_hold":sum(c["status"]=="ON_HOLD" for c in cards)},"attention":[c for c in cards if c["overrun"] or c["status"]=="DELAYED"],"projects":cards,"recent_job_cards":recent_job_cards,"milestone_summary":milestone_rows,"milestone_progress":milestone_progress,"consultant_workload":consultant_workload,"msme_progress":msme_progress}

@app.get("/api/dashboard/management")
def management_dashboard(db:DB,user:Annotated[User,Depends(require(Role.MANAGEMENT))],status_filter:str|None=Query(default=None,alias="status"),search:str|None=None,card_id:int|None=None,overrun:bool|None=None):
    payload=dashboard_payload(db,user)
    if status_filter or search or card_id or overrun is not None:
        if card_id: payload["projects"]=[row for row in payload["projects"] if row["id"]==card_id]
        if overrun is not None: payload["projects"]=[row for row in payload["projects"] if row["overrun"]==overrun]
        query=select(JobCard).join(MSME,MSME.id==JobCard.msme_id).where(JobCard.deleted_at==None)
        if status_filter: payload["projects"]=[row for row in payload["projects"] if row["status"]==status_filter]
        if search:
            term=f"%{search}%"; matches={row[0] for row in db.execute(query.where((JobCard.number.ilike(term))|(JobCard.location.ilike(term))|(MSME.name.ilike(term))|(MSME.zed_id.ilike(term))).with_only_columns(JobCard.id)).all()}
            payload["projects"]=[row for row in payload["projects"] if row["id"] in matches]
    return payload
@app.get("/api/dashboard/admin")
def admin_dashboard(db:DB,user:Annotated[User,Depends(require(Role.ADMIN))]):
    payload=dashboard_payload(db,user)
    lead_assigned=select(Assignment.id).where(Assignment.job_card_id==JobCard.id,Assignment.assignment_type=="LEAD_CONSULTANT",Assignment.active.is_(True)).exists()
    payload["kpis"].update({"total_users":int(db.scalar(select(func.count(User.id))) or 0),"total_consultants":int(db.scalar(select(func.count(User.id)).where(User.role==Role.END_USER)) or 0),"visits_recorded":int(db.scalar(select(func.count(Visit.id)).where(Visit.deleted_at==None)) or 0),"total_msmes":int(db.scalar(select(func.count(MSME.id)).where(MSME.deleted_at.is_(None))) or 0),"total_job_cards":int(db.scalar(select(func.count(JobCard.id)).where(JobCard.deleted_at.is_(None))) or 0),"job_cards_awaiting_consultant_assignment":int(db.scalar(select(func.count(JobCard.id)).where(JobCard.deleted_at.is_(None),~lead_assigned)) or 0),"work_in_progress":int(db.scalar(select(func.count(JobCard.id)).where(JobCard.deleted_at.is_(None),JobCard.status=="IN_PROGRESS")) or 0),"returned_documents":int(db.scalar(select(func.count(Document.id)).where(Document.status=="RETURNED",Document.deleted_at.is_(None))) or 0)+int(db.scalar(select(func.count(RecordDocument.id)).where(RecordDocument.status=="RETURNED")) or 0),"milestones_awaiting_action":int(db.scalar(select(func.count(Milestone.id)).where(Milestone.status.in_(("READY_FOR_REVIEW","AWAITING_UNIT_CONFIRMATION")))) or 0),"upcoming_visits":int(db.scalar(select(func.count(Visit.id)).where(Visit.visit_date>=date.today(),Visit.status=="DRAFT",Visit.deleted_at.is_(None))) or 0)+int(db.scalar(select(func.count(PlannedVisit.id)).where(PlannedVisit.visit_date>=date.today(),PlannedVisit.status=="PLANNED")) or 0),"overdue_activities":int(db.scalar(select(func.count(JobActivity.id)).where(JobActivity.end_date<date.today(),JobActivity.status.in_(("PLANNED","IN_PROGRESS")))) or 0)})
    payload["kpis"]["pending_activities"]=payload["kpis"]["pending_visits"]+payload["kpis"]["pending_documents"]
    workload=db.execute(select(User.id,User.full_name,func.count(func.distinct(JobCard.id)),func.coalesce(func.sum(JobCard.required_md),0)).join(Assignment,Assignment.user_id==User.id).join(JobCard,JobCard.id==Assignment.job_card_id).where(User.role==Role.END_USER,User.status==AccountStatus.ACTIVE,Assignment.active.is_(True),JobCard.deleted_at.is_(None)).group_by(User.id,User.full_name).order_by(User.full_name)).all()
    payload["consultant_workload"]=[{"user_id":uid,"full_name":name,"active_job_cards":int(cards),"planned_mandays":str(md)} for uid,name,cards,md in workload]
    availability_rows=db.execute(select(User.id,User.full_name).where(User.role==Role.END_USER,User.status==AccountStatus.ACTIVE).order_by(User.full_name)).all()
    current_allocation=select(Assignment.id).where(Assignment.user_id==User.id,Assignment.active.is_(True),or_(Assignment.allocation_start_date.is_(None),Assignment.allocation_start_date<=date.today()),or_(Assignment.allocation_end_date.is_(None),Assignment.allocation_end_date>=date.today())).exists()
    payload["consultant_availability"]=[{"user_id":uid,"full_name":name,"available":not bool(db.scalar(select(current_allocation).where(User.id==uid)))} for uid,name in availability_rows]
    upcoming=db.execute(select(PlannedVisit.id,PlannedVisit.visit_date,PlannedVisit.start_time,JobCard.id,JobCard.number,User.full_name).join(JobCard,JobCard.id==PlannedVisit.job_card_id).outerjoin(User,User.id==PlannedVisit.consultant_id).where(PlannedVisit.visit_date>=date.today(),PlannedVisit.status=="PLANNED",JobCard.deleted_at.is_(None)).order_by(PlannedVisit.visit_date,PlannedVisit.start_time).limit(12)).all()
    payload["upcoming_visits"]=[{"visit_id":vid,"visit_number":vid,"visit_date":visit_date,"start_time":str(start_time),"job_card_id":card_id,"job_card_number":card_number,"consultant":consultant} for vid,visit_date,start_time,card_id,card_number,consultant in upcoming]
    return payload
@app.get("/api/dashboard/marketing")
def marketing_dashboard(db:DB,user:Annotated[User,Depends(require(Role.MARKETING))]):
    open_statuses=("NEW","CONTACTED","FOLLOW_UP","QUALIFIED","FINALISED")
    pending_payments=("PENDING","INVOICE_RAISED","PARTIALLY_PAID","OVERDUE")
    enquiry_scope=[]
    if user.role==Role.MARKETING:
        enquiry_scope.append(or_(Enquiry.created_by==user.id,Enquiry.assigned_marketing_user_id==user.id))
    status_counts={str(status):int(count) for status,count in db.execute(select(Enquiry.status,func.count(Enquiry.id)).where(*enquiry_scope).group_by(Enquiry.status))}
    today_start=datetime.combine(date.today(),datetime.min.time(),tzinfo=timezone.utc)
    followup_scope=[EnquiryFollowUp.next_follow_up_date.is_not(None),EnquiryFollowUp.status!="CANCELLED",Enquiry.status.in_(open_statuses),*enquiry_scope]
    followup_stmt=select(func.count(EnquiryFollowUp.id)).join(Enquiry,Enquiry.id==EnquiryFollowUp.enquiry_id).where(*followup_scope)
    followups_overdue=int(db.scalar(followup_stmt.where(EnquiryFollowUp.next_follow_up_date<today_start)) or 0)
    followups_upcoming=int(db.scalar(followup_stmt.where(EnquiryFollowUp.next_follow_up_date>=today_start)) or 0)
    pending_balance=db.scalar(select(func.coalesce(func.sum(Payment.amount_due-Payment.amount_paid),0)).where(Payment.status.in_(pending_payments))) or 0
    initial_payment=~select(Payment.id).where(Payment.job_card_id==JobCard.id,Payment.milestone_id.is_(None),Payment.status=="PAID").exists()
    awaiting_activation=int(db.scalar(select(func.count(JobCard.id)).where(JobCard.deleted_at.is_(None),JobCard.status.in_(("AWAITING_PAYMENT","ASSIGNED","IN_PROGRESS")),initial_payment)) or 0)
    milestone_payments={str(code):int(count) for code,count in db.execute(select(Milestone.code,func.count(Milestone.id)).join(JobCard,JobCard.id==Milestone.job_card_id).where(JobCard.deleted_at.is_(None),Milestone.code.in_(("M1","M2","M3")),~select(Payment.id).where(Payment.milestone_id==Milestone.id,Payment.status=="PAID").exists()).group_by(Milestone.code))}
    confirmed_payments=int(db.scalar(select(func.count(Payment.id)).where(Payment.status=="PAID")) or 0)
    overdue_payments=int(db.scalar(select(func.count(Payment.id)).where(Payment.status.in_(pending_payments),Payment.due_date.is_not(None),Payment.due_date<date.today())) or 0)
    recent_receipts=db.execute(select(PaymentReceipt,Payment,User).join(Payment,Payment.id==PaymentReceipt.payment_id).join(User,User.id==PaymentReceipt.confirmed_by).order_by(PaymentReceipt.received_at.desc()).limit(8)).all()
    recent_payment_activity=[{"id":receipt.id,"invoice_number":payment.invoice_number,"amount":str(receipt.amount),"reference":receipt.external_reference,"received_at":receipt.received_at,"confirmed_by":confirmer.full_name,"milestone_id":payment.milestone_id} for receipt,payment,confirmer in recent_receipts]
    total_enquiries=sum(status_counts.values())
    new_enquiries=status_counts.get("NEW",0)
    converted_enquiries=status_counts.get("CONVERTED",0)
    return {"enquiries_by_status":status_counts,"total_enquiries":total_enquiries,"new_enquiries":new_enquiries,"converted_enquiries":converted_enquiries,"followups_due":followups_upcoming+followups_overdue,"followups_upcoming":followups_upcoming,"followups_overdue":followups_overdue,"open_tickets":int(db.scalar(select(func.count(Ticket.id)).where(Ticket.status.not_in(("COMPLETED","REJECTED","CANCELLED")))) or 0),"pending_invoices":int(db.scalar(select(func.count(Payment.id)).where(Payment.status.in_(pending_payments))) or 0),"pending_balance":pending_balance,"payments_confirmed":confirmed_payments,"payments_overdue":overdue_payments,"recent_payment_activity":recent_payment_activity,"projects_awaiting_activation":awaiting_activation,"closure_pending":int(db.scalar(select(func.count(JobCard.id)).where(JobCard.status=="CLOSURE_PENDING",JobCard.deleted_at.is_(None))) or 0),"milestone_payments_pending":milestone_payments}
@app.get("/api/dashboard/user")
def user_dashboard(db:DB,user:Annotated[User,Depends(require(Role.END_USER))]):
    today=date.today();assigned=select(JobCard).join(Assignment,Assignment.job_card_id==JobCard.id).where(Assignment.user_id==user.id,Assignment.active.is_(True),JobCard.deleted_at.is_(None)).distinct()
    assigned_count=int(db.scalar(select(func.count(func.distinct(JobCard.id))).select_from(JobCard).join(Assignment,Assignment.job_card_id==JobCard.id).where(Assignment.user_id==user.id,Assignment.active.is_(True),JobCard.deleted_at.is_(None))) or 0)
    plan_rows=db.execute(select(JobCard.id,JobCard.required_md).join(Assignment,Assignment.job_card_id==JobCard.id).where(Assignment.user_id==user.id,Assignment.active.is_(True),JobCard.deleted_at.is_(None)).distinct()).all()
    planned=sum((Decimal(str(amount)) for _,amount in plan_rows),Decimal(0))
    time_rows=db.execute(select(Visit.working_minutes,JobCard.hours_per_manday).join(JobCard,JobCard.id==Visit.job_card_id).where(Visit.created_by==user.id,Visit.status=="SUBMITTED",Visit.deleted_at.is_(None),JobCard.deleted_at.is_(None))).all()
    activity_time_rows=db.execute(select(JobActivity.duration_minutes,JobCard.hours_per_manday).join(JobCard,JobCard.id==JobActivity.job_card_id).where(JobActivity.assigned_user_id==user.id,JobActivity.status=="COMPLETED",JobActivity.visit_id.is_(None),JobCard.deleted_at.is_(None))).all()
    planned_visit_time_rows=db.execute(select(PlannedVisit.actual_duration_minutes,JobCard.hours_per_manday).join(JobCard,JobCard.id==PlannedVisit.job_card_id).where(PlannedVisit.consultant_id==user.id,PlannedVisit.status=="COMPLETED",JobCard.deleted_at.is_(None),~select(Visit.id).where(Visit.job_card_id==PlannedVisit.job_card_id,Visit.visit_date==PlannedVisit.actual_date,Visit.consultant_id==PlannedVisit.consultant_id,Visit.status.in_(("SUBMITTED","REVIEWED","APPROVED")),Visit.deleted_at.is_(None)).exists())).all()
    actual=sum((Decimal(minutes)/Decimal(60)/Decimal(str(hours or 8)) for minutes,hours in (*time_rows,*activity_time_rows,*planned_visit_time_rows) if minutes is not None),Decimal(0)).quantize(MD_PRECISION)
    cards=list(db.scalars(assigned.order_by(JobCard.created_at.desc()).limit(5)))
    projects=[card_dashboard_row(db,card) for card in cards]
    open_actions=list(db.scalars(select(WorkflowAction).where(WorkflowAction.assigned_user_id==user.id,WorkflowAction.status.in_(("OPEN","IN_PROGRESS","WAITING","ON_HOLD"))).order_by(WorkflowAction.due_at,WorkflowAction.created_at.desc()).limit(8)))
    overdue_actions=int(db.scalar(select(func.count(WorkflowAction.id)).where(WorkflowAction.assigned_user_id==user.id,WorkflowAction.status.in_(("OPEN","IN_PROGRESS","WAITING","ON_HOLD")),WorkflowAction.due_at<datetime.now(timezone.utc))) or 0)
    today_visits=int(db.scalar(select(func.count(Visit.id)).where(Visit.created_by==user.id,Visit.visit_date==today,Visit.deleted_at.is_(None))) or 0)
    upcoming=int(db.scalar(select(func.count(Visit.id)).where(Visit.created_by==user.id,Visit.visit_date>today,Visit.status.in_(("DRAFT","SUBMITTED")),Visit.deleted_at.is_(None))) or 0)
    pending=int(db.scalar(select(func.count(Visit.id)).where(Visit.created_by==user.id,Visit.status=="DRAFT",Visit.deleted_at.is_(None))) or 0)
    returned=int(db.scalar(select(func.count(Visit.id)).where(Visit.created_by==user.id,Visit.status.in_(("RETURNED","REJECTED")),Visit.deleted_at.is_(None))) or 0)+int(db.scalar(select(func.count(RecordDocument.id)).where(RecordDocument.uploaded_by==user.id,RecordDocument.status=="RETURNED")) or 0)+int(db.scalar(select(func.count(WorkflowAction.id)).where(WorkflowAction.assigned_user_id==user.id,WorkflowAction.status=="RETURNED")) or 0)
    pending_documents=int(db.scalar(select(func.count(RecordDocument.id)).where(RecordDocument.uploaded_by==user.id,RecordDocument.status.in_(("DRAFT","SUBMITTED","UNDER_REVIEW")))) or 0)+int(db.scalar(select(func.count(Document.id)).where(Document.uploaded_by==user.id,Document.status.in_(("SUBMITTED","RESUBMITTED")),Document.deleted_at.is_(None))) or 0)
    planned_upcoming=int(db.scalar(select(func.count(PlannedVisit.id)).where(PlannedVisit.consultant_id==user.id,PlannedVisit.visit_date>=today,PlannedVisit.status=="PLANNED")) or 0)
    overdue_activities=int(db.scalar(select(func.count(JobActivity.id)).where(JobActivity.assigned_user_id==user.id,JobActivity.status.in_(("PLANNED","IN_PROGRESS")),JobActivity.end_date<today)) or 0)
    due_today_tasks=int(db.scalar(select(func.count(WorkflowAction.id)).where(WorkflowAction.assigned_user_id==user.id,WorkflowAction.status.in_(("OPEN","IN_PROGRESS","WAITING","ON_HOLD")),func.date(WorkflowAction.due_at)==today)) or 0)
    active_milestones=int(db.scalar(select(func.count(func.distinct(Milestone.id))).join(JobCard,JobCard.id==Milestone.job_card_id).join(Assignment,Assignment.job_card_id==JobCard.id).where(Assignment.user_id==user.id,Assignment.active.is_(True),JobCard.deleted_at.is_(None),Milestone.status.not_in(("COMPLETED","CANCELLED")))) or 0)
    completed_activities=int(db.scalar(select(func.count(JobActivity.id)).where(JobActivity.assigned_user_id==user.id,JobActivity.status=="COMPLETED")) or 0)
    my_visits=int(db.scalar(select(func.count(Visit.id)).where(Visit.created_by==user.id,Visit.deleted_at.is_(None))) or 0)
    kpis={"total_projects":assigned_count,"assigned_job_cards":assigned_count,"active_milestones":active_milestones,"my_actions":len(open_actions),"my_visits":my_visits,"today_activities":today_visits,"today_visits":today_visits,"upcoming_visits":upcoming+planned_upcoming,"planned_visits":planned_upcoming,"pending_activities":pending,"pending_submissions":pending+pending_documents,"pending_documents":pending_documents,"returned_items":returned,"completed_activities":completed_activities,"due_today_tasks":due_today_tasks,"overdue_tasks":overdue_actions+overdue_activities,"overdue_activities":overdue_activities,"planned_mandays":planned.quantize(MD_PRECISION),"actual_mandays":actual,"completed_mandays":actual,"remaining_mandays":(planned-actual).quantize(MD_PRECISION)}
    recent=[{"id":card.id,"number":card.number,"status":projects[index]["status"],"location":card.location,"project_start_date":card.project_start_date,"project_end_date":card.project_end_date,"progress":projects[index]["progress"]} for index,card in enumerate(cards)]
    return {"role":user.role,"kpis":kpis,"projects":projects,"recent_job_cards":recent,"my_actions":[_workflow_out(db,row).model_dump(mode="json") for row in open_actions]}
