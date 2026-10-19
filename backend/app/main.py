import base64
import asyncio
import json
import hashlib
import hmac
import html
import os
import re
import secrets
import struct
from datetime import datetime, time as TimeOfDay, timedelta, timezone
from datetime import date
from uuid import UUID
from decimal import Decimal
from typing import Annotated, ClassVar
import logging
import time
import jwt
from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED
from xml.sax.saxutils import escape as xml_escape
from urllib.parse import quote
from pathlib import Path
from zoneinfo import ZoneInfo
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from .core import AccountStatus, ENV, JWT_SECRET, REFRESH_EXPIRY_DAYS, JWT_EXPIRY_MINUTES, Role, check_password_rules, decode_token, issue_token, passwords
from .database import SessionLocal, get_db
from .models import ActivityType, Approval, Assignment, AuditLog, AuthMfaChallenge, AuthSession, BusinessSequence, ClientMilestonePayment, Department, Document, Enquiry, EnquiryDocument, EnquiryFollowUp, EnquiryOnboarding, EnquirySLABreach, GoogleWorkspaceAccount, JobActivity, JobActivityAttachment, JobCard, MasterData, Milestone, MilestoneConsultantAllocation, MilestoneReviewHistory, MSME, Notification, Payment, PaymentReceipt, PlannedVisit, ProjectClosure, RecordDocument, ServicePackage, SystemSetting, Ticket, TicketAttachment, TicketRelationship, User, Visit, VisitReviewHistory, WorkflowAction, WorkflowActionAttachment, WorkflowTemplate, ZedReferenceDocument
from . import google_workspace
from .seed import disable_demo_accounts, ensure_payment_workflows, seed
from .services import MD_PRECISION, card_dashboard_row, manday_summary, milestone_summary, planner_actual_minutes
from .storage import MAX_BASE64_CHARS, MAX_BYTES, ROOT as STORAGE_ROOT, save as save_file
from .events import audit, notify, notify_once
from .reports import approval_rows, consultant_rows, document_rows, milestone_rows, payment_rows, project_rows, task_rows, to_csv, to_pdf, to_xlsx, visit_rows
from .mail import send_password_reset, send_email, send_initial_password
from .business_numbers import next_business_number

app=FastAPI(title="D'Mastru Job Card API",version="1.0.0")
bearer=HTTPBearer(auto_error=False)
DB=Annotated[Session,Depends(get_db)]
logger=logging.getLogger("job_card_api")
origins=[origin.strip() for origin in os.getenv("CORS_ORIGINS","http://127.0.0.1:5173,http://localhost:5173").split(",") if origin.strip()]
app.add_middleware(CORSMiddleware,allow_origins=origins,allow_credentials=False,allow_methods=["GET","POST","PATCH","PUT","DELETE"],allow_headers=["Authorization","Content-Type"],max_age=600)
RATE_WINDOW_SECONDS=int(os.getenv("RATE_LIMIT_WINDOW_SECONDS","60")); RATE_MAX_REQUESTS=int(os.getenv("RATE_LIMIT_MAX_REQUESTS","120")); LOGIN_MAX_REQUESTS=int(os.getenv("LOGIN_RATE_LIMIT_MAX_REQUESTS","10")); MFA_REQUIRED=os.getenv("MFA_REQUIRED","false").strip().lower() in ("1","true","yes"); _rates:dict[str,tuple[float,int]]={}

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
class LoginChallengeOut(BaseModel): challenge_token:str; setup_required:bool; password_change_required:bool=False; full_name:str; email:str
class MfaChallengeRequest(BaseModel): challenge_token:str=Field(min_length=32,max_length=128)
class MfaVerifyRequest(MfaChallengeRequest): code:str=Field(pattern="^[0-9]{6}$")
class MfaSetupOut(BaseModel): secret:str; provisioning_uri:str
class UserCreate(EmailModel): full_name: str=Field(min_length=2,max_length=160); password: str; mobile: str|None=None; consultant_id: str|None=None; role: Role=Role.END_USER; department_id:int|None=None
class UserAdminUpdate(BaseModel): email:str|None=Field(default=None,max_length=320);full_name:str|None=Field(default=None,min_length=2,max_length=160); mobile:str|None=None; consultant_id:str|None=None; role:Role|None=None; department_id:int|None=None
class InitialPasswordChange(BaseModel):
    challenge_token:str=Field(min_length=32,max_length=128); new_password:str; confirm_password:str
class UserOut(EmailModel):
    model_config=ConfigDict(from_attributes=True)
    id:int; full_name:str; role:Role; status:AccountStatus; mobile:str|None=None; department_id:int|None=None; mfa_enabled:bool=False
class UserCreateOut(UserOut): initial_password_email_sent:bool; message:str
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
class JobCardUpdate(BaseModel): model_config=ConfigDict(extra="forbid"); msme_id:int|None=None; location:str|None=Field(default=None,min_length=2,max_length=160); project_start_date:date|None=None; project_end_date:date|None=None; required_md:Decimal|None=Field(default=None,gt=0); hours_per_manday:Decimal|None=Field(default=None,gt=0,le=24); director_id:int|None=None; remarks:str|None=Field(default=None,max_length=10000);status:str|None=Field(default=None,pattern="^(AWAITING_ASSIGNMENT|ASSIGNED|IN_PROGRESS|ON_HOLD|CANCELLED|AWAITING_PAYMENT)$")
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
    msme_id:int; location:str=Field(min_length=2,max_length=160); project_start_date:date; project_end_date:date; required_md:Decimal|None=Field(default=None,gt=0); hours_per_manday:Decimal|None=Field(default=None,gt=0,le=24); lead_consultant_id:int|None=None; co_consultant_id:int|None=None; subject_expert_id:int|None=None; director_id:int|None=None;remarks:str|None=Field(default=None,max_length=10000)
    source_enquiry_id:UUID|None=None;project_name:str|None=Field(default=None,max_length=200);service_name:str|None=Field(default=None,max_length=250);package_code:str|None=Field(default=None,max_length=64);scope:str|None=Field(default=None,max_length=10000)
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
    code:str=Field(min_length=2,max_length=64,pattern="^[A-Z0-9_-]+$");name:str=Field(min_length=2,max_length=160);steps:list[WorkflowStepConfig]=Field(min_length=1,max_length=25);trigger_event:str=Field(default="MANUAL",pattern="^(MANUAL|PROJECT_ACTIVATED|JOB_CARD_CREATED|MILESTONE_COMPLETED|MILESTONE_PAID)$");active:bool=True
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
    id:int; number:str; source_ticket_id:UUID|None=None;source_enquiry_id:UUID|None=None;project_name:str|None=None;service_name:str|None=None;package_code:str|None=None;scope:str|None=None;msme_id:int; location:str; project_start_date:date; project_end_date:date; required_md:Decimal; hours_per_manday:Decimal=Decimal(8); required_hours:Decimal; unit_representative_id:int|None=None; director_id:int|None=None;remarks:str|None=None;status:str; payment_gate_enabled:bool=False
    @field_serializer("required_hours")
    def serialize_required_hours(self,value:Decimal)->Decimal: return value.quantize(Decimal("0.01"))
class JobCardListOut(JobCardOut): completed_md:Decimal=Decimal(0); remaining_md:Decimal=Decimal(0); progress:Decimal=Decimal(0); overrun:bool=False
class MilestoneCreate(BaseModel): code:str=Field(pattern="^M[123]$"); name:str=Field(min_length=2,max_length=120); planned_visits:int=Field(ge=1); planned_mandays:Decimal|None=Field(default=None,gt=0);amount:Decimal|None=Field(default=None,ge=0);payment_percent:Decimal|None=Field(default=None,ge=0,le=100);activities:list[str]=Field(default_factory=list);responsible_consultant_id:int|None=None;description:str|None=None; start_date:date|None=None; closing_date:date|None=None; payment_date:date|None=None; portal_report_submission_date:date|None=None
class MilestoneUpdate(BaseModel): name:str|None=Field(default=None,min_length=2,max_length=120); planned_visits:int|None=Field(default=None,ge=1);planned_mandays:Decimal|None=Field(default=None,gt=0);amount:Decimal|None=Field(default=None,ge=0);payment_percent:Decimal|None=Field(default=None,ge=0,le=100);activities:list[str]|None=None;responsible_consultant_id:int|None=None;description:str|None=None;start_date:date|None=None; closing_date:date|None=None; payment_date:date|None=None; portal_report_submission_date:date|None=None
class MilestoneConsultantAllocationInput(BaseModel): consultant_id:int;allocated_mandays:Decimal=Field(gt=0,max_digits=10,decimal_places=2)
class MilestoneConsultantAllocationRequest(BaseModel): allocations:list[MilestoneConsultantAllocationInput]=Field(min_length=1,max_length=3)
class MilestoneOut(MilestoneCreate):
    model_config=ConfigDict(from_attributes=True)
    id:int; job_card_id:int; status:str; payment_status:str|None="PAYMENT_PENDING"; client_payment_confirmed_at:datetime|None=None; invoice_status:str|None=None; invoice_number:str|None=None; invoice_amount:Decimal|None=None; invoice_due_date:date|None=None; invoice_ready:bool=False; actual_visits:int=0; progress:Decimal=Decimal(0); review_status:str="NOT_SUBMITTED"; review_cycle:int=0; review_remarks:str|None=None; assigned_consultants:int=0; estimated_working_days:Decimal|None=None; responsible_consultant_name:str|None=None; consultant_allocations:list[dict]=Field(default_factory=list)
class MilestoneReviewDecision(BaseModel):
    decision:str=Field(pattern="^(APPROVED|RETURNED|REJECTED)$");comment:str|None=Field(default=None,max_length=10000)
class MilestoneReviewOut(BaseModel):
    model_config=ConfigDict(from_attributes=True)
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
    status:str|None=Field(default=None,pattern="^(PLANNED|IN_PROGRESS|COMPLETED|CANCELLED)$");remarks:str|None=Field(default=None,max_length=10000);duration_minutes:int|None=Field(default=None,ge=0,le=100000);start_date:date|None=None;end_date:date|None=None;expected_completion_date:date|None=None
class JobActivityOut(BaseModel):
    id:int;job_card_id:int;milestone_id:int;visit_id:int|None;visit_number:int|None=None;planned_visit_id:int|None=None;activity_type_id:int;activity_type_code:str;activity_type_name:str;name:str;description:str;assigned_user_id:int;assigned_user_name:str;start_date:date|None;end_date:date|None;expected_completion_date:date|None=None;sla_due_at:datetime|None=None;duration_minutes:int;mandays:Decimal;status:str;review_status:str="NOT_SUBMITTED";review_remarks:str|None=None;reviewed_by:int|None=None;reviewed_at:datetime|None=None;remarks:str|None;created_by:int;created_at:datetime;reference_documents:list[dict]=Field(default_factory=list)
class JobActivityAttachmentOut(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id:int;activity_id:int;file_name:str;file_size:int;mime_type:str;uploaded_by:int;uploaded_at:datetime;status:str="DRAFT";review_remarks:str|None=None;reviewed_by:int|None=None;reviewed_at:datetime|None=None
class ActivityAttachmentReviewIn(BaseModel):
    decision:str=Field(pattern="^(UNDER_REVIEW|APPROVED|RETURNED|REJECTED)$");comment:str=Field(min_length=3,max_length=5000)
class VisitCreate(BaseModel):
    planned_visit_id:int|None=None;milestone_id:int;visit_date:date;hours:int=Field(default=0,ge=0);minutes:int=Field(default=0,ge=0,lt=60)
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
    job_card_id:int;milestone_id:int;visit_date:date;start_time:TimeOfDay;expected_duration_minutes:int=Field(gt=0,le=1440);location:str=Field(min_length=2,max_length=240);objective:str=Field(min_length=3,max_length=5000);co_consultant_id:int|None=None;subject_expert_id:int|None=None
class PlannedVisitUpdate(BaseModel):
    visit_date:date|None=None;start_time:TimeOfDay|None=None;expected_duration_minutes:int|None=Field(default=None,gt=0,le=1440);location:str|None=Field(default=None,min_length=2,max_length=240);objective:str|None=Field(default=None,min_length=3,max_length=5000);co_consultant_id:int|None=None;subject_expert_id:int|None=None;status:str|None=Field(default=None,pattern="^(PLANNED|CANCELLED|COMPLETED)$");reason:str|None=Field(default=None,min_length=3,max_length=5000);actual_date:date|None=None;actual_start_time:TimeOfDay|None=None;actual_duration_minutes:int|None=Field(default=None,gt=0,le=1440);outcome:str|None=Field(default=None,max_length=10000);attachment_name:str|None=Field(default=None,max_length=255);attachment_mime_type:str|None=Field(default=None,max_length=120);attachment_base64:str|None=None
class PlannedVisitOut(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id:int;job_card_id:int;job_card_number:str|None=None;msme_name:str|None=None;milestone_id:int;milestone_code:str|None=None;milestone_name:str|None=None;consultant_id:int;consultant_name:str|None=None;co_consultant_id:int|None=None;subject_expert_id:int|None=None;visit_number:int|None=None;visit_date:date;start_time:TimeOfDay;expected_duration_minutes:int;location:str;objective:str;status:str;change_reason:str|None=None;actual_date:date|None=None;actual_start_time:TimeOfDay|None=None;actual_duration_minutes:int|None=None;outcome:str|None=None;attachment_name:str|None=None;admin_escalated_at:datetime|None=None;management_escalated_at:datetime|None=None;created_by:int;created_at:datetime;updated_at:datetime
class VisitUpdate(VisitCreate): pass
class VisitOut(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id:int; job_card_id:int; milestone_id:int; planned_visit_id:int|None=None; visit_number:int; visit_date:date; start_time:TimeOfDay|None=None;end_time:TimeOfDay|None=None;location:str|None=None;findings:str|None=None;consultant_id:int|None=None; co_consultant_id:int|None=None; subject_expert_id:int|None=None; working_minutes:int; work_details:str; rating_code:str; rating_description:str; remarks:str|None=None; attachment_name:str|None=None; attachment_mime_type:str|None=None; status:str; rejection_reason:str|None=None; created_by:int; job_card_number:str|None=None
class VisitReviewDecision(BaseModel): decision:str=Field(pattern="^(APPROVED|RETURNED|REJECTED)$");comment:str|None=Field(default=None,max_length=5000)
class DocumentUpload(BaseModel):
    document_type:str=Field(min_length=2,max_length=80); file_name:str=Field(min_length=1,max_length=255); mime_type:str; content_base64:str; remarks:str|None=None
class DocumentOut(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id:int; job_card_id:int; document_type:str; file_name:str; file_size:int; mime_type:str; version:int; status:str; remarks:str|None=None; uploaded_by:int; reviewed_by:int|None=None;reviewed_at:datetime|None=None;job_card_number:str|None=None
class RecordDocumentUpload(BaseModel): document_type:str=Field(min_length=2,max_length=80);file_name:str=Field(min_length=1,max_length=255);mime_type:str;content_base64:str;remarks:str|None=Field(default=None,max_length=10000);status:str=Field(default="SUBMITTED",pattern="^(DRAFT|SUBMITTED)$")
class RecordDocumentOut(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id:int;owner_job_card_id:int|None=None;ticket_id:UUID|None=None;enquiry_id:UUID|None=None;job_card_id:int|None=None;milestone_id:int|None=None;action_id:UUID|None=None;visit_id:int|None=None;payment_id:int|None=None;milestone_review_id:int|None=None;visit_review_id:int|None=None;approval_id:int|None=None;activity_id:int|None=None
    document_type:str;file_name:str;file_size:int;mime_type:str;version:int;status:str;remarks:str|None=None;uploaded_by:int;uploaded_at:datetime;reviewed_by:int|None=None;reviewed_at:datetime|None=None;editor_provider:str|None=None;google_file_id:str|None=None;google_file_url:str|None=None
class RecordDocumentReview(BaseModel): decision:str=Field(pattern="^(UNDER_REVIEW|APPROVED|RETURNED|REJECTED)$");comment:str=Field(min_length=3,max_length=5000)
class ConsultantVisitReportIn(BaseModel):
    title:str=Field(min_length=3,max_length=160)
    summary:str=Field(min_length=10,max_length=10000)
    findings:str=Field(min_length=10,max_length=20000)
    actions:str=Field(default="",max_length=20000)
    conclusion:str=Field(min_length=5,max_length=10000)
    format:str=Field(default="docx",pattern="^(docx|xlsx)$")
class WorkProductDraftIn(BaseModel):
    document_id:int|None=None
    kind:str=Field(pattern="^(DOCUMENT|SHEET)$")
    title:str=Field(min_length=2,max_length=120)
    body:str=Field(default="",max_length=100000)
    rows:list[list[str]]=Field(default_factory=list,max_length=300)
    snapshot:dict|None=None
class GoogleWorkProductCreate(BaseModel):
    kind:str=Field(pattern="^(DOCUMENT|SHEET)$")
    title:str=Field(min_length=2,max_length=120)
    activity_id:int|None=None
class GoogleReferenceWorkProductCreate(BaseModel):
    title:str=Field(min_length=2,max_length=120)
    reference_document_id:int
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
    milestone_id:int|None=None
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
    id:int;invoice_number:str;job_card_id:int|None=None;enquiry_id:UUID|None=None;milestone_id:int|None=None
    description:str;amount_due:Decimal;amount_paid:Decimal;currency:str;status:str;invoice_date:date;due_date:date|None=None
    remarks:str|None=None;created_by:int;created_at:datetime;updated_at:datetime
class PaymentReceiptCreate(BaseModel):
    completed:bool=True
    amount:Decimal|None=Field(default=None,gt=0,max_digits=12,decimal_places=2)
    external_reference:str|None=Field(default=None,min_length=2,max_length=120)
    received_at:datetime=Field(default_factory=lambda:datetime.now(timezone.utc))
    remarks:str|None=Field(default=None,max_length=5000)
    @field_validator("external_reference")
    @classmethod
    def normalize_reference(cls,value):
        if value is None: return value
        value=value.strip().upper()
        if len(value)<2: raise ValueError("Payment reference must have at least two characters.")
        return value
class ClientMilestonePaymentConfirmation(BaseModel):
    completed:bool=True
    external_reference:str|None=Field(default=None,min_length=2,max_length=120)
    remarks:str|None=Field(default=None,max_length=5000)
class PaymentCancelIn(BaseModel): reason:str=Field(min_length=3,max_length=1000)
class EnquiryClientBriefingSchedule(BaseModel):
    meeting_type:str=Field(default="DISCUSSION",pattern="^(CALL|MEETING|DISCUSSION)$")
    scheduled_at:datetime
class EnquiryClientConfirmation(BaseModel):
    decision:str=Field(pattern="^(CONFIRMED|CHANGES_REQUESTED|DECLINED)$")
    note:str=Field(min_length=3,max_length=5000)
class EnquiryOnboardingCreate(BaseModel):
    msme_id:int|None=None
    address:str|None=Field(default=None,max_length=5000);city:str|None=Field(default=None,max_length=80);state:str|None=Field(default=None,max_length=80);unit_name:str|None=Field(default=None,max_length=160)
    service_name:str=Field(min_length=2,max_length=250);project_name:str|None=Field(default=None,max_length=200);scope:str=Field(min_length=3,max_length=10000);location:str=Field(min_length=2,max_length=160)
    package_code:str|None=Field(default=None,max_length=64)
class ZedBronzeCertificationCompletion(BaseModel):
    report_document_id:int=Field(gt=0)
    certificate_number:str|None=Field(default=None,max_length=120)
    silver_gold_interest:str=Field(pattern="^(YES|NO)$")
class ZedExpansionPlan(BaseModel):
    services:list[str]=Field(min_length=1,max_length=2)
    notes:str=Field(min_length=3,max_length=5000)
    follow_up_date:date|None=None
    client_accepted:bool=False
class EnquiryOnboardingOut(BaseModel):
    id:int;enquiry_id:UUID;enquiry_number:str;company_name:str;contact_person:str|None=None;email:str|None=None;phone:str|None=None;industry:str|None=None;enquiry_for:list[str]|None=None;zed_id:str|None=None;msme_id:int;msme_code:str;service_name:str;project_name:str;scope:str;location:str;package_code:str|None=None;planned_mandays:Decimal|None=None;project_timeline_start:date|None=None;project_timeline_end:date|None=None;commercial_amount:Decimal|None=None;initial_payment_amount:Decimal;status:str;payment:PaymentOut|None=None
    management_approval_status:str="PENDING"
    zed_bronze_certification_status:str="NOT_REQUIRED";zed_bronze_certificate_number:str|None=None;zed_bronze_report_document_id:int|None=None;zed_bronze_confirmed_at:datetime|None=None;zed_silver_gold_interest:str|None=None;zed_marketing_plan_status:str="NOT_REQUIRED";zed_marketing_plan_services:str|None=None;zed_marketing_plan_notes:str|None=None;zed_marketing_plan_due_date:date|None=None
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
    ENQUIRY_FOR_OPTIONS:ClassVar[set[str]] = {"ZED Silver", "ZED Bronze", "ZED Gold", "Lean", "ISO", "Training"}
    msme_id:int|None=None
    company_name:str=Field(min_length=2,max_length=200);contact_person:str|None=Field(default=None,max_length=160)
    email:str|None=Field(default=None,max_length=320);phone:str|None=Field(default=None,max_length=40)
    industry:str|None=Field(default=None,max_length=120);location:str|None=Field(default=None,max_length=200)
    requirement:str=Field(min_length=3,max_length=10000);enquiry_date:date=Field(default_factory=date.today);deadline_date:date
    enquiry_for:list[str]=Field(min_length=1);zed_id:str|None=Field(default=None,max_length=120);zed_pass:str|None=Field(default=None,max_length=500);feedback:str|None=Field(default=None,max_length=10000)
    source:str=Field(pattern="^(MARKETING_TEAM|MANAGEMENT|CONSULTANT_REFERRAL|USER_REFERRAL)$");referral_consultant_id:int|None=None;package_code:str|None=Field(default=None,max_length=64)
    expected_scope:str|None=Field(default=None,max_length=10000);expected_commercial_amount:Decimal|None=Field(default=None,ge=0,max_digits=12,decimal_places=2);assigned_marketing_user_id:int|None=None
    status:str=Field(default="NEW",pattern="^(NEW|CONTACTED|QUALIFIED|LOST|CANCELLED)$")
    follow_up_date:date|None=None;next_follow_up_type:str|None=Field(default=None,pattern="^(CALL|MEETING|EMAIL|CLIENT_DISCUSSION)$");remarks:str|None=Field(default=None,max_length=10000)
    @field_validator("email")
    @classmethod
    def normalize_optional_email(cls,value): return value.strip().lower() if value else value
    @field_validator("enquiry_for")
    @classmethod
    def validate_enquiry_for(cls,value):
        if not value or set(value)-EnquiryCreate.ENQUIRY_FOR_OPTIONS: raise ValueError("Choose one or more supported enquiry services.")
        return list(dict.fromkeys(value))
    @model_validator(mode="after")
    def validate_referral(self):
        if (self.source=="CONSULTANT_REFERRAL") != (self.referral_consultant_id is not None): raise ValueError("Select a consultant only for Consultant referral enquiries.")
        if not any(option.startswith("ZED ") for option in self.enquiry_for) and (self.zed_id or self.zed_pass): raise ValueError("ZED details require a ZED enquiry service.")
        return self
class EnquiryUpdate(BaseModel):
    ENQUIRY_FOR_OPTIONS:ClassVar[set[str]] = EnquiryCreate.ENQUIRY_FOR_OPTIONS
    msme_id:int|None=None;company_name:str|None=Field(default=None,min_length=2,max_length=200)
    contact_person:str|None=Field(default=None,max_length=160);email:str|None=Field(default=None,max_length=320)
    phone:str|None=Field(default=None,max_length=40);industry:str|None=Field(default=None,max_length=120)
    location:str|None=Field(default=None,max_length=200);requirement:str|None=Field(default=None,min_length=3,max_length=10000)
    enquiry_date:date|None=None;deadline_date:date|None=None;source:str|None=Field(default=None,pattern="^(MARKETING_TEAM|MANAGEMENT|CONSULTANT_REFERRAL|USER_REFERRAL)$");referral_consultant_id:int|None=None;package_code:str|None=Field(default=None,max_length=64)
    enquiry_for:list[str]|None=None;zed_id:str|None=Field(default=None,max_length=120);zed_pass:str|None=Field(default=None,max_length=500);feedback:str|None=Field(default=None,max_length=10000)
    expected_scope:str|None=Field(default=None,max_length=10000);expected_commercial_amount:Decimal|None=Field(default=None,ge=0,max_digits=12,decimal_places=2);assigned_marketing_user_id:int|None=None
    status:str|None=Field(default=None,pattern="^(NEW|CONTACTED|QUALIFIED|LOST|CANCELLED|CONVERTED)$")
    follow_up_date:date|None=None;remarks:str|None=Field(default=None,max_length=10000)
    @field_validator("enquiry_for")
    @classmethod
    def validate_enquiry_for(cls,value):
        if value is None: return value
        if not value or set(value)-EnquiryUpdate.ENQUIRY_FOR_OPTIONS: raise ValueError("Choose one or more supported enquiry services.")
        return list(dict.fromkeys(value))
class EnquiryLinkJobCard(BaseModel): job_card_id:int
class EnquiryOut(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id:UUID;number:str;msme_id:int|None=None;company_name:str;deadline_date:date|None=None
    enquiry_for:list[str]|None=None;zed_id:str|None=None;zed_pass_set:bool=False;feedback:str|None=None
    contact_person:str|None=None;email:str|None=None;phone:str|None=None;industry:str|None=None;location:str|None=None
    requirement:str;enquiry_date:date;source:str|None=None;package_code:str|None=None;expected_scope:str|None=None;expected_commercial_amount:Decimal|None=None
    assigned_marketing_user_id:int|None=None;referral_consultant_id:int|None=None;status:str;management_approval_status:str="NOT_REQUIRED";management_approval_comment:str|None=None;management_approved_at:datetime|None=None;follow_up_date:date|None=None;remarks:str|None=None
    created_by:int;created_at:datetime;updated_at:datetime
class EnquiryApprovalDecision(BaseModel):
    decision:str=Field(pattern="^(APPROVED|REJECTED)$")
    comment:str|None=Field(default=None,max_length=5000)
class EnquiryFollowUpCreate(BaseModel):
    follow_up_date:datetime=Field(default_factory=lambda:datetime.now(timezone.utc))
    follow_up_type:str=Field(pattern="^(CALL|MEETING|EMAIL|CLIENT_DISCUSSION|FOLLOW_UP|REQUIREMENT_UPDATE|COMMERCIAL_DISCUSSION|SCOPE_DISCUSSION)$")
    person_contacted:str|None=Field(default=None,max_length=160);notes:str=Field(min_length=2,max_length=10000)
    next_follow_up_date:datetime|None=None;next_follow_up_type:str|None=Field(default=None,pattern="^(CALL|MEETING|EMAIL|CLIENT_DISCUSSION)$");status:str=Field(default="COMPLETED",pattern="^(OPEN|COMPLETED|CANCELLED)$")
    attachment_name:str|None=Field(default=None,max_length=255);attachment_mime_type:str|None=None;attachment_base64:str|None=None
class EnquiryFollowUpOut(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id:int;enquiry_id:UUID;follow_up_date:datetime;follow_up_type:str;person_contacted:str|None=None
    notes:str;next_follow_up_date:datetime|None=None;next_follow_up_type:str|None=None;responsible_user_id:int;status:str;attachment_name:str|None=None;attachment_mime_type:str|None=None;created_at:datetime
class RecordFileUpload(BaseModel):
    file_name:str=Field(min_length=1,max_length=255);mime_type:str=Field(min_length=3,max_length=120)
    content_base64:str=Field(min_length=8,max_length=MAX_BASE64_CHARS);document_type:str|None=Field(default=None,min_length=2,max_length=80);remarks:str|None=Field(default=None,max_length=5000)
class RecordFileOut(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id:int;file_name:str;file_size:int;mime_type:str;uploaded_by:int;uploaded_at:datetime
class TicketAttachmentOut(RecordFileOut): ticket_id:UUID
class EnquiryDocumentOut(RecordFileOut): enquiry_id:UUID;document_type:str;remarks:str|None=None;google_file_id:str|None=None;google_file_url:str|None=None;google_synced_at:datetime|None=None
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
    if not session or session.revoked or not session.mfa_verified or expiry < datetime.now(timezone.utc): raise HTTPException(401,"Your session has expired. Please sign in again.")
    user=db.get(User,int(claims["sub"]))
    if not user or user.status != AccountStatus.ACTIVE: raise HTTPException(403,"Your account is not active.")
    return user
def ticket_workspace_user(user:Annotated[User,Depends(current_user)]):
    if user.role==Role.MARKETING: raise HTTPException(404,"Not found.")
    return user
def require(*roles:Role):
    def check(user:Annotated[User,Depends(current_user)]):
        if roles and user.role not in roles: raise HTTPException(403,"You do not have permission to access this resource.")
        return user
    return check
def token_hash(value:str): return hashlib.sha256(value.encode()).hexdigest()
def _field_cipher():
    try: from cryptography.fernet import Fernet
    except ImportError: raise HTTPException(503,"Secure field encryption dependency is not installed.")
    configured=os.getenv("FIELD_ENCRYPTION_KEY")
    if configured: key=configured.encode()
    elif ENV.lower() in ("production","prod"):
        raise HTTPException(503,"Secure field encryption is not configured.")
    else: key=base64.urlsafe_b64encode(hashlib.sha256(os.getenv("JWT_SECRET","development-only-field-encryption").encode()).digest())
    try: return Fernet(key)
    except (ValueError,TypeError): raise HTTPException(503,"Secure field encryption is not configured.")
def _encrypt_sensitive(value:str|None)->str|None:
    if not value: return None
    return _field_cipher().encrypt(value.encode()).decode()
def issue_session(db:Session,user:User):
    sid=secrets.token_urlsafe(32); refresh=secrets.token_urlsafe(48); now=datetime.now(timezone.utc)
    db.add(AuthSession(id=sid,user_id=user.id,expires_at=now+timedelta(minutes=JWT_EXPIRY_MINUTES),refresh_token_hash=token_hash(refresh),refresh_expires_at=now+timedelta(days=REFRESH_EXPIRY_DAYS),mfa_verified=True))
    return TokenOut(access_token=issue_token(user.id,sid),refresh_token=refresh,user=user)

def _create_mfa_challenge(db:Session,user:User)->str:
    raw=secrets.token_urlsafe(40)
    db.add(AuthMfaChallenge(token_hash=token_hash(raw),user_id=user.id,expires_at=datetime.now(timezone.utc)+timedelta(minutes=5)))
    return raw

def _get_mfa_challenge(db:Session,raw:str)->tuple[AuthMfaChallenge,User]:
    challenge=db.scalar(select(AuthMfaChallenge).where(AuthMfaChallenge.token_hash==token_hash(raw)).with_for_update())
    now=datetime.now(timezone.utc)
    expiry=challenge.expires_at.replace(tzinfo=timezone.utc) if challenge and challenge.expires_at.tzinfo is None else (challenge.expires_at if challenge else None)
    if not challenge or challenge.used_at or challenge.attempts>=5 or not expiry or expiry<=now:
        raise HTTPException(401,"Your sign-in verification expired. Start again.")
    user=db.get(User,challenge.user_id)
    if not user or user.status!=AccountStatus.ACTIVE: raise HTTPException(403,"Your account is not active.")
    return challenge,user

def _decrypt_mfa_secret(user:User)->str:
    if not user.mfa_secret_encrypted: raise HTTPException(409,"Authenticator setup must be started again.")
    try: return _field_cipher().decrypt(user.mfa_secret_encrypted.encode()).decode()
    except Exception: raise HTTPException(503,"Authenticator secret cannot be read. Contact your administrator.")

def _totp_match(secret:str,code:str,now:datetime|None=None)->int|None:
    current=int((now or datetime.now(timezone.utc)).timestamp())//30
    key=base64.b32decode(secret+"="*((8-len(secret)%8)%8),casefold=True)
    for counter in (current-1,current,current+1):
        digest=hmac.new(key,struct.pack(">Q",counter),hashlib.sha1).digest()
        offset=digest[-1]&0x0f
        value=struct.unpack(">I",digest[offset:offset+4])[0]&0x7fffffff
        if hmac.compare_digest(f"{value%1_000_000:06d}",code): return counter
    return None

def _verify_mfa_challenge(db:Session,request:MfaVerifyRequest,*,enrollment:bool):
    challenge,user=_get_mfa_challenge(db,request.challenge_token)
    if user.password_change_required: raise HTTPException(409,"Set a permanent password before setting up MFA.")
    if enrollment and user.mfa_enabled: raise HTTPException(409,"MFA is already enabled for this account.")
    if not enrollment and not user.mfa_enabled: raise HTTPException(409,"Complete authenticator setup before signing in.")
    secret=_decrypt_mfa_secret(user)
    matched_counter=_totp_match(secret,request.code)
    if matched_counter is None or (user.mfa_last_counter is not None and matched_counter<=user.mfa_last_counter):
        challenge.attempts+=1
        if challenge.attempts>=5: challenge.used_at=datetime.now(timezone.utc)
        audit(db,user.id,"MFA_VERIFICATION_FAILED","USER",user.id,f"attempts={challenge.attempts}")
        db.commit()
        raise HTTPException(401,"Invalid or expired authenticator code.")
    challenge.used_at=datetime.now(timezone.utc)
    user.mfa_last_counter=matched_counter
    if enrollment: user.mfa_enabled=True
    user.last_login_at=datetime.now(timezone.utc)
    token=issue_session(db,user)
    audit(db,user.id,"MFA_ENROLLED" if enrollment else "MFA_LOGIN_VERIFIED","USER",user.id)
    audit(db,user.id,"LOGIN","USER",user.id,"MFA verified")
    db.commit()
    return token

def _can_manage_crm(user:User)->bool:
    return user.role in (Role.ADMIN,Role.MARKETING)

def _validate_referral_consultant(db:Session,consultant_id:int|None):
    if consultant_id is None: return
    consultant=db.get(User,consultant_id)
    if not consultant or consultant.role!=Role.END_USER or consultant.status!=AccountStatus.ACTIVE:
        raise HTTPException(422,"Choose an active registered consultant.")

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
    return user.role in (Role.ADMIN,Role.MANAGEMENT) or enquiry.created_by==user.id or enquiry.assigned_marketing_user_id==user.id or (user.role==Role.MARKETING and enquiry.source=="USER_REFERRAL")

def _package_supports_service(package:ServicePackage,services:list[str])->bool:
    package_words=set(re.sub(r"[^a-z0-9]+"," ",f"{package.code} {package.name}".casefold()).split())
    for service in services:
        words=re.sub(r"[^a-z0-9]+"," ",service.casefold()).split()
        if words and (set(words).issubset(package_words) or words[-1] in package_words):
            return True
    return False

def _ensure_enquiry_sla_breaches(db:Session,user:User,now:datetime|None=None):
    now=now or datetime.now(timezone.utc)
    business_timezone=ZoneInfo(os.getenv("APP_TIMEZONE","Asia/Kolkata"))
    local_now=now.astimezone(business_timezone)
    scope=[]
    if user.role==Role.MARKETING: scope.append(or_(Enquiry.created_by==user.id,Enquiry.assigned_marketing_user_id==user.id))
    has_response=select(EnquiryFollowUp.id).where(EnquiryFollowUp.enquiry_id==Enquiry.id,EnquiryFollowUp.status=="COMPLETED").exists()
    response_candidates=list(db.scalars(select(Enquiry).where(*scope,Enquiry.deadline_date.is_not(None),Enquiry.deadline_date<=local_now.date(),Enquiry.status.not_in(("CONVERTED","LOST","CANCELLED")),~has_response)))
    existing_responses=set(db.scalars(select(EnquirySLABreach.enquiry_id).where(EnquirySLABreach.breach_type=="REQUEST_RESPONSE",EnquirySLABreach.follow_up_id.is_(None))))
    events=[]
    for enquiry in response_candidates:
        if enquiry.id in existing_responses: continue
        due=datetime.combine(enquiry.deadline_date+timedelta(days=1),TimeOfDay.min,tzinfo=business_timezone)
        if due>now: continue
        breach=EnquirySLABreach(enquiry_id=enquiry.id,breach_type="REQUEST_RESPONSE",due_at=due,breached_at=now,status="OPEN")
        db.add(breach)
        events.append((enquiry,"first response overdue",breach))
    follow_rows=db.execute(select(EnquiryFollowUp,Enquiry).join(Enquiry,Enquiry.id==EnquiryFollowUp.enquiry_id).where(*scope,Enquiry.status.not_in(("CONVERTED","LOST","CANCELLED")),EnquiryFollowUp.status!="CANCELLED",EnquiryFollowUp.next_follow_up_date<=now)).all()
    existing_followups=set(db.scalars(select(EnquirySLABreach.follow_up_id).where(EnquirySLABreach.breach_type=="FOLLOW_UP",EnquirySLABreach.follow_up_id.is_not(None))))
    for followup,enquiry in follow_rows:
        if followup.id in existing_followups: continue
        due=followup.next_follow_up_date
        if due.tzinfo is None: due=due.replace(tzinfo=timezone.utc)
        breach=EnquirySLABreach(enquiry_id=enquiry.id,follow_up_id=followup.id,breach_type="FOLLOW_UP",due_at=due,breached_at=now,status="OPEN")
        db.add(breach)
        events.append((enquiry,"scheduled follow-up overdue",breach))
    db.flush()
    for enquiry,reason,breach in events:
        audit(db,user.id,"ENQUIRY_SLA_BREACHED","ENQUIRY",None,f"{enquiry.number}: {reason}",entity_ref=str(enquiry.id))
        target_id=enquiry.assigned_marketing_user_id or enquiry.created_by
        notify_once(db,target_id,f"SLA breached for enquiry {enquiry.number}: {reason}.",f"/marketing?search={enquiry.number}",f"enquiry-sla-breach-{breach.id}")

def _resolve_enquiry_sla_breaches(db:Session,enquiry:Enquiry,user:User):
    now=datetime.now(timezone.utc)
    active=list(db.scalars(select(EnquirySLABreach).where(EnquirySLABreach.enquiry_id==enquiry.id,EnquirySLABreach.status=="OPEN")))
    for breach in active:
        breach.status="RESOLVED";breach.resolved_at=now
        audit(db,user.id,"ENQUIRY_SLA_RESOLVED","ENQUIRY",None,f"{enquiry.number}: {breach.breach_type}",entity_ref=str(enquiry.id))

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

def _milestone_allocations(db:Session,milestone:Milestone):
    rows=list(db.scalars(select(MilestoneConsultantAllocation).where(MilestoneConsultantAllocation.milestone_id==milestone.id,MilestoneConsultantAllocation.active.is_(True)).order_by(MilestoneConsultantAllocation.id)))
    if rows:return rows
    if milestone.responsible_consultant_id and milestone.planned_mandays:
        return [MilestoneConsultantAllocation(milestone_id=milestone.id,consultant_id=milestone.responsible_consultant_id,allocated_mandays=milestone.planned_mandays,assigned_by=None,active=True)]
    return []

def _milestone_consultant_ids(db:Session,milestone:Milestone)->set[int]:
    return {row.consultant_id for row in _milestone_allocations(db,milestone)}

def _milestone_staffing_summary(db:Session,milestone:Milestone):
    allocations=[]
    for row in _milestone_allocations(db,milestone):
        consultant=db.get(User,row.consultant_id)
        if consultant and consultant.role==Role.END_USER and consultant.status==AccountStatus.ACTIVE:
            allocations.append({"consultant_id":consultant.id,"consultant_name":consultant.full_name,"allocated_mandays":str(row.allocated_mandays)})
    lead=next((row for row in allocations if row["consultant_id"]==milestone.responsible_consultant_id),allocations[0] if allocations else None)
    # Calendar duration depends on whether consultants work together or on separate visits;
    # only the visit plan can determine it, so do not imply a fixed duration here.
    return {"assigned_consultants":len(allocations),"estimated_working_days":None,"responsible_consultant_name":lead["consultant_name"] if lead else None,"consultant_allocations":allocations}

def _milestone_out_for_user(db:Session,milestone:Milestone,user:User):
    values={**milestone.__dict__,**milestone_summary(db,milestone),**_milestone_staffing_summary(db,milestone)}
    client_payment=db.scalar(select(ClientMilestonePayment).where(ClientMilestonePayment.milestone_id==milestone.id))
    invoice=db.scalar(select(Payment).where(Payment.milestone_id==milestone.id).order_by(Payment.id.desc()).limit(1))
    next_code={"M1":"M2","M2":"M3"}.get(milestone.code)
    next_milestone=db.scalar(select(Milestone).where(Milestone.job_card_id==milestone.job_card_id,Milestone.code==next_code)) if next_code else None
    next_paid=bool(next_milestone and db.scalar(select(ClientMilestonePayment.id).where(ClientMilestonePayment.milestone_id==next_milestone.id)))
    invoice_data=_payment_out(invoice) if invoice else None
    values.update(client_payment_confirmed_at=client_payment.confirmed_at if client_payment else None,
        invoice_status=invoice_data["status"] if invoice_data else None,invoice_number=invoice.invoice_number if invoice else None,
        invoice_amount=invoice.amount_due if invoice else None,invoice_due_date=invoice.due_date if invoice else None,
        invoice_ready=milestone_summary(db,milestone)["status"]=="COMPLETED" and (next_paid or next_milestone is None) and (not invoice or invoice.status=="CANCELLED"))
    if not db.get(JobCard,milestone.job_card_id).payment_gate_enabled:
        values["payment_status"]="NOT_REQUIRED"
        values["invoice_ready"]=False
    if user.role==Role.ADMIN:
        values["amount"]=None
        values["payment_percent"]=None
        values["payment_date"]=None
        values["invoice_status"]=None;values["invoice_number"]=None;values["invoice_amount"]=None;values["invoice_due_date"]=None;values["invoice_ready"]=False;values["client_payment_confirmed_at"]=None
        if values.get("status")=="LOCKED": values["status"]="PENDING_ACTIVATION"
    return MilestoneOut(**values)

def _require_work_payment(db:Session,card:JobCard,milestone:Milestone|None=None):
    if not card.payment_gate_enabled: return
    if milestone is None:
        milestone=db.scalar(select(Milestone).where(Milestone.job_card_id==card.id,Milestone.code=="M1"))
    if milestone:
        milestone_paid=db.scalar(select(ClientMilestonePayment.id).where(ClientMilestonePayment.milestone_id==milestone.id))
        if not milestone_paid: raise HTTPException(423,f"{milestone.code} work is locked until Finance confirms its milestone payment.")
        if not milestone.responsible_consultant_id and milestone.code=="M1":
            legacy_assignment=db.scalar(select(Assignment).where(Assignment.job_card_id==card.id,Assignment.assignment_type=="LEAD_CONSULTANT",Assignment.active.is_(True)).limit(1))
            if legacy_assignment:
                active_consultant(db,legacy_assignment.user_id)
                milestone.responsible_consultant_id=legacy_assignment.user_id
    if not milestone or not _milestone_consultant_ids(db,milestone):
        raise HTTPException(423,"Finance has confirmed payment. Admin must assign a consultant to this milestone before work can start.")

def _record_milestone_consultant_assignment(db:Session,milestone:Milestone,consultant_id:int,admin:User):
    card=db.get(JobCard,milestone.job_card_id)
    assignment=db.scalar(select(Assignment).where(Assignment.job_card_id==card.id,Assignment.user_id==consultant_id,Assignment.assignment_type=="LEAD_CONSULTANT",Assignment.active.is_(True)))
    if not assignment:
        assignment=Assignment(job_card_id=card.id,user_id=consultant_id,assignment_type="LEAD_CONSULTANT",assigned_by=admin.id,allocation_start_date=milestone.start_date,allocation_end_date=milestone.closing_date)
        db.add(assignment)
        allocation=next((row for row in _milestone_allocations(db,milestone) if row.consultant_id==consultant_id),None)
        allocated_md=allocation.allocated_mandays if allocation else milestone.planned_mandays or 0
        notify(db,consultant_id,f"You were assigned to {milestone.code} on Job Card {card.number} ({allocated_md} mandays).",f"/job-cards/{card.id}")
    if milestone.code=="M1" and card.status in ("AWAITING_PAYMENT","AWAITING_ASSIGNMENT") and db.scalar(select(ClientMilestonePayment.id).where(ClientMilestonePayment.milestone_id==milestone.id)):
        card.status="ASSIGNED"

def _m1_payment_confirmed(db:Session,card:JobCard)->bool:
    return bool(db.scalar(select(ClientMilestonePayment.id).join(Milestone,ClientMilestonePayment.milestone_id==Milestone.id).where(Milestone.job_card_id==card.id,Milestone.code=="M1").limit(1)))

def _require_all_project_payments(db:Session,card:JobCard):
    _require_work_payment(db,card)
    if card.payment_gate_enabled:
        for milestone in db.scalars(select(Milestone).where(Milestone.job_card_id==card.id)):
            _require_work_payment(db,card,milestone)

def _decode_encoded_file(encoded:str)->bytes:
    if len(encoded)>MAX_BASE64_CHARS: raise HTTPException(413,"Each file must be smaller than 1 GB.")
    try: content=base64.b64decode(encoded,validate=True)
    except (ValueError,TypeError): raise HTTPException(422,"The uploaded attachment is invalid.")
    if not content or len(content)>MAX_BYTES: raise HTTPException(413,"Each file must be smaller than 1 GB.")
    return content

def _save_encoded_file(file_name:str,mime_type:str,encoded:str):
    try:
        content=_decode_encoded_file(encoded)
        key=save_file(file_name,mime_type,content)
        return key,len(content)
    except ValueError as exc: raise HTTPException(422,str(exc))
    except HTTPException: raise
    except Exception: raise HTTPException(422,"The uploaded attachment is invalid.")

def _download_attachment(file_name:str,mime_type:str,key:str):
    from .storage import ROOT
    path=ROOT/key
    if not path.is_file(): raise HTTPException(404,"Attachment file not found.")
    return Response(content=path.read_bytes(),media_type=mime_type,headers={"Content-Disposition":f"attachment; filename*=UTF-8''{quote(file_name,safe='')}"})

@app.post("/api/job-cards/{card_id}/payments",response_model=PaymentOut,status_code=201)
def create_payment_invoice(card_id:int,data:PaymentCreate,db:DB,user:Annotated[User,Depends(require(Role.FINANCE))]):
    card=db.get(JobCard,card_id)
    if not card or card.deleted_at: raise HTTPException(404,"Job Card not found.")
    mutable_card(card)
    if data.milestone_id:
        milestone=db.get(Milestone,data.milestone_id)
        if not milestone or milestone.job_card_id!=card_id: raise HTTPException(422,"The selected milestone does not belong to this Job Card.")
        if milestone_summary(db,milestone)["status"]!="COMPLETED": raise HTTPException(409,"D'Mastru can invoice QCI only for a completed milestone.")
        next_code={"M1":"M2","M2":"M3"}.get(milestone.code)
        next_milestone=db.scalar(select(Milestone).where(Milestone.job_card_id==card.id,Milestone.code==next_code)) if next_code else None
        if next_milestone and not db.scalar(select(ClientMilestonePayment.id).where(ClientMilestonePayment.milestone_id==next_milestone.id)):
            raise HTTPException(409,f"The client's {next_code} payment to QCI must be confirmed before raising D'Mastru's invoice to QCI for {milestone.code}.")
    else: milestone=None
    if card.payment_gate_enabled and milestone is None:
        raise HTTPException(422,"Only milestone payment invoices are supported for Job Cards.")
    if card.commercial_amount is not None:
        expected_amount=(Decimal(str(card.commercial_amount))*Decimal(str(card.project_payment_percent or 0))/Decimal(100)) if milestone is None else (Decimal(str(milestone.amount)) if milestone.amount is not None else None)
        if expected_amount is not None and Decimal(data.amount_due)!=expected_amount:
            raise HTTPException(422,"Invoice amount must match the commercial amount configured for this package payment stage.")
    existing=db.scalar(select(Payment).where(Payment.job_card_id==card_id,Payment.milestone_id==data.milestone_id,Payment.status!="CANCELLED"))
    if existing: raise HTTPException(409,"An invoice already exists for this project or milestone. Update or cancel the existing invoice instead.")
    values=data.model_dump();values["amount_due"]=Decimal(values["amount_due"]);values["amount_paid"]=Decimal("0.00");values["status"]="INVOICE_RAISED"
    invoice=Payment(**values,invoice_number=next_business_number(db,"invoice"),job_card_id=card_id,created_by=user.id)
    if milestone and milestone.amount is None:
        milestone.amount=Decimal(data.amount_due)
        audit(db,user.id,"QCI_INVOICE_AMOUNT_SET","MILESTONE",milestone.id,f"Finance configured D'Mastru invoice amount to QCI for {milestone.code}.")
    db.add(invoice)
    try: db.flush()
    except IntegrityError:
        db.rollback();raise HTTPException(409,"An invoice already exists for this project or milestone.")
    for assignment in db.scalars(select(Assignment).where(Assignment.job_card_id==card_id,Assignment.active.is_(True))):
        notify(db,assignment.user_id,f"Invoice {invoice.invoice_number} was raised for Job Card {card.number}.",f"/job-cards/{card_id}")
    audit(db,user.id,"QCI_INVOICE_CREATED","PAYMENT",invoice.id,f"D'Mastru invoice {invoice.invoice_number} to QCI; amount={invoice.amount_due}; milestone={milestone.code if milestone else 'PROJECT'}")
    db.commit();db.refresh(invoice);return invoice

@app.get("/api/job-cards/{card_id}/payments",response_model=list[PaymentOut])
def list_payments(card_id:int,db:DB,user:Annotated[User,Depends(require(Role.FINANCE,Role.MANAGEMENT))]):
    if not db.scalar(select(JobCard.id).where(JobCard.id==card_id,JobCard.deleted_at.is_(None))): raise HTTPException(404,"Job Card not found.")
    rows=list(db.scalars(select(Payment).where(Payment.job_card_id==card_id).order_by(Payment.invoice_date.desc(),Payment.id.desc())))
    return [_payment_out(payment) for payment in rows]

@app.get("/api/payments/{payment_id}/receipts",response_model=list[PaymentReceiptOut])
def list_payment_receipts(payment_id:int,db:DB,user:Annotated[User,Depends(require(Role.FINANCE,Role.MANAGEMENT))]):
    payment=db.get(Payment,payment_id)
    if not payment: raise HTTPException(404,"Invoice not found.")
    if payment.job_card_id and not db.scalar(select(JobCard.id).where(JobCard.id==payment.job_card_id,JobCard.deleted_at.is_(None))): raise HTTPException(404,"Job Card not found.")
    return list(db.scalars(select(PaymentReceipt).where(PaymentReceipt.payment_id==payment_id).order_by(PaymentReceipt.received_at.desc())))

@app.get("/api/finance/job-cards")
def finance_job_cards(db:DB,finance:Annotated[User,Depends(require(Role.FINANCE))]):
    cards=list(db.scalars(select(JobCard).where(JobCard.deleted_at.is_(None),JobCard.payment_gate_enabled.is_(True)).order_by(JobCard.created_at.desc())))
    items=[]
    for card in cards:
        milestones=list(db.scalars(select(Milestone).where(Milestone.job_card_id==card.id).order_by(Milestone.code)))
        items.append({"id":card.id,"number":card.number,"location":card.location,"status":card.status,"milestones":[{**{key:value for key,value in _milestone_out_for_user(db,m,finance).model_dump(mode="json").items() if key in ("id","code","name","status","payment_status","client_payment_confirmed_at","invoice_status","invoice_number","invoice_amount","invoice_due_date","invoice_ready")}} for m in milestones]})
    return items

@app.post("/api/milestones/{milestone_id}/client-payment-confirmation",status_code=201)
def confirm_client_qci_payment(milestone_id:int,data:ClientMilestonePaymentConfirmation,db:DB,user:Annotated[User,Depends(require(Role.FINANCE))]):
    milestone=db.scalar(select(Milestone).where(Milestone.id==milestone_id).with_for_update())
    if not milestone: raise HTTPException(404,"Milestone not found.")
    card=mutable_card(db.get(JobCard,milestone.job_card_id))
    if not card.payment_gate_enabled: raise HTTPException(409,"This Job Card does not require client payment confirmation.")
    if milestone_summary(db,milestone)["status"]=="COMPLETED": raise HTTPException(409,"Client payment confirmation is not needed for a completed milestone.")
    previous_code={"M2":"M1","M3":"M2"}.get(milestone.code)
    if previous_code:
        previous=db.scalar(select(Milestone).where(Milestone.job_card_id==card.id,Milestone.code==previous_code))
        if not previous or milestone_summary(db,previous)["status"]!="COMPLETED": raise HTTPException(409,f"{previous_code} must be completed and approved before confirming the client's {milestone.code} payment to QCI.")
    if not data.completed: raise HTTPException(422,"Confirm only after the client payment to QCI is complete.")
    existing=db.scalar(select(ClientMilestonePayment).where(ClientMilestonePayment.milestone_id==milestone.id))
    if existing: raise HTTPException(409,"The client payment to QCI is already confirmed for this milestone.")
    reference=data.external_reference.strip().upper() if data.external_reference else None
    payment=ClientMilestonePayment(milestone_id=milestone.id,external_reference=reference,remarks=data.remarks,confirmed_by=user.id,confirmed_at=datetime.now(timezone.utc))
    db.add(payment);milestone.status="READY"
    if milestone.code=="M1":
        card.status="ASSIGNED" if milestone.responsible_consultant_id else "AWAITING_ASSIGNMENT"
    _finish_milestone_payment_actions(db,milestone,user)
    _start_triggered_workflows(db,"MILESTONE_PAID",card,user,milestone)
    audit(db,user.id,"CLIENT_QCI_MILESTONE_PAYMENT_CONFIRMED","MILESTONE",milestone.id,f"Client payment to QCI completed for {card.number} {milestone.code}; reference={reference or 'not provided'}.")
    for admin in db.scalars(select(User).where(User.role==Role.ADMIN,User.status==AccountStatus.ACTIVE)):
        notify(db,admin.id,f"Client payment to QCI is confirmed for {milestone.code} on Job Card {card.number}. Admin can now assign a consultant to this milestone.",f"/admin/job-cards?search={quote(card.number)}&focus=allocation")
    if previous_code:
        for finance_user in db.scalars(select(User).where(User.role==Role.FINANCE,User.status==AccountStatus.ACTIVE)):
            notify(db,finance_user.id,f"The client’s {milestone.code} payment to QCI is confirmed. Raise and track D’Mastru’s invoice to QCI for the completed {previous_code} milestone on Job Card {card.number}.",f"/finance/payments?job_card={card.id}&milestone_id={previous.id}")
    db.commit();db.refresh(payment)
    return {"milestone_id":milestone.id,"job_card_id":card.id,"milestone_code":milestone.code,"confirmed":True,"external_reference":payment.external_reference,"remarks":payment.remarks,"confirmed_by":payment.confirmed_by,"confirmed_at":payment.confirmed_at}

@app.post("/api/payments/{payment_id}/confirm",response_model=PaymentOut)
def confirm_payment(payment_id:int,data:PaymentReceiptCreate,db:DB,user:Annotated[User,Depends(require(Role.FINANCE))]):
    payment=db.scalar(select(Payment).where(Payment.id==payment_id).with_for_update())
    if not payment: raise HTTPException(404,"Invoice not found.")
    card=db.get(JobCard,payment.job_card_id) if payment.job_card_id else None
    if payment.job_card_id and not card: raise HTTPException(404,"Job Card not found.")
    if card: mutable_card(card)
    if not card and (payment.enquiry_id is None or payment.milestone_id is not None): raise HTTPException(409,"This invoice is not linked to an onboarding request.")
    if payment.status in ("PAID","CANCELLED"): raise HTTPException(409,"This invoice cannot accept another payment confirmation.")
    if not data.completed: raise HTTPException(422,"Only completed payments can be confirmed.")
    balance=Decimal(payment.amount_due)-Decimal(payment.amount_paid)
    if data.amount is not None and Decimal(data.amount)!=balance: raise HTTPException(422,"Partial payment confirmation is not supported. Confirm the payment only when the full invoice is paid.")
    external_reference=data.external_reference or f"AUTO-{payment.invoice_number}"
    if db.scalar(select(PaymentReceipt.id).where(PaymentReceipt.external_reference==external_reference)): raise HTTPException(409,"This payment confirmation has already been recorded.")
    received_at=data.received_at
    if received_at.tzinfo is None: received_at=received_at.replace(tzinfo=timezone.utc)
    if received_at>datetime.now(timezone.utc): raise HTTPException(422,"Payment received time cannot be in the future.")
    if received_at.date()<payment.invoice_date: raise HTTPException(422,"Payment cannot be received before the invoice date.")
    amount=balance
    receipt=PaymentReceipt(payment_id=payment.id,amount=amount,external_reference=external_reference,received_at=received_at,confirmed_by=user.id,remarks=data.remarks)
    db.add(receipt);payment.amount_paid=Decimal(payment.amount_paid)+amount
    payment.status="PAID" if payment.amount_paid>=payment.amount_due else "PARTIALLY_PAID";payment.updated_at=datetime.now(timezone.utc)
    try: db.flush()
    except IntegrityError:
        db.rollback();raise HTTPException(409,"This payment reference has already been recorded.")
    if payment.status=="PAID" and not card:
        onboarding=db.scalar(select(EnquiryOnboarding).where(EnquiryOnboarding.enquiry_id==payment.enquiry_id).with_for_update())
        if not onboarding or onboarding.status!="PAYMENT_PENDING": raise HTTPException(409,"The onboarding request is no longer awaiting initial payment.")
        onboarding.status="PAYMENT_CONFIRMED";onboarding.payment_confirmed_at=received_at
        audit(db,user.id,"INITIAL_PAYMENT_CONFIRMED","PAYMENT",payment.id,f"invoice={payment.invoice_number}; reference={data.external_reference}; enquiry={payment.enquiry_id}")
        for admin in db.scalars(select(User).where(User.role==Role.ADMIN,User.status==AccountStatus.ACTIVE)):
            notify(db,admin.id,f"Initial payment is confirmed for enquiry {onboarding.enquiry_id}. It is ready for Job Card creation.","/admin/job-cards?focus=payment-confirmed")
    elif payment.status=="PAID":
        if payment.milestone_id:
            milestone=db.get(Milestone,payment.milestone_id)
            audit(db,user.id,"QCI_INVOICE_PAID","MILESTONE",milestone.id if milestone else payment.milestone_id,f"QCI paid D'Mastru invoice {payment.invoice_number}; milestone work gates are based on client payments to QCI.")
        else:
            card.status="IN_PROGRESS"
            audit(db,user.id,"JOB_CARD_ACTIVATED","JOB_CARD",card.id,f"Initial invoice {payment.invoice_number} paid")
            _start_triggered_workflows(db,"PROJECT_ACTIVATED",card,user)
            first_milestone=db.scalar(select(Milestone).where(Milestone.job_card_id==card.id,Milestone.code=="M1"))
            if first_milestone:_start_triggered_workflows(db,"JOB_CARD_CREATED",card,user,first_milestone)
            paid_milestones=db.scalars(select(Milestone).join(Payment,Payment.milestone_id==Milestone.id).where(Milestone.job_card_id==card.id,Payment.status=="PAID"))
            for milestone in paid_milestones:
                milestone.status="READY"
                _start_triggered_workflows(db,"MILESTONE_PAID",card,user,milestone)
                for assignment in db.scalars(select(Assignment).where(Assignment.job_card_id==card.id,Assignment.active.is_(True))):
                    notify(db,assignment.user_id,f"{milestone.code} is now unlocked after project activation.",f"/job-cards/{card.id}")
        for assignment in db.scalars(select(Assignment).where(Assignment.job_card_id==card.id,Assignment.active.is_(True))):
            message=(f"QCI payment received against D'Mastru invoice {payment.invoice_number}." if payment.milestone_id else f"Payment confirmed for invoice {payment.invoice_number}; project activated.")
            notify(db,assignment.user_id,message,f"/job-cards/{card.id}")
    audit(db,user.id,"PAYMENT_CONFIRMED","PAYMENT",payment.id,f"invoice={payment.invoice_number}; received={amount}; reference={data.external_reference}; status={payment.status}")
    db.commit();db.refresh(payment);return _payment_out(payment)

@app.post("/api/payments/{payment_id}/cancel",response_model=PaymentOut)
def cancel_payment_invoice(payment_id:int,data:PaymentCancelIn,db:DB,user:Annotated[User,Depends(require(Role.FINANCE))]):
    payment=db.scalar(select(Payment).where(Payment.id==payment_id).with_for_update())
    if not payment: raise HTTPException(404,"Invoice not found.")
    if payment.job_card_id and not db.scalar(select(JobCard.id).where(JobCard.id==payment.job_card_id)): raise HTTPException(404,"Job Card not found.")
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
def action_transfer_assignees(department_id:int,db:DB,user:Annotated[User,Depends(require(Role.ADMIN,Role.MANAGEMENT))],role_filter:str|None=Query(default=None,alias="role")):
    if not db.scalar(select(Department.id).where(Department.id==department_id,Department.active.is_(True))): raise HTTPException(404,"Active department not found.")
    query=select(User).where(User.department_id==department_id,User.status==AccountStatus.ACTIVE)
    if role_filter: query=query.where(User.role==role_filter)
    return [ActionTransferUserOut(id=row.id,full_name=row.full_name,role=row.role.value) for row in db.scalars(query.order_by(User.full_name))]

@app.get("/api/search",response_model=list[GlobalSearchResult])
def global_search(db:DB,user:Annotated[User,Depends(current_user)],q:str=Query(min_length=2,max_length=100)):
    term=f"%{q.strip()}%";results=[]
    def add(kind:str,id_value:object,title:str,subtitle:str,status:str,href:str):
        results.append(GlobalSearchResult(kind=kind,id=str(id_value),title=title,subtitle=subtitle,status=status,href=href))
    if user.role!=Role.MARKETING:
        tickets=select(Ticket).where(or_(Ticket.number.ilike(term),Ticket.title.ilike(term)))
        if user.role not in (Role.ADMIN,Role.MANAGEMENT):
            visible=[Ticket.created_by==user.id,Ticket.assigned_user_id==user.id]
            if user.department_id: visible.extend((Ticket.created_department_id==user.department_id,Ticket.assigned_department_id==user.department_id))
            tickets=tickets.where(or_(*visible))
        for row in db.scalars(tickets.order_by(Ticket.created_at.desc()).limit(5)):
            add("Ticket",row.id,f"{row.number} · {row.title}",row.ticket_type,row.status,"/marketing")
    cards=select(JobCard,MSME).join(MSME,MSME.id==JobCard.msme_id).where(JobCard.deleted_at.is_(None),or_(JobCard.number.ilike(term),MSME.name.ilike(term),MSME.msme_code.ilike(term),MSME.zed_id.ilike(term)))
    if user.role in (Role.MARKETING,Role.FINANCE): cards=cards.where(JobCard.id<0)
    if user.role==Role.END_USER: cards=cards.join(Assignment,Assignment.job_card_id==JobCard.id).where(Assignment.user_id==user.id,Assignment.active.is_(True))
    elif user.role==Role.UNIT_REPRESENTATIVE: cards=cards.where(JobCard.id<0)
    elif user.role==Role.DIRECTOR: cards=cards.where(JobCard.director_id==user.id,JobCard.status=="PENDING_APPROVAL")
    for card,msme in db.execute(cards.order_by(JobCard.created_at.desc()).limit(5)):
        href="/admin/job-cards" if user.role==Role.ADMIN else ("/management/projects" if user.role==Role.MANAGEMENT else ("/approvals" if user.role==Role.DIRECTOR else "/user/job-cards"))
        add("Job Card",card.id,f"{card.number} · {msme.name}",card.location,card.status,href)
    milestones=select(Milestone,JobCard).join(JobCard,JobCard.id==Milestone.job_card_id).where(JobCard.deleted_at.is_(None),or_(Milestone.code.ilike(term),Milestone.name.ilike(term)))
    if user.role in (Role.MARKETING,Role.FINANCE): milestones=milestones.where(Milestone.id<0)
    if user.role==Role.END_USER: milestones=milestones.join(Assignment,Assignment.job_card_id==JobCard.id).where(Assignment.user_id==user.id,Assignment.active.is_(True))
    elif user.role==Role.UNIT_REPRESENTATIVE: milestones=milestones.where(Milestone.id<0)
    elif user.role==Role.DIRECTOR: milestones=milestones.where(JobCard.director_id==user.id,JobCard.status=="PENDING_APPROVAL")
    for row,card in db.execute(milestones.order_by(Milestone.id.desc()).limit(5)):
        href="/admin/job-cards" if user.role==Role.ADMIN else ("/management/projects" if user.role==Role.MANAGEMENT else ("/approvals" if user.role==Role.DIRECTOR else "/user/job-cards"))
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
def list_tickets(db:DB,user:Annotated[User,Depends(ticket_workspace_user)],status_filter:str|None=Query(default=None,alias="status"),priority:str|None=None,ticket_type:str|None=None,search:str|None=None,assigned_to_me:bool=False,page:int=Query(default=1,ge=1),page_size:int=Query(default=25,ge=1,le=100)):
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
def create_ticket(data:TicketCreate,db:DB,user:Annotated[User,Depends(ticket_workspace_user)]):
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
def get_ticket(ticket_id:UUID,db:DB,user:Annotated[User,Depends(ticket_workspace_user)]):
    row=_get_visible_ticket(db,ticket_id,user)
    return _ticket_out(row,db.get(Enquiry,row.enquiry_id).number if row.enquiry_id else None,db.get(JobCard,row.job_card_id).number if row.job_card_id else None)

@app.patch("/api/tickets/{ticket_id}",response_model=TicketOut)
def update_ticket(ticket_id:UUID,data:TicketUpdate,db:DB,user:Annotated[User,Depends(ticket_workspace_user)]):
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
def ticket_relationships(ticket_id:UUID,db:DB,user:Annotated[User,Depends(ticket_workspace_user)]):
    _get_visible_ticket(db,ticket_id,user)
    return list(db.scalars(select(TicketRelationship).where(TicketRelationship.ticket_id==ticket_id).order_by(TicketRelationship.created_at.desc())))

@app.get("/api/tickets/{ticket_id}/attachments",response_model=list[TicketAttachmentOut])
def list_ticket_attachments(ticket_id:UUID,db:DB,user:Annotated[User,Depends(ticket_workspace_user)]):
    _get_visible_ticket(db,ticket_id,user)
    return list(db.scalars(select(TicketAttachment).where(TicketAttachment.ticket_id==ticket_id).order_by(TicketAttachment.uploaded_at.desc()).limit(100)))

@app.post("/api/tickets/{ticket_id}/attachments",response_model=TicketAttachmentOut,status_code=201)
def upload_ticket_attachment(ticket_id:UUID,data:RecordFileUpload,db:DB,user:Annotated[User,Depends(ticket_workspace_user)]):
    ticket=_get_visible_ticket(db,ticket_id,user)
    key,size=_save_encoded_file(data.file_name,data.mime_type,data.content_base64)
    row=TicketAttachment(ticket_id=ticket_id,file_name=data.file_name,storage_key=key,file_size=size,mime_type=data.mime_type,uploaded_by=user.id)
    db.add(row);db.flush();audit(db,user.id,"TICKET_ATTACHMENT_UPLOADED","TICKET",None,f"{ticket.number}: {data.file_name}",entity_ref=str(ticket.id));db.commit();db.refresh(row);return row

@app.get("/api/ticket-attachments/{attachment_id}/download")
def download_ticket_attachment(attachment_id:int,db:DB,user:Annotated[User,Depends(ticket_workspace_user)]):
    row=db.get(TicketAttachment,attachment_id)
    if not row: raise HTTPException(404,"Attachment not found.")
    _get_visible_ticket(db,row.ticket_id,user)
    return _download_attachment(row.file_name,row.mime_type,row.storage_key)

@app.post("/api/tickets/{ticket_id}/relationships",response_model=TicketRelationshipOut,status_code=201)
def create_ticket_relationship(ticket_id:UUID,data:TicketRelationshipCreate,db:DB,user:Annotated[User,Depends(ticket_workspace_user)]):
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
    if user.role==Role.MARKETING: stmt=stmt.where(or_(Enquiry.created_by==user.id,Enquiry.assigned_marketing_user_id==user.id,Enquiry.source=="USER_REFERRAL"))
    if assigned_to_me: stmt=stmt.where(Enquiry.assigned_marketing_user_id==user.id)
    if status_filter: stmt=stmt.where(Enquiry.status==status_filter)
    if search: stmt=stmt.where(or_(Enquiry.number.ilike(f"%{search.strip()}%"),Enquiry.company_name.ilike(f"%{search.strip()}%"),Enquiry.contact_person.ilike(f"%{search.strip()}%"),Enquiry.email.ilike(f"%{search.strip()}%"),Enquiry.phone.ilike(f"%{search.strip()}%")))
    total=db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows=db.execute(stmt.outerjoin(User,User.id==Enquiry.created_by).add_columns(User.full_name).order_by(Enquiry.updated_at.desc()).offset((page-1)*page_size).limit(page_size)).all()
    items=[]
    for enquiry,referrer_name in rows:
        item=EnquiryOut.model_validate(enquiry).model_dump(mode="json")
        item["referrer_name"]=referrer_name if enquiry.source=="USER_REFERRAL" else None
        items.append(item)
    return {"items":items,"total":total,"page":page,"page_size":page_size}

@app.post("/api/referrals",response_model=EnquiryOut,status_code=201)
def create_user_referral(data:EnquiryCreate,db:DB,user:Annotated[User,Depends(current_user)]):
    """Create an enquiry referral from any signed-in workspace and alert Marketing."""
    values=data.model_dump()
    duplicate=_find_duplicate_enquiry(db,values.get("company_name"),values.get("email"),values.get("phone"))
    if duplicate: raise HTTPException(409,"An enquiry for this company, email, or phone already exists. Please contact Marketing to update the existing enquiry.")
    next_follow_up_type=values.pop("next_follow_up_type",None)
    if values.get("follow_up_date") and not next_follow_up_type: raise HTTPException(422,"Select the next follow-up method when setting its date.")
    values.update(source="USER_REFERRAL",referral_consultant_id=None,status="NEW",assigned_marketing_user_id=None)
    values["zed_pass_encrypted"]=_encrypt_sensitive(values.pop("zed_pass",None))
    if values.get("msme_id") and not db.get(MSME,values["msme_id"]): raise HTTPException(422,"MSME does not exist.")
    marketers=list(db.scalars(select(User).where(User.role==Role.MARKETING,User.status==AccountStatus.ACTIVE).order_by(User.id)))
    if marketers: values["assigned_marketing_user_id"]=marketers[0].id
    row=Enquiry(**values,number=next_business_number(db,"enquiry"),created_by=user.id);db.add(row);db.flush()
    if row.follow_up_date:
        business_timezone=ZoneInfo(os.getenv("APP_TIMEZONE","Asia/Kolkata"))
        planned_at=datetime.combine(row.follow_up_date,TimeOfDay(9,0),tzinfo=business_timezone)
        db.add(EnquiryFollowUp(enquiry_id=row.id,follow_up_date=datetime.now(timezone.utc),follow_up_type=next_follow_up_type,person_contacted=row.contact_person,notes="Initial follow-up scheduled at enquiry capture.",next_follow_up_date=planned_at,next_follow_up_type=next_follow_up_type,responsible_user_id=row.assigned_marketing_user_id or user.id,status="OPEN"))
    audit(db,user.id,"ENQUIRY_REFERRED","ENQUIRY",None,f"{row.number} referred by {user.full_name}",entity_ref=str(row.id))
    for marketer in marketers:
        notify(db,marketer.id,f"New client referral {row.number} from {user.full_name}.",f"/marketing?view=enquiries&search={row.number}")
    db.commit();db.refresh(row);return row

@app.get("/api/my-referrals")
def list_my_referrals(db:DB,user:Annotated[User,Depends(current_user)]):
    rows=list(db.scalars(select(Enquiry).where(Enquiry.created_by==user.id,Enquiry.source=="USER_REFERRAL").order_by(Enquiry.created_at.desc())))
    result=[]
    for row in rows:
        onboarding=db.scalar(select(EnquiryOnboarding).where(EnquiryOnboarding.enquiry_id==row.id))
        successful=bool(onboarding and onboarding.status in ("ONBOARDED","PAYMENT_CONFIRMED","JOB_CARD_CREATED"))
        if successful: referral_status="SUCCESSFUL"
        elif row.management_approval_status=="PENDING": referral_status="AWAITING_MANAGEMENT_APPROVAL"
        elif row.client_confirmation_status in ("CHANGES_REQUESTED","DECLINED"): referral_status="ON_HOLD"
        elif row.client_confirmation_status=="CONFIRMED": referral_status="CLIENT_CONFIRMED"
        elif row.management_approval_status=="APPROVED": referral_status="AWAITING_CLIENT_DISCUSSION"
        else: referral_status=row.status
        result.append({"id":str(row.id),"number":row.number,"company_name":row.company_name,"contact_person":row.contact_person,"enquiry_date":row.enquiry_date,"status":referral_status,"created_at":row.created_at})
    return result

def _normalized_phone_expression():
    expression=func.trim(Enquiry.phone)
    for character in ("+","-","(",")"," ",".","/","\t","\r","\n"):
        expression=func.replace(expression,character,"")
    return expression

def _find_duplicate_enquiry(db:Session,company_name:str|None,email:str|None,phone:str|None,exclude_id:UUID|None=None):
    conditions=[]
    if company_name and len(company_name.strip())>1: conditions.append(func.lower(func.trim(Enquiry.company_name))==company_name.strip().lower())
    if email and email.strip(): conditions.append(func.lower(func.trim(Enquiry.email))==email.strip().lower())
    phone_digits="".join(character for character in (phone or "") if character.isdigit())
    if phone_digits: conditions.append(_normalized_phone_expression()==phone_digits)
    if not conditions: return None
    stmt=select(Enquiry).where(or_(*conditions))
    if exclude_id is not None: stmt=stmt.where(Enquiry.id!=exclude_id)
    return db.scalar(stmt.order_by(Enquiry.created_at.asc()).limit(1))

@app.get("/api/enquiries/matches")
def enquiry_matches(db:DB,user:Annotated[User,Depends(require(Role.ADMIN,Role.MARKETING,Role.MANAGEMENT))],company_name:str|None=None,email:str|None=None,phone:str|None=None):
    if not any((company_name and len(company_name.strip())>1,email and email.strip(),phone and phone.strip())):
        raise HTTPException(422,"Provide company name, email, or phone to search for possible duplicates.")
    conditions=[]
    if company_name and len(company_name.strip())>1: conditions.append(func.lower(func.trim(Enquiry.company_name))==company_name.strip().lower())
    if email and email.strip(): conditions.append(func.lower(func.trim(Enquiry.email))==email.strip().lower())
    phone_digits="".join(character for character in (phone or "") if character.isdigit())
    if phone_digits: conditions.append(_normalized_phone_expression()==phone_digits)
    stmt=select(Enquiry).where(or_(*conditions))
    if user.role==Role.MARKETING: stmt=stmt.where(or_(Enquiry.created_by==user.id,Enquiry.assigned_marketing_user_id==user.id,Enquiry.source=="USER_REFERRAL"))
    return list(db.scalars(stmt.order_by(Enquiry.created_at.desc()).limit(20)))

@app.get("/api/marketing/assignees")
def marketing_assignees(db:DB,user:Annotated[User,Depends(require(Role.ADMIN,Role.MARKETING))]):
    return [{"id":row.id,"full_name":row.full_name} for row in db.scalars(select(User).where(User.role==Role.MARKETING,User.status==AccountStatus.ACTIVE).order_by(User.full_name))]

@app.get("/api/marketing/consultants")
def marketing_consultants(db:DB,user:Annotated[User,Depends(require(Role.ADMIN,Role.MARKETING))]):
    return [{"id":row.id,"full_name":row.full_name,"consultant_id":row.consultant_id} for row in db.scalars(select(User).where(User.role==Role.END_USER,User.status==AccountStatus.ACTIVE).order_by(User.full_name))]

@app.post("/api/enquiries",response_model=EnquiryOut,status_code=201)
def create_enquiry(data:EnquiryCreate,db:DB,user:Annotated[User,Depends(require(Role.ADMIN,Role.MARKETING))]):
    values=data.model_dump()
    if user.role==Role.MARKETING and values.get("status")=="CONVERTED": raise HTTPException(422,"Create the enquiry first, then convert it to trigger the Admin handoff.")
    duplicate=_find_duplicate_enquiry(db,values.get("company_name"),values.get("email"),values.get("phone"))
    if duplicate: raise HTTPException(409,f"Possible duplicate: enquiry {duplicate.number} already matches this company, email, or phone. Open the existing enquiry instead.")
    next_follow_up_type=values.pop("next_follow_up_type",None)
    values["zed_pass_encrypted"]=_encrypt_sensitive(values.pop("zed_pass",None))
    _validate_referral_consultant(db,values.get("referral_consultant_id"))
    if values.get("msme_id") and not db.get(MSME,values["msme_id"]): raise HTTPException(422,"MSME does not exist.")
    marketer_id=values.get("assigned_marketing_user_id") or (user.id if user.role==Role.MARKETING else None)
    if marketer_id:
        marketer=db.get(User,marketer_id)
        if not marketer or marketer.role!=Role.MARKETING or marketer.status!=AccountStatus.ACTIVE: raise HTTPException(422,"Assigned marketer must be an active Marketing user.")
    values["assigned_marketing_user_id"]=marketer_id
    row=Enquiry(**values,number=next_business_number(db,"enquiry"),created_by=user.id);db.add(row)
    db.flush()
    if row.follow_up_date:
        if not next_follow_up_type: raise HTTPException(422,"Select the next follow-up method when setting its date.")
        business_timezone=ZoneInfo(os.getenv("APP_TIMEZONE","Asia/Kolkata"))
        planned_at=datetime.combine(row.follow_up_date,TimeOfDay(9,0),tzinfo=business_timezone)
        db.add(EnquiryFollowUp(enquiry_id=row.id,follow_up_date=datetime.now(timezone.utc),follow_up_type=next_follow_up_type,person_contacted=row.contact_person,notes="Initial follow-up scheduled at enquiry capture.",next_follow_up_date=planned_at,next_follow_up_type=next_follow_up_type,responsible_user_id=marketer_id or user.id,status="OPEN"))
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
    if user.role==Role.MARKETING and row.status=="CONVERTED": raise HTTPException(409,"This enquiry has been handed to Admin and is no longer editable by Marketing.")
    changes=data.model_dump(exclude_unset=True)
    zed_pass=changes.pop("zed_pass",None)
    if zed_pass: changes["zed_pass_encrypted"]=_encrypt_sensitive(zed_pass)
    if "referral_consultant_id" in changes: _validate_referral_consultant(db,changes["referral_consultant_id"])
    effective_source=changes.get("source",row.source)
    effective_referral=changes.get("referral_consultant_id",row.referral_consultant_id)
    if ("source" in changes or "referral_consultant_id" in changes) and ((effective_source=="CONSULTANT_REFERRAL") != (effective_referral is not None)):
        raise HTTPException(422,"Select an active consultant when Lead source is Consultant referral.")
    effective_services=changes.get("enquiry_for",row.enquiry_for or [])
    has_zed_service=any(option.startswith("ZED ") for option in effective_services)
    if (zed_pass or changes.get("zed_id")) and not has_zed_service:
        raise HTTPException(422,"ZED ID and pass are only available for ZED enquiry services.")
    if "enquiry_for" in changes and not has_zed_service:
        changes["zed_id"]=None;changes["zed_pass_encrypted"]=None
    if "assigned_marketing_user_id" in changes and changes["assigned_marketing_user_id"] is not None:
        assignee=db.get(User,changes["assigned_marketing_user_id"])
        if not assignee or assignee.role!=Role.MARKETING or assignee.status!=AccountStatus.ACTIVE: raise HTTPException(422,"Assigned marketer must be an active Marketing user.")
    if "deadline_date" in changes and changes["deadline_date"] is None: raise HTTPException(422,"A response deadline date is required.")
    if changes.get("msme_id") and not db.get(MSME,changes["msme_id"]): raise HTTPException(422,"MSME does not exist.")
    if any(key in changes and changes[key]!=getattr(row,key) for key in ("company_name","email","phone")):
        duplicate=_find_duplicate_enquiry(db,changes.get("company_name",row.company_name),changes.get("email",row.email),changes.get("phone",row.phone),exclude_id=row.id)
        if duplicate: raise HTTPException(409,f"Possible duplicate: enquiry {duplicate.number} already matches this company, email, or phone.")
    previous_status=row.status
    if changes.get("status")=="CONVERTED" and previous_status!="QUALIFIED":
        raise HTTPException(409,"Only a qualified enquiry can be converted and handed to Admin.")
    before={key:("[redacted]" if key=="zed_pass_encrypted" else getattr(row,key)) for key in changes}
    audit_changes={key:("[updated]" if key=="zed_pass_encrypted" else value) for key,value in changes.items()}
    for key,value in changes.items(): setattr(row,key,value)
    if previous_status!="CONVERTED" and row.status=="CONVERTED":
        row.management_approval_status="PENDING";row.management_approval_comment=None;row.management_approved_by=None;row.management_approved_at=None
    row.updated_at=datetime.now(timezone.utc);audit(db,user.id,"ENQUIRY_UPDATED","ENQUIRY",None,f"{row.number}: {before} -> {audit_changes}",entity_ref=str(row.id))
    if previous_status!="CONVERTED" and row.status=="CONVERTED":
        audit(db,user.id,"ENQUIRY_SUBMITTED_FOR_MANAGEMENT_APPROVAL","ENQUIRY",None,f"{row.number} is awaiting Management approval before onboarding.",entity_ref=str(row.id))
        for manager in db.scalars(select(User).where(User.role==Role.MANAGEMENT,User.status==AccountStatus.ACTIVE)):
            notify(db,manager.id,f"Enquiry {row.number} is awaiting your approval before onboarding.","/management/reviews")
    if row.status in ("CONVERTED","LOST","CANCELLED"): _resolve_enquiry_sla_breaches(db,row,user)
    if row.assigned_marketing_user_id and row.assigned_marketing_user_id!=user.id: notify(db,row.assigned_marketing_user_id,f"Enquiry {row.number} was updated.",f"/marketing/enquiries/{row.id}")
    db.commit();db.refresh(row);return row

@app.get("/api/admin/enquiries/onboarding")
def admin_onboarding_enquiries(db:DB,user:Annotated[User,Depends(require(Role.ADMIN))],page:int=Query(default=1,ge=1),page_size:int=Query(default=100,ge=1,le=200)):
    linked_card=select(JobCard.id).where(JobCard.source_enquiry_id==Enquiry.id).exists()
    has_onboarding=select(EnquiryOnboarding.id).where(EnquiryOnboarding.enquiry_id==Enquiry.id).exists()
    stmt=select(Enquiry).where(Enquiry.status=="CONVERTED",Enquiry.management_approval_status=="APPROVED",~linked_card,~has_onboarding)
    total=int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows=list(db.scalars(stmt.order_by(Enquiry.updated_at.desc()).offset((page-1)*page_size).limit(page_size)))
    items=[]
    for row in rows:
        meeting_at=row.client_meeting_at
        if meeting_at and meeting_at.tzinfo is None: meeting_at=meeting_at.replace(tzinfo=ZoneInfo(os.getenv("APP_TIMEZONE","Asia/Kolkata")))
        evidence=[]
        for doc in db.scalars(select(EnquiryDocument).where(EnquiryDocument.enquiry_id==row.id,EnquiryDocument.document_type.in_(("CLIENT_BRIEFING_EVIDENCE","CLIENT_BRIEFING_GOOGLE_DRAFT"))).order_by(EnquiryDocument.uploaded_at.desc())):
            evidence_at=doc.google_synced_at if doc.google_file_id else doc.uploaded_at
            if evidence_at and evidence_at.tzinfo is None: evidence_at=evidence_at.replace(tzinfo=timezone.utc)
            evidence.append({"id":doc.id,"file_name":doc.file_name,"file_size":doc.file_size,"uploaded_at":doc.uploaded_at,"valid_for_current_meeting":bool(meeting_at and evidence_at and evidence_at>=meeting_at),"google_file_url":doc.google_file_url,"google_synced_at":doc.google_synced_at})
        followups=list(db.scalars(select(EnquiryFollowUp).where(EnquiryFollowUp.enquiry_id==row.id,EnquiryFollowUp.follow_up_type=="CLIENT_DISCUSSION").order_by(EnquiryFollowUp.created_at.desc()).limit(20)))
        items.append({"id":str(row.id),"number":row.number,"company_name":row.company_name,"status":row.status,"management_approval_status":row.management_approval_status,"contact_person":row.contact_person,"email":row.email,"phone":row.phone,"industry":row.industry,"location":row.location,"requirement":row.requirement,"expected_scope":row.expected_scope,"enquiry_for":row.enquiry_for,"package_code":row.package_code,"zed_id":row.zed_id,"zed_pass_set":row.zed_pass_set,"feedback":row.feedback,"deadline_date":row.deadline_date,"source":row.source,"referral_consultant_id":row.referral_consultant_id,"client_meeting_type":row.client_meeting_type,"client_meeting_at":row.client_meeting_at,"client_meeting_completed_at":row.client_meeting_completed_at,"client_briefing_service_name":row.client_briefing_service_name,"client_briefing_package_code":row.client_briefing_package_code,"client_briefing_planned_mandays":row.client_briefing_planned_mandays,"client_briefing_timeline_start":row.client_briefing_timeline_start,"client_briefing_timeline_end":row.client_briefing_timeline_end,"client_briefing_scope":row.client_briefing_scope,"client_confirmation_status":row.client_confirmation_status,"client_confirmation_note":row.client_confirmation_note,"client_confirmation_recorded_at":row.client_confirmation_recorded_at,"client_briefing_evidence":evidence,"client_briefing_followups":[{"id":f.id,"follow_up_date":f.follow_up_date,"follow_up_type":f.follow_up_type,"notes":f.notes,"next_follow_up_date":f.next_follow_up_date,"next_follow_up_type":f.next_follow_up_type,"status":f.status} for f in followups],"updated_at":row.updated_at})
    return {"items":items,"total":total,"page":page,"page_size":page_size}

def _has_current_client_briefing_evidence(db:Session,enquiry:Enquiry)->bool:
    if not enquiry.client_meeting_at: return False
    meeting_at=enquiry.client_meeting_at
    if meeting_at.tzinfo is None: meeting_at=meeting_at.replace(tzinfo=ZoneInfo(os.getenv("APP_TIMEZONE","Asia/Kolkata")))
    for doc in db.scalars(select(EnquiryDocument).where(EnquiryDocument.enquiry_id==enquiry.id,EnquiryDocument.document_type.in_(("CLIENT_BRIEFING_EVIDENCE","CLIENT_BRIEFING_GOOGLE_DRAFT")))):
        evidence_at=doc.google_synced_at if doc.google_file_id else doc.uploaded_at
        if evidence_at and evidence_at.tzinfo is None: evidence_at=evidence_at.replace(tzinfo=timezone.utc)
        if evidence_at and evidence_at>=meeting_at: return True
    return False

@app.get("/api/management/enquiry-approvals")
def management_enquiry_approval_queue(db:DB,user:Annotated[User,Depends(require(Role.MANAGEMENT))]):
    rows=list(db.scalars(select(Enquiry).where(Enquiry.status=="CONVERTED",Enquiry.management_approval_status=="PENDING").order_by(Enquiry.updated_at.asc())))
    return [{"id":str(row.id),"number":row.number,"company_name":row.company_name,"contact_person":row.contact_person,"email":row.email,"phone":row.phone,"requirement":row.requirement,"enquiry_for":row.enquiry_for,"source":row.source,"referrer_name":(db.get(User,row.referral_consultant_id) if row.source=="CONSULTANT_REFERRAL" and row.referral_consultant_id else db.get(User,row.created_by)).full_name,"expected_scope":row.expected_scope,"created_at":row.created_at} for row in rows]

@app.get("/api/management/enquiry-approval-history")
def management_enquiry_approval_history(db:DB,user:Annotated[User,Depends(require(Role.MANAGEMENT))]):
    rows=db.execute(select(Enquiry,User.full_name).outerjoin(User,User.id==Enquiry.management_approved_by).where(Enquiry.management_approval_status.in_(("APPROVED","REJECTED"))).order_by(Enquiry.management_approved_at.desc(),Enquiry.updated_at.desc()).limit(100)).all()
    return [{"id":str(row.id),"number":row.number,"company_name":row.company_name,"decision":row.management_approval_status,"comment":row.management_approval_comment,"decided_by":approver_name,"decided_at":row.management_approved_at} for row,approver_name in rows]

@app.post("/api/management/enquiries/{enquiry_id}/approval")
def decide_enquiry_management_approval(enquiry_id:UUID,data:EnquiryApprovalDecision,db:DB,user:Annotated[User,Depends(require(Role.MANAGEMENT))]):
    row=db.get(Enquiry,enquiry_id)
    if not row or row.status!="CONVERTED" or row.management_approval_status!="PENDING": raise HTTPException(404,"Enquiry is not awaiting Management approval.")
    comment=(data.comment or "").strip()
    if data.decision=="REJECTED" and len(comment)<3: raise HTTPException(422,"A reason is required when rejecting an enquiry.")
    row.management_approval_status=data.decision
    row.management_approval_comment=comment or None
    row.management_approved_by=user.id
    row.management_approved_at=datetime.now(timezone.utc)
    row.updated_at=datetime.now(timezone.utc)
    if data.decision=="REJECTED": row.status="QUALIFIED"
    audit(db,user.id,f"ENQUIRY_MANAGEMENT_{data.decision}","ENQUIRY",None,f"{row.number}: {comment or 'Approved'}",entity_ref=str(row.id))
    if data.decision=="APPROVED":
        for admin in db.scalars(select(User).where(User.role==Role.ADMIN,User.status==AccountStatus.ACTIVE)):
            notify(db,admin.id,f"Management approved enquiry {row.number}. Admin can now arrange the client discussion before onboarding.","/admin/job-cards?focus=onboarding")
    recipients={row.created_by}
    if row.assigned_marketing_user_id: recipients.add(row.assigned_marketing_user_id)
    for recipient in recipients:
        notify(db,recipient,f"Management {data.decision.lower()} enquiry {row.number}.{f' Reason: {comment}' if comment else ''}",f"/marketing?view=enquiries&search={row.number}")
    db.commit();db.refresh(row)
    return {"id":str(row.id),"status":row.status,"management_approval_status":row.management_approval_status,"management_approval_comment":row.management_approval_comment,"management_approved_at":row.management_approved_at}

def _enquiry_onboarding_out(db:Session,item:EnquiryOnboarding):
    enquiry=db.get(Enquiry,item.enquiry_id);msme=db.get(MSME,item.msme_id)
    payment=db.scalar(select(Payment).where(Payment.enquiry_id==item.enquiry_id,Payment.job_card_id.is_(None),Payment.milestone_id.is_(None)).order_by(Payment.id.desc()))
    return EnquiryOnboardingOut(id=item.id,enquiry_id=item.enquiry_id,enquiry_number=enquiry.number,company_name=enquiry.company_name,contact_person=enquiry.contact_person,email=enquiry.email,phone=enquiry.phone,industry=enquiry.industry,enquiry_for=enquiry.enquiry_for,zed_id=enquiry.zed_id,msme_id=msme.id,msme_code=msme.msme_code,service_name=item.service_name,project_name=item.project_name,scope=item.scope,location=item.location,package_code=item.package_code,planned_mandays=item.planned_mandays,project_timeline_start=item.project_timeline_start,project_timeline_end=item.project_timeline_end,commercial_amount=item.commercial_amount,initial_payment_amount=item.initial_payment_amount,status=item.status,management_approval_status=enquiry.management_approval_status,payment=PaymentOut.model_validate(payment) if payment else None,zed_bronze_certification_status=item.zed_bronze_certification_status,zed_bronze_certificate_number=item.zed_bronze_certificate_number,zed_bronze_report_document_id=item.zed_bronze_report_document_id,zed_bronze_confirmed_at=item.zed_bronze_confirmed_at,zed_silver_gold_interest=item.zed_silver_gold_interest,zed_marketing_plan_status=item.zed_marketing_plan_status,zed_marketing_plan_services=item.zed_marketing_plan_services,zed_marketing_plan_notes=item.zed_marketing_plan_notes,zed_marketing_plan_due_date=item.zed_marketing_plan_due_date)

@app.put("/api/admin/enquiries/{enquiry_id}/client-briefing")
def schedule_enquiry_client_briefing(enquiry_id:UUID,data:EnquiryClientBriefingSchedule,db:DB,admin:Annotated[User,Depends(require(Role.ADMIN))]):
    enquiry=db.get(Enquiry,enquiry_id)
    if not enquiry or enquiry.status!="CONVERTED": raise HTTPException(404,"Approved converted enquiry not found.")
    if enquiry.management_approval_status!="APPROVED": raise HTTPException(409,"Management approval is required before the client briefing.")
    if db.scalar(select(EnquiryOnboarding.id).where(EnquiryOnboarding.enquiry_id==enquiry.id)): raise HTTPException(409,"Onboarding has already started for this enquiry.")
    selected_services=[part.strip() for part in (enquiry.enquiry_for or []) if part.strip()]
    if not selected_services: raise HTTPException(409,"The enquiry must have at least one requested service before the discussion can be scheduled.")
    package=db.scalar(select(ServicePackage).where(ServicePackage.code==enquiry.package_code,ServicePackage.active.is_(True))) if enquiry.package_code else None
    if package and not _package_supports_service(package,selected_services): raise HTTPException(409,"The enquiry's selected package does not match its requested services. Update the enquiry package before continuing.")
    scheduled=data.scheduled_at
    if scheduled.tzinfo is None: scheduled=scheduled.replace(tzinfo=ZoneInfo(os.getenv("APP_TIMEZONE","Asia/Kolkata")))
    enquiry.client_meeting_type=data.meeting_type
    enquiry.client_meeting_at=scheduled
    enquiry.client_meeting_completed_at=None
    enquiry.client_briefing_service_name=", ".join(selected_services)
    enquiry.client_briefing_package_code=package.code if package else None
    enquiry.client_briefing_planned_mandays=Decimal(str(package.planned_mandays)) if package else None
    enquiry.client_briefing_timeline_start=None
    enquiry.client_briefing_timeline_end=None
    enquiry.client_briefing_scope=(enquiry.expected_scope or enquiry.requirement).strip()
    enquiry.client_confirmation_status="SCHEDULED"
    enquiry.client_confirmation_note=None
    enquiry.client_confirmation_recorded_at=None
    enquiry.client_confirmation_recorded_by=None
    enquiry.updated_at=datetime.now(timezone.utc)
    audit(db,admin.id,"ENQUIRY_CLIENT_BRIEFING_SCHEDULED","ENQUIRY",None,f"{enquiry.number}: client discussion at {scheduled.isoformat()}; {enquiry.client_briefing_service_name}; package {enquiry.client_briefing_package_code or 'not selected'}",entity_ref=str(enquiry.id))
    db.commit();db.refresh(enquiry)
    return {"id":str(enquiry.id),"client_meeting_type":enquiry.client_meeting_type,"client_meeting_at":enquiry.client_meeting_at,"client_meeting_completed_at":enquiry.client_meeting_completed_at,"client_briefing_service_name":enquiry.client_briefing_service_name,"client_briefing_package_code":enquiry.client_briefing_package_code,"client_briefing_planned_mandays":enquiry.client_briefing_planned_mandays,"client_briefing_timeline_start":enquiry.client_briefing_timeline_start,"client_briefing_timeline_end":enquiry.client_briefing_timeline_end,"client_briefing_scope":enquiry.client_briefing_scope,"client_confirmation_status":enquiry.client_confirmation_status,"client_confirmation_note":enquiry.client_confirmation_note,"client_confirmation_recorded_at":enquiry.client_confirmation_recorded_at}

@app.post("/api/admin/enquiries/{enquiry_id}/client-confirmation")
def record_enquiry_client_confirmation(enquiry_id:UUID,data:EnquiryClientConfirmation,db:DB,admin:Annotated[User,Depends(require(Role.ADMIN))]):
    enquiry=db.get(Enquiry,enquiry_id)
    if not enquiry or enquiry.status!="CONVERTED": raise HTTPException(404,"Approved converted enquiry not found.")
    if enquiry.management_approval_status!="APPROVED": raise HTTPException(409,"Management approval is required before the client discussion.")
    if enquiry.client_confirmation_status!="SCHEDULED" or not enquiry.client_meeting_at or not enquiry.client_briefing_service_name: raise HTTPException(409,"Schedule and document the client briefing before recording the client decision.")
    meeting_at=enquiry.client_meeting_at
    if meeting_at.tzinfo is None: meeting_at=meeting_at.replace(tzinfo=ZoneInfo(os.getenv("APP_TIMEZONE","Asia/Kolkata")))
    if meeting_at>datetime.now(timezone.utc): raise HTTPException(409,"Record the client decision after the scheduled call or meeting has taken place.")
    if not _has_current_client_briefing_evidence(db,enquiry): raise HTTPException(409,"Upload proof that this client discussion took place before recording the client decision.")
    enquiry.client_meeting_completed_at=datetime.now(timezone.utc)
    enquiry.client_confirmation_status=data.decision
    enquiry.client_confirmation_note=data.note.strip()
    enquiry.client_confirmation_recorded_at=datetime.now(timezone.utc)
    enquiry.client_confirmation_recorded_by=admin.id
    enquiry.updated_at=datetime.now(timezone.utc)
    audit(db,admin.id,f"ENQUIRY_CLIENT_{data.decision}","ENQUIRY",None,f"{enquiry.number}: {data.note.strip()}",entity_ref=str(enquiry.id))
    if data.decision!="CONFIRMED":
        recipients={enquiry.created_by}
        if enquiry.assigned_marketing_user_id: recipients.add(enquiry.assigned_marketing_user_id)
        for recipient in recipients:
            notify(db,recipient,f"Client briefing for enquiry {enquiry.number}: {data.decision.replace('_',' ').lower()}. Admin has recorded: {data.note.strip()}",f"/marketing?view=enquiries&search={enquiry.number}")
    db.commit();db.refresh(enquiry)
    return {"id":str(enquiry.id),"client_meeting_completed_at":enquiry.client_meeting_completed_at,"client_confirmation_status":enquiry.client_confirmation_status,"client_confirmation_note":enquiry.client_confirmation_note,"client_confirmation_recorded_at":enquiry.client_confirmation_recorded_at}

@app.post("/api/admin/enquiries/{enquiry_id}/onboarding",response_model=EnquiryOnboardingOut,response_model_exclude={"commercial_amount","initial_payment_amount","payment"},status_code=201)
def create_enquiry_onboarding(enquiry_id:UUID,data:EnquiryOnboardingCreate,db:DB,admin:Annotated[User,Depends(require(Role.ADMIN))]):
    enquiry=db.get(Enquiry,enquiry_id)
    if not enquiry or enquiry.status!="CONVERTED": raise HTTPException(404,"Converted enquiry not found.")
    if enquiry.management_approval_status!="APPROVED": raise HTTPException(409,"Management approval is required before onboarding can begin.")
    if enquiry.client_confirmation_status!="CONFIRMED": raise HTTPException(409,"Record the completed client briefing and confirmation before onboarding can begin.")
    if not _has_current_client_briefing_evidence(db,enquiry): raise HTTPException(409,"Upload proof of the completed client discussion before onboarding can begin.")
    if db.scalar(select(EnquiryOnboarding.id).where(EnquiryOnboarding.enquiry_id==enquiry.id)): raise HTTPException(409,"Onboarding has already been started for this enquiry.")
    selected_services=[part.strip() for part in data.service_name.split(",") if part.strip()]
    agreed_services=[part.strip() for part in (enquiry.client_briefing_service_name or "").split(",") if part.strip()]
    if selected_services!=agreed_services: raise HTTPException(422,"Onboarding services must match the client-confirmed briefing.")
    if data.package_code!=enquiry.client_briefing_package_code: raise HTTPException(422,"Onboarding package must match the client-confirmed briefing.")
    if data.scope.strip()!=str(enquiry.client_briefing_scope or "").strip(): raise HTTPException(422,"Onboarding scope must match the client-confirmed briefing.")
    if not selected_services or set(selected_services)-set(enquiry.enquiry_for or []): raise HTTPException(422,"Choose service(s) that were requested on this enquiry.")
    package=db.scalar(select(ServicePackage).where(ServicePackage.code==data.package_code,ServicePackage.active.is_(True))) if data.package_code else None
    if data.package_code and not package: raise HTTPException(422,"Selected service package is not active or does not exist.")
    if package and not _package_supports_service(package,selected_services): raise HTTPException(422,"Select a package configured for one of the services on this enquiry.")
    commercial_amount=Decimal(str(package.commercial_amount)) if package else None
    msme=db.get(MSME,data.msme_id) if data.msme_id else db.scalar(select(MSME).where(func.lower(MSME.name)==enquiry.company_name.lower(),MSME.deleted_at.is_(None)))
    if data.msme_id and (not msme or msme.deleted_at or msme.status!="ACTIVE"): raise HTTPException(422,"Selected MSME is not active.")
    if msme and msme.name.strip().casefold()!=enquiry.company_name.strip().casefold(): raise HTTPException(422,"The selected MSME company name must match the enquiry.")
    if not msme:
        level=next((service.removeprefix("ZED ") for service in selected_services if service.startswith("ZED ")),"Bronze")
        msme=MSME(name=enquiry.company_name.strip(),msme_code=next_business_number(db,"msme"),zed_id=enquiry.zed_id,certificate_level=level,address=data.address,city=data.city or enquiry.location,state=data.state,contact_person=enquiry.contact_person,email=enquiry.email,phone=enquiry.phone,industry=enquiry.industry,unit_name=data.unit_name,status="ACTIVE")
        db.add(msme);db.flush()
    elif not msme.zed_id and enquiry.zed_id:
        msme.zed_id=enquiry.zed_id
    project_number=next_business_number(db,"project")
    zed_bronze_required="ZED Bronze" in selected_services
    onboarding=EnquiryOnboarding(enquiry_id=enquiry.id,msme_id=msme.id,service_name=", ".join(selected_services),project_name=project_number,scope=data.scope,location=data.location,package_code=package.code if package else None,planned_mandays=enquiry.client_briefing_planned_mandays,project_timeline_start=enquiry.client_briefing_timeline_start,project_timeline_end=enquiry.client_briefing_timeline_end,commercial_amount=commercial_amount,initial_payment_amount=Decimal("0.00"),status="ONBOARDED",created_by=admin.id,zed_bronze_certification_status="PENDING" if zed_bronze_required else "NOT_REQUIRED")
    db.add(onboarding);enquiry.msme_id=msme.id;enquiry.package_code=package.code if package else None;enquiry.expected_scope=data.scope;enquiry.expected_commercial_amount=commercial_amount;enquiry.updated_at=datetime.now(timezone.utc)
    try: db.flush()
    except IntegrityError:
        db.rollback();raise HTTPException(409,"Onboarding has already been started for this enquiry.")
    audit(db,admin.id,"ONBOARDING_COMPLETED","ENQUIRY",None,f"{enquiry.number}; {onboarding.service_name}; MSME {msme.msme_code}",entity_ref=str(enquiry.id))
    db.commit();db.refresh(onboarding);return _enquiry_onboarding_out(db,onboarding)

@app.get("/api/admin/onboardings/ready-for-job-card")
def admin_ready_onboardings(db:DB,admin:Annotated[User,Depends(require(Role.ADMIN))],page:int=Query(default=1,ge=1),page_size:int=Query(default=100,ge=1,le=200)):
    stmt=select(EnquiryOnboarding).where(EnquiryOnboarding.status.in_(("ONBOARDED","PAYMENT_CONFIRMED")),EnquiryOnboarding.job_card_id.is_(None),EnquiryOnboarding.zed_bronze_certification_status!="PENDING",EnquiryOnboarding.service_name!="ZED Bronze").order_by(EnquiryOnboarding.updated_at.desc())
    total=int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    items=list(db.scalars(stmt.offset((page-1)*page_size).limit(page_size)))
    return {"items":[_enquiry_onboarding_out(db,item).model_dump(mode="json",exclude={"payment","commercial_amount","initial_payment_amount"}) for item in items],"total":total,"page":page,"page_size":page_size}

@app.get("/api/admin/onboardings/zed-bronze-pending")
def admin_zed_bronze_pending(db:DB,admin:Annotated[User,Depends(require(Role.ADMIN))]):
    rows=list(db.scalars(select(EnquiryOnboarding).where(EnquiryOnboarding.zed_bronze_certification_status=="PENDING",EnquiryOnboarding.job_card_id.is_(None)).order_by(EnquiryOnboarding.updated_at.asc())))
    items=[]
    for item in rows:
        documents=list(db.scalars(select(EnquiryDocument).where(EnquiryDocument.enquiry_id==item.enquiry_id,EnquiryDocument.document_type=="ZED_BRONZE_CERTIFICATE_REPORT").order_by(EnquiryDocument.uploaded_at.desc())))
        items.append({**_enquiry_onboarding_out(db,item).model_dump(mode="json",exclude={"payment","commercial_amount","initial_payment_amount"}),"report_documents":[{"id":doc.id,"file_name":doc.file_name,"file_size":doc.file_size,"uploaded_at":doc.uploaded_at} for doc in documents]})
    return {"items":items,"total":len(items)}

@app.post("/api/admin/onboardings/{onboarding_id}/zed-bronze-completion")
def complete_zed_bronze_certification(onboarding_id:int,data:ZedBronzeCertificationCompletion,db:DB,admin:Annotated[User,Depends(require(Role.ADMIN))]):
    item=db.get(EnquiryOnboarding,onboarding_id)
    if not item or item.zed_bronze_certification_status!="PENDING": raise HTTPException(404,"ZED Bronze certification is not awaiting confirmation.")
    services=[part.strip() for part in item.service_name.split(",") if part.strip()]
    if "ZED Bronze" not in services: raise HTTPException(409,"Bronze certification steps apply only to ZED Bronze enquiries.")
    report=db.get(EnquiryDocument,data.report_document_id)
    if not report or report.enquiry_id!=item.enquiry_id or report.document_type!="ZED_BRONZE_CERTIFICATE_REPORT": raise HTTPException(422,"Upload the ZED Bronze certificate/report to this enquiry before confirming certification.")
    item.zed_bronze_certification_status="COMPLETED"
    item.zed_bronze_certificate_number=(data.certificate_number or "").strip() or None
    item.zed_bronze_report_document_id=report.id
    item.zed_bronze_confirmed_at=datetime.now(timezone.utc)
    item.zed_bronze_confirmed_by=admin.id
    item.zed_silver_gold_interest=data.silver_gold_interest
    item.zed_marketing_plan_status="PENDING" if data.silver_gold_interest=="YES" else "NOT_REQUIRED"
    item.updated_at=datetime.now(timezone.utc)
    enquiry=db.get(Enquiry,item.enquiry_id)
    audit(db,admin.id,"ZED_BRONZE_CERTIFICATION_CONFIRMED","ENQUIRY",None,f"{enquiry.number}: Bronze certificate/report {report.file_name}; Silver/Gold interest {data.silver_gold_interest}",entity_ref=str(enquiry.id))
    if data.silver_gold_interest=="YES":
        creator=db.get(User,enquiry.created_by)
        marketer_ids={enquiry.assigned_marketing_user_id} if enquiry.assigned_marketing_user_id else ({enquiry.created_by} if creator and creator.role==Role.MARKETING else set())
        recipients=list(db.scalars(select(User).where(User.role==Role.MARKETING,User.status==AccountStatus.ACTIVE,User.id.in_(marketer_ids)))) if marketer_ids else list(db.scalars(select(User).where(User.role==Role.MARKETING,User.status==AccountStatus.ACTIVE)))
        for recipient in recipients:
            notify(db,recipient.id,f"ZED Bronze certification is complete for {enquiry.number}; the client is interested in ZED Silver/Gold. Prepare the next-step plan.","/marketing?view=zed-upgrades")
    db.commit();db.refresh(item)
    return _enquiry_onboarding_out(db,item)

@app.get("/api/marketing/zed-upgrade-referrals")
def marketing_zed_upgrade_referrals(db:DB,user:Annotated[User,Depends(require(Role.MARKETING))]):
    visible=or_(Enquiry.created_by==user.id,Enquiry.assigned_marketing_user_id==user.id,Enquiry.assigned_marketing_user_id.is_(None))
    rows=list(db.scalars(select(EnquiryOnboarding).join(Enquiry,Enquiry.id==EnquiryOnboarding.enquiry_id).where(EnquiryOnboarding.zed_marketing_plan_status.in_(("PENDING","PLANNED")),EnquiryOnboarding.zed_silver_gold_interest=="YES",visible).order_by(EnquiryOnboarding.zed_bronze_confirmed_at.asc())))
    return [{**_enquiry_onboarding_out(db,item).model_dump(mode="json",exclude={"payment","commercial_amount","initial_payment_amount"}),"enquiry":{"number":db.get(Enquiry,item.enquiry_id).number,"id":str(item.enquiry_id)}} for item in rows]

@app.post("/api/marketing/onboardings/{onboarding_id}/zed-upgrade-plan")
def save_zed_upgrade_plan(onboarding_id:int,data:ZedExpansionPlan,db:DB,user:Annotated[User,Depends(require(Role.MARKETING))]):
    item=db.get(EnquiryOnboarding,onboarding_id)
    if not item or item.zed_marketing_plan_status not in ("PENDING","PLANNED"): raise HTTPException(404,"ZED upgrade referral is not pending.")
    enquiry=db.get(Enquiry,item.enquiry_id)
    if not enquiry or not (enquiry.created_by==user.id or enquiry.assigned_marketing_user_id==user.id or enquiry.assigned_marketing_user_id is None): raise HTTPException(404,"ZED upgrade referral not found.")
    if not set(data.services).issubset({"ZED Silver","ZED Gold"}) or len(set(data.services))!=len(data.services): raise HTTPException(422,"Choose ZED Silver and/or ZED Gold only.")
    if data.client_accepted and len(data.services)!=1: raise HTTPException(422,"When the client accepts an upgrade, select exactly one ZED level.")
    previous_plan_status=item.zed_marketing_plan_status
    previous_follow_up_date=item.zed_marketing_plan_due_date
    item.zed_marketing_plan_services=", ".join(data.services)
    item.zed_marketing_plan_notes=data.notes.strip()
    item.zed_marketing_plan_due_date=data.follow_up_date
    item.zed_marketing_plan_status="CONVERTED" if data.client_accepted else "PLANNED"
    item.zed_marketing_planned_at=datetime.now(timezone.utc)
    item.zed_marketing_planned_by=user.id
    item.updated_at=datetime.now(timezone.utc)
    if data.client_accepted:
        target=data.services[0]
        item.service_name=target
        item.package_code=None
        item.planned_mandays=Decimal("14.00")
        item.commercial_amount=None
        item.initial_payment_amount=Decimal("0.00")
        enquiry.enquiry_for=[service for service in (enquiry.enquiry_for or []) if service!="ZED Bronze"]
        if target not in enquiry.enquiry_for: enquiry.enquiry_for.append(target)
        enquiry.package_code=None
        enquiry.expected_commercial_amount=None
        enquiry.updated_at=datetime.now(timezone.utc)
        for admin in db.scalars(select(User).where(User.role==Role.ADMIN,User.status==AccountStatus.ACTIVE)):
            notify(db,admin.id,f"{enquiry.number}: the client accepted the {target} upgrade from ZED Bronze. Admin can create the paid ZED Job Card and follow the standard M1 payment confirmation process.","/admin/job-cards")
    if data.follow_up_date and not data.client_accepted and (previous_plan_status=="PENDING" or previous_follow_up_date!=data.follow_up_date):
        plan_notes=f"ZED Bronze client interested in {item.zed_marketing_plan_services}. {item.zed_marketing_plan_notes}".strip()
        db.add(EnquiryFollowUp(enquiry_id=enquiry.id,follow_up_date=datetime.now(timezone.utc),follow_up_type="COMMERCIAL_DISCUSSION",person_contacted=enquiry.contact_person,notes=plan_notes,next_follow_up_date=datetime.combine(data.follow_up_date,TimeOfDay(10),tzinfo=timezone.utc),next_follow_up_type="CLIENT_DISCUSSION",responsible_user_id=user.id,status="OPEN"))
    action="ZED_BRONZE_UPGRADED_TO_PAID_ZED" if data.client_accepted else "ZED_SILVER_GOLD_PLAN_RECORDED"
    audit(db,user.id,action,"ENQUIRY",None,f"{enquiry.number}: {item.zed_marketing_plan_services}; {item.zed_marketing_plan_notes}",entity_ref=str(enquiry.id))
    db.commit();db.refresh(item)
    return _enquiry_onboarding_out(db,item)

@app.get("/api/finance/onboardings")
def finance_onboardings(db:DB,finance:Annotated[User,Depends(require(Role.FINANCE))],page:int=Query(default=1,ge=1),page_size:int=Query(default=100,ge=1,le=200)):
    return {"items":[],"total":0,"page":page,"page_size":page_size}

@app.post("/api/onboardings/{onboarding_id}/invoice",response_model=PaymentOut,status_code=201)
def create_onboarding_invoice(onboarding_id:int,data:PaymentCreate,db:DB,finance:Annotated[User,Depends(require(Role.FINANCE))]):
    raise HTTPException(410,"Onboarding invoices are no longer used. Raise a milestone invoice from the Job Card payment workspace.")

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

@app.post("/api/enquiries/{enquiry_id}/google-work-products",response_model=EnquiryDocumentOut,status_code=201)
def create_enquiry_google_work_product(enquiry_id:UUID,data:GoogleWorkProductCreate,db:DB,user:Annotated[User,Depends(require(Role.ADMIN))]):
    enquiry=db.get(Enquiry,enquiry_id)
    if not enquiry or enquiry.status!="CONVERTED" or enquiry.management_approval_status!="APPROVED": raise HTTPException(404,"Approved enquiry not found.")
    meeting_at=enquiry.client_meeting_at
    if enquiry.client_confirmation_status!="SCHEDULED" or not meeting_at: raise HTTPException(409,"Schedule a client discussion before creating its evidence document.")
    if meeting_at.tzinfo is None: meeting_at=meeting_at.replace(tzinfo=ZoneInfo(os.getenv("APP_TIMEZONE","Asia/Kolkata")))
    if meeting_at>datetime.now(timezone.utc): raise HTTPException(409,"Create the discussion document after the scheduled meeting has taken place.")
    access=_google_access_for_user(db,user.id)
    title=data.title.strip()
    created=google_workspace.create_file(access,title,data.kind)
    file_id=str(created.get("id", ""));url=str(created.get("webViewLink", ""))
    if not file_id or not url: raise HTTPException(502,"Google created the file but did not return its editor link.")
    filename,mime,content,_=_work_product_file(WorkProductDraftIn(kind=data.kind,title=title))
    key=save_file(filename,mime,content)
    row=EnquiryDocument(enquiry_id=enquiry_id,file_name=filename,storage_key=key,file_size=len(content),mime_type=mime,document_type="CLIENT_BRIEFING_GOOGLE_DRAFT",remarks="Google document. Sync its latest version here after adding the discussion record.",uploaded_by=user.id,google_file_id=file_id,google_file_url=url)
    db.add(row);db.flush();audit(db,user.id,"ENQUIRY_GOOGLE_WORK_PRODUCT_CREATED","ENQUIRY",None,f"{enquiry.number}: {title}; Google file {file_id}",entity_ref=str(enquiry.id));db.commit();db.refresh(row);return row

@app.post("/api/enquiry-documents/{document_id}/sync-google",response_model=EnquiryDocumentOut)
def sync_enquiry_google_work_product(document_id:int,db:DB,user:Annotated[User,Depends(require(Role.ADMIN))]):
    row=db.get(EnquiryDocument,document_id)
    if not row or not row.google_file_id: raise HTTPException(404,"Google discussion document not found.")
    enquiry=db.get(Enquiry,row.enquiry_id)
    if not enquiry or not _visible_enquiry(enquiry,user): raise HTTPException(404,"Google discussion document not found.")
    meeting_at=enquiry.client_meeting_at
    if not meeting_at: raise HTTPException(409,"Schedule the client discussion first.")
    if meeting_at.tzinfo is None: meeting_at=meeting_at.replace(tzinfo=ZoneInfo(os.getenv("APP_TIMEZONE","Asia/Kolkata")))
    if meeting_at>datetime.now(timezone.utc): raise HTTPException(409,"Sync discussion evidence after the scheduled meeting.")
    kind="SHEET" if row.file_name.lower().endswith(".xlsx") else "DOCUMENT"
    content,mime=google_workspace.export_file(_google_access_for_user(db,user.id),row.google_file_id,kind)
    if len(content)>MAX_BYTES: raise HTTPException(413,"Each file must be smaller than 1 GB.")
    extension=".xlsx" if kind=="SHEET" else ".docx"
    filename=re.sub(r"[^A-Za-z0-9._ -]","",row.file_name.rsplit(".",1)[0]).strip()[:100] or "Discussion"
    filename+=extension
    try: key=save_file(filename,mime,content)
    except ValueError as exc: raise HTTPException(422,f"Google export could not be saved to the portal: {exc}") from None
    old_key=row.storage_key;row.storage_key=key;row.file_name=filename;row.file_size=len(content);row.mime_type=mime;row.document_type="CLIENT_BRIEFING_EVIDENCE";row.google_synced_at=datetime.now(timezone.utc)
    audit(db,user.id,"ENQUIRY_GOOGLE_WORK_PRODUCT_SYNCED","ENQUIRY",None,f"{enquiry.number}: Google file {row.google_file_id}",entity_ref=str(enquiry.id));db.commit();db.refresh(row)
    try: (STORAGE_ROOT/old_key).unlink(missing_ok=True)
    except OSError: pass
    return row

@app.delete("/api/enquiry-documents/{document_id}",status_code=204)
def delete_enquiry_google_work_product(document_id:int,db:DB,user:Annotated[User,Depends(require(Role.ADMIN))]):
    row=db.get(EnquiryDocument,document_id)
    if not row or not row.google_file_id: raise HTTPException(404,"Google discussion document not found.")
    enquiry=db.get(Enquiry,row.enquiry_id)
    if not enquiry or not _visible_enquiry(enquiry,user): raise HTTPException(404,"Google discussion document not found.")
    google_workspace.delete_file(_google_access_for_user(db,user.id),row.google_file_id)
    storage_path=STORAGE_ROOT/row.storage_key
    audit(db,user.id,"ENQUIRY_GOOGLE_WORK_PRODUCT_DELETED","ENQUIRY",None,f"{enquiry.number}: Google file {row.google_file_id}",entity_ref=str(enquiry.id))
    db.delete(row);db.commit()
    try: storage_path.unlink(missing_ok=True)
    except OSError: pass
    return Response(status_code=204)

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
    _ensure_enquiry_sla_breaches(db,user)
    db.commit()
    now=datetime.now(timezone.utc)
    stmt=select(EnquiryFollowUp,Enquiry).join(Enquiry,Enquiry.id==EnquiryFollowUp.enquiry_id).where(EnquiryFollowUp.next_follow_up_date.is_not(None),EnquiryFollowUp.status!="CANCELLED",Enquiry.status.not_in(("CONVERTED","LOST","CANCELLED")))
    if user.role==Role.MARKETING: stmt=stmt.where(or_(Enquiry.created_by==user.id,Enquiry.assigned_marketing_user_id==user.id))
    if bucket=="overdue": stmt=stmt.where(EnquiryFollowUp.next_follow_up_date<now)
    elif bucket=="upcoming": stmt=stmt.where(EnquiryFollowUp.next_follow_up_date>=now)
    if search:
        term=f"%{search.strip()}%"
        stmt=stmt.where(or_(Enquiry.number.ilike(term),Enquiry.company_name.ilike(term),Enquiry.contact_person.ilike(term),EnquiryFollowUp.notes.ilike(term)))
    total=db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows=db.execute(stmt.order_by(EnquiryFollowUp.next_follow_up_date.asc()).offset((page-1)*page_size).limit(page_size)).all()
    items=[]
    for followup,enquiry in rows:
        breach=db.scalar(select(EnquirySLABreach.id).where(EnquirySLABreach.follow_up_id==followup.id,EnquirySLABreach.breach_type=="FOLLOW_UP"))
        due=followup.next_follow_up_date
        if due.tzinfo is None: due=due.replace(tzinfo=timezone.utc)
        items.append({"id":followup.id,"enquiry_id":str(enquiry.id),"enquiry_number":enquiry.number,"company_name":enquiry.company_name,"contact_person":enquiry.contact_person,"phone":enquiry.phone,"status":enquiry.status,"follow_up_type":followup.next_follow_up_type or followup.follow_up_type,"notes":followup.notes,"next_follow_up_date":due,"responsible_user_id":followup.responsible_user_id,"overdue":due<=now,"sla_breached":bool(breach)})
    return {"items":items,"total":total,"page":page,"page_size":page_size}

@app.get("/api/marketing/sla-breaches")
def marketing_sla_breaches(db:DB,user:Annotated[User,Depends(require(Role.ADMIN,Role.MARKETING))],page:int=Query(default=1,ge=1),page_size:int=Query(default=50,ge=1,le=100)):
    _ensure_enquiry_sla_breaches(db,user);db.commit()
    stmt=select(EnquirySLABreach,Enquiry,EnquiryFollowUp).join(Enquiry,Enquiry.id==EnquirySLABreach.enquiry_id).outerjoin(EnquiryFollowUp,EnquiryFollowUp.id==EnquirySLABreach.follow_up_id).where(EnquirySLABreach.status=="OPEN")
    if user.role==Role.MARKETING: stmt=stmt.where(or_(Enquiry.created_by==user.id,Enquiry.assigned_marketing_user_id==user.id))
    total=db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows=db.execute(stmt.order_by(EnquirySLABreach.breached_at.desc()).offset((page-1)*page_size).limit(page_size)).all()
    return {"items":[{"id":breach.id,"enquiry_id":str(enquiry.id),"enquiry_number":enquiry.number,"company_name":enquiry.company_name,"contact_person":enquiry.contact_person,"breach_type":breach.breach_type,"due_at":breach.due_at,"breached_at":breach.breached_at,"follow_up_type":followup.follow_up_type if followup else None,"status":breach.status} for breach,enquiry,followup in rows],"total":total,"page":page,"page_size":page_size}

@app.get("/api/enquiries/{enquiry_id}/related-records")
def enquiry_related_records(enquiry_id:UUID,db:DB,user:Annotated[User,Depends(current_user)]):
    if user.role==Role.MARKETING: raise HTTPException(403,"Converted enquiries are handed to Admin for Job Card onboarding.")
    enquiry=db.get(Enquiry,enquiry_id)
    if not enquiry or not _visible_enquiry(enquiry,user): raise HTTPException(404,"Enquiry not found.")
    cards=list(db.scalars(select(JobCard).where(JobCard.source_enquiry_id==enquiry_id).order_by(JobCard.created_at.desc())))
    return {"job_cards":[JobCardOut.model_validate(card).model_dump() for card in cards]}

@app.post("/api/enquiries/{enquiry_id}/link-job-card",response_model=JobCardOut)
def link_enquiry_job_card(enquiry_id:UUID,data:EnquiryLinkJobCard,db:DB,user:Annotated[User,Depends(require(Role.ADMIN,Role.MARKETING))]):
    raise HTTPException(409,"Direct linking is disabled. Convert the enquiry, complete Admin onboarding, and create the Job Card from the onboarding queue. Finance confirms milestone payments after Job Card creation.")

@app.post("/api/enquiries/{enquiry_id}/follow-ups",response_model=EnquiryFollowUpOut,status_code=201)
def create_enquiry_followup(enquiry_id:UUID,data:EnquiryFollowUpCreate,db:DB,user:Annotated[User,Depends(require(Role.ADMIN,Role.MARKETING))]):
    enquiry=db.get(Enquiry,enquiry_id)
    if not enquiry or not _visible_enquiry(enquiry,user): raise HTTPException(404,"Enquiry not found.")
    if user.role==Role.MARKETING and enquiry.status=="CONVERTED": raise HTTPException(409,"This enquiry has been handed to Admin and is no longer editable by Marketing.")
    _ensure_enquiry_sla_breaches(db,user)
    scheduled=list(db.scalars(select(EnquiryFollowUp).where(EnquiryFollowUp.enquiry_id==enquiry.id,EnquiryFollowUp.status=="OPEN",EnquiryFollowUp.next_follow_up_date.is_not(None))))
    for existing_schedule in scheduled:
        existing_schedule.status="CANCELLED"
        audit(db,user.id,"ENQUIRY_FOLLOW_UP_SUPERSEDED","ENQUIRY",None,f"{enquiry.number}: replaced scheduled follow-up",entity_ref=str(enquiry.id))
    values=data.model_dump();attachment_name=values.pop("attachment_name");attachment_mime_type=values.pop("attachment_mime_type");attachment_base64=values.pop("attachment_base64")
    if any((attachment_name,attachment_mime_type,attachment_base64)) and not all((attachment_name,attachment_mime_type,attachment_base64)): raise HTTPException(422,"Follow-up attachment name, type, and content are all required.")
    attachment_key=None
    if attachment_name: attachment_key,_=_save_encoded_file(attachment_name,attachment_mime_type,attachment_base64)
    row=EnquiryFollowUp(enquiry_id=enquiry_id,responsible_user_id=user.id,attachment_name=attachment_name,attachment_mime_type=attachment_mime_type,attachment_key=attachment_key,**values);db.add(row)
    if enquiry.status=="NEW":
        enquiry.status="CONTACTED"
    enquiry.follow_up_date=data.next_follow_up_date.date() if data.next_follow_up_date else None;enquiry.updated_at=datetime.now(timezone.utc)
    db.flush()
    if data.status=="COMPLETED": _resolve_enquiry_sla_breaches(db,enquiry,user)
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
    # Keep local/test schemas at the same Alembic revision as deployed databases.
    if ENV in ("development","test"):
        from alembic import command
        from alembic.config import Config
        backend_dir=Path(__file__).resolve().parent.parent
        command.upgrade(Config(str(backend_dir/"alembic.ini")),"head")
        seed()
    elif ENV=="production":
        disable_demo_accounts()
        ensure_payment_workflows()

async def _enquiry_sla_monitor():
    interval=max(30,int(os.getenv("SLA_MONITOR_INTERVAL_SECONDS","60")))
    while True:
        db=SessionLocal()
        try:
            admin=db.scalar(select(User).where(User.role==Role.ADMIN,User.status==AccountStatus.ACTIVE).order_by(User.id).limit(1))
            if admin:
                _ensure_enquiry_sla_breaches(db,admin)
                db.commit()
        except Exception:
            db.rollback();logging.getLogger(__name__).exception("Enquiry SLA monitor failed")
        finally:
            db.close()
        await asyncio.sleep(interval)

@app.on_event("startup")
async def start_enquiry_sla_monitor():
    app.state.enquiry_sla_task=asyncio.create_task(_enquiry_sla_monitor())
    app.state.planned_visit_sla_task=asyncio.create_task(_planned_visit_sla_monitor())

@app.on_event("shutdown")
async def stop_enquiry_sla_monitor():
    for name in ("enquiry_sla_task","planned_visit_sla_task"):
        task=getattr(app.state,name,None)
        if task:
            task.cancel()
            try: await task
            except asyncio.CancelledError: pass

async def _planned_visit_sla_monitor():
    interval=max(30,int(os.getenv("SLA_MONITOR_INTERVAL_SECONDS","60")))
    while True:
        db=SessionLocal()
        try:
            now=datetime.now(timezone.utc)
            pending_email=or_(and_(PlannedVisit.admin_escalated_at.is_not(None),PlannedVisit.admin_email_sent_at.is_(None)),and_(PlannedVisit.management_escalated_at.is_not(None),PlannedVisit.management_email_sent_at.is_(None)))
            rows=db.execute(select(PlannedVisit,JobCard.number,User.full_name).join(JobCard,JobCard.id==PlannedVisit.job_card_id).join(User,User.id==PlannedVisit.consultant_id).where(or_(PlannedVisit.status=="PLANNED",pending_email),JobCard.deleted_at.is_(None),PlannedVisit.visit_date<=now.astimezone(ZoneInfo("Asia/Kolkata")).date())).all()
            for row,card_number,consultant_name in rows:
                visit_end=datetime.combine(row.visit_date,row.start_time,tzinfo=ZoneInfo("Asia/Kolkata"))+timedelta(minutes=row.expected_duration_minutes)
                age=now-visit_end.astimezone(timezone.utc)
                admin_users=list(db.scalars(select(User).where(User.role==Role.ADMIN,User.status==AccountStatus.ACTIVE)))
                management_users=list(db.scalars(select(User).where(User.role==Role.MANAGEMENT,User.status==AccountStatus.ACTIVE)))
                if row.status=="PLANNED" and age>=timedelta(hours=24) and row.admin_escalated_at is None:
                    message=f"Visit update SLA breached: {consultant_name} has not submitted findings and evidence for {card_number}, planned {row.visit_date} at {row.start_time}."
                    for recipient in admin_users:
                        notify_once(db,recipient.id,message,f"/job-cards/{row.job_card_id}",f"planned-visit-sla-admin-{row.id}")
                    audit(db,None,"PLANNED_VISIT_SLA_ADMIN_ESCALATION","PLANNED_VISIT",row.id,message)
                    row.admin_escalated_at=now
                    db.commit()
                if row.admin_escalated_at is not None and age>=timedelta(hours=24) and row.admin_email_sent_at is None and admin_users and (row.admin_email_attempted_at is None or now-row.admin_email_attempted_at>=timedelta(minutes=15)):
                    claim=db.execute(update(PlannedVisit).where(PlannedVisit.id==row.id,PlannedVisit.admin_email_sent_at.is_(None),or_(PlannedVisit.admin_email_attempted_at.is_(None),PlannedVisit.admin_email_attempted_at<now-timedelta(minutes=15))).values(admin_email_attempted_at=now)).rowcount;db.commit()
                    if claim:
                        delivered=False
                        for recipient in admin_users:
                            try:
                                await asyncio.to_thread(send_email,recipient.email,"D'Mastru: site visit update overdue",f"Visit update SLA breached: {consultant_name} has not submitted findings and evidence for {card_number}, planned {row.visit_date} at {row.start_time}.\n\nPlease follow up with the consultant.")
                                delivered=True
                            except Exception: logger.exception("Could not email Admin about overdue planned visit %s",row.id)
                        if delivered: db.execute(update(PlannedVisit).where(PlannedVisit.id==row.id).values(admin_email_sent_at=datetime.now(timezone.utc)));db.commit()
                if row.status=="PLANNED" and age>=timedelta(hours=48) and row.management_escalated_at is None:
                    message=f"Second escalation: visit findings and evidence remain outstanding for {card_number}, consultant {consultant_name}, visit planned {row.visit_date} at {row.start_time}. Admin escalation was raised after 24 hours."
                    for recipient in management_users:
                        notify_once(db,recipient.id,message,f"/job-cards/{row.job_card_id}",f"planned-visit-sla-management-{row.id}")
                    audit(db,None,"PLANNED_VISIT_SLA_MANAGEMENT_ESCALATION","PLANNED_VISIT",row.id,message)
                    row.management_escalated_at=now
                    db.commit()
                if row.management_escalated_at is not None and age>=timedelta(hours=48) and row.management_email_sent_at is None and management_users and (row.management_email_attempted_at is None or now-row.management_email_attempted_at>=timedelta(minutes=15)):
                    claim=db.execute(update(PlannedVisit).where(PlannedVisit.id==row.id,PlannedVisit.management_email_sent_at.is_(None),or_(PlannedVisit.management_email_attempted_at.is_(None),PlannedVisit.management_email_attempted_at<now-timedelta(minutes=15))).values(management_email_attempted_at=now)).rowcount;db.commit()
                    if claim:
                        delivered=False
                        for recipient in management_users:
                            try:
                                await asyncio.to_thread(send_email,recipient.email,"D'Mastru: second escalation for missing visit update",f"Second escalation: visit findings and evidence remain outstanding for {card_number}, consultant {consultant_name}, visit planned {row.visit_date} at {row.start_time}. Admin escalation was raised after 24 hours.\n\nPlease review the overdue visit update.")
                                delivered=True
                            except Exception: logger.exception("Could not email Management about overdue planned visit %s",row.id)
                        if delivered: db.execute(update(PlannedVisit).where(PlannedVisit.id==row.id).values(management_email_sent_at=datetime.now(timezone.utc)));db.commit()
            overdue_visit_tasks=db.scalars(select(JobActivity).where(JobActivity.planned_visit_id.is_not(None),JobActivity.status.in_(("PLANNED","IN_PROGRESS")),JobActivity.sla_due_at.is_not(None),JobActivity.sla_due_at<=now)).all()
            admin_users=list(db.scalars(select(User).where(User.role==Role.ADMIN,User.status==AccountStatus.ACTIVE)))
            management_users=list(db.scalars(select(User).where(User.role==Role.MANAGEMENT,User.status==AccountStatus.ACTIVE)))
            for activity in overdue_visit_tasks:
                card=db.get(JobCard,activity.job_card_id)
                if not card or card.deleted_at: continue
                milestone=db.get(Milestone,activity.milestone_id)
                message=f"{milestone.code if milestone else 'Milestone'} visit task SLA breached: {activity.name} for {card.number} was due within 24 hours of the submitted visit report."
                notify_once(db,activity.assigned_user_id,message,f"/user/job-cards?card={card.id}",f"visit-task-sla-consultant-{activity.id}")
                for recipient in admin_users+management_users:
                    notify_once(db,recipient.id,message,f"/job-cards/{card.id}",f"visit-task-sla-review-{activity.id}-{recipient.id}")
            overdue_evidence=db.execute(select(JobActivityAttachment,JobActivity).join(JobActivity,JobActivity.id==JobActivityAttachment.activity_id).where(JobActivityAttachment.status.in_(("SUBMITTED","UNDER_REVIEW")),JobActivity.status=="COMPLETED",JobActivity.updated_at<=now-timedelta(hours=24))).all()
            for attachment,activity in overdue_evidence:
                card=db.get(JobCard,activity.job_card_id)
                if not activity or not card or card.deleted_at:continue
                for recipient in admin_users:
                    notify_once(db,recipient.id,f"Activity evidence approval is overdue: {attachment.file_name} for {activity.name} on Job Card {card.number} has been awaiting review for over 24 hours.","/admin/documents",f"activity-evidence-review-sla-{attachment.id}-{recipient.id}")
            overdue_visit_reports=db.scalars(select(RecordDocument).where(RecordDocument.visit_id.is_not(None),RecordDocument.document_type=="Consultant Visit Report",RecordDocument.status.in_(("SUBMITTED","UNDER_REVIEW")),RecordDocument.uploaded_at<=now-timedelta(hours=24))).all()
            for document in overdue_visit_reports:
                card=db.get(JobCard,document.owner_job_card_id) if document.owner_job_card_id else None
                for recipient in admin_users:
                    notify_once(db,recipient.id,f"Visit report approval is overdue: {document.file_name} for Job Card {card.number if card else document.owner_job_card_id} has been awaiting review for over 24 hours.",f"/admin/documents?document={document.id}",f"visit-report-review-sla-{document.id}-{recipient.id}")
            if overdue_visit_tasks or overdue_evidence or overdue_visit_reports: db.commit()
        except Exception:
            db.rollback();logger.exception("Planned visit SLA monitor failed")
        finally:
            db.close()
        await asyncio.sleep(interval)

@app.post("/api/auth/login",response_model=LoginChallengeOut|TokenOut)
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
    user.failed_attempts=0
    if user.password_change_required:
        challenge=_create_mfa_challenge(db,user)
        audit(db,user.id,"PASSWORD_CHANGE_CHALLENGE_ISSUED","USER",user.id)
        db.commit()
        return LoginChallengeOut(challenge_token=challenge,setup_required=not user.mfa_enabled,password_change_required=True,full_name=user.full_name,email=user.email)
    if user.role==Role.MANAGEMENT or not MFA_REQUIRED:
        user.last_login_at=datetime.now(timezone.utc)
        session=issue_session(db,user)
        detail="Management MFA exemption; password verified" if user.role==Role.MANAGEMENT else "MFA temporarily disabled; password verified"
        audit(db,user.id,"LOGIN","USER",user.id,detail)
        db.commit()
        return session
    challenge=_create_mfa_challenge(db,user)
    audit(db,user.id,"MFA_CHALLENGE_ISSUED","USER",user.id)
    db.commit()
    return LoginChallengeOut(challenge_token=challenge,setup_required=not user.mfa_enabled,password_change_required=user.password_change_required,full_name=user.full_name,email=user.email)

@app.post("/api/auth/initial-password")
def set_initial_password(data:InitialPasswordChange,db:DB):
    if data.new_password!=data.confirm_password: raise HTTPException(422,"Passwords do not match.")
    check_password_rules(data.new_password)
    challenge,user=_get_mfa_challenge(db,data.challenge_token)
    if not user.password_change_required: raise HTTPException(409,"A permanent password is not required for this account.")
    user.password_hash=passwords.hash(data.new_password); user.password_change_required=False
    user.reset_token_hash=None;user.reset_expires_at=None
    challenge.used_at=datetime.now(timezone.utc)
    for session in db.scalars(select(AuthSession).where(AuthSession.user_id==user.id)): session.revoked=True
    audit(db,user.id,"INITIAL_PASSWORD_SET","USER",user.id,"Permanent password set after Admin account creation")
    db.commit()
    next_step="Sign in again with your new password." if user.role==Role.MANAGEMENT or not MFA_REQUIRED else "Sign in again with your new password to continue MFA setup."
    return {"success":True,"message":f"Permanent password saved. {next_step}"}

@app.post("/api/auth/mfa/setup",response_model=MfaSetupOut)
def setup_authenticator(data:MfaChallengeRequest,db:DB):
    challenge,user=_get_mfa_challenge(db,data.challenge_token)
    if user.password_change_required: raise HTTPException(409,"Set a permanent password before setting up MFA.")
    if user.mfa_enabled: raise HTTPException(409,"Authenticator is already enabled for this account.")
    if not user.mfa_secret_encrypted:
        secret=base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
        user.mfa_secret_encrypted=_encrypt_sensitive(secret)
        db.commit()
    else: secret=_decrypt_mfa_secret(user)
    uri=f"otpauth://totp/D%27Mastru:{quote(user.email)}?secret={secret}&issuer=D%27Mastru&algorithm=SHA1&digits=6&period=30"
    return MfaSetupOut(secret=secret,provisioning_uri=uri)

@app.post("/api/auth/mfa/setup/verify",response_model=TokenOut)
def verify_authenticator_setup(data:MfaVerifyRequest,db:DB):
    return _verify_mfa_challenge(db,data,enrollment=True)

@app.post("/api/auth/mfa/verify",response_model=TokenOut)
def verify_authenticator_login(data:MfaVerifyRequest,db:DB):
    return _verify_mfa_challenge(db,data,enrollment=False)

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
    if not session or session.revoked or not session.mfa_verified or not expiry or expiry<datetime.now(timezone.utc): raise HTTPException(401,"Your session has expired. Please sign in again.")
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
    user.password_hash=passwords.hash(data.new_password); user.password_change_required=False; user.reset_token_hash=None; user.reset_expires_at=None
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

@app.post("/api/users",response_model=UserCreateOut,status_code=201)
def create_user(data:UserCreate,db:DB,admin:Annotated[User,Depends(require(Role.ADMIN))]):
    if db.scalar(select(User).where(User.email==str(data.email).lower())): raise HTTPException(409,"Email already exists.")
    check_password_rules(data.password)
    if data.role in (Role.ADMIN,Role.MANAGEMENT) and admin.role!=Role.ADMIN: raise HTTPException(403,"Only Admin can create privileged accounts.")
    if data.department_id and not db.get(Department,data.department_id): raise HTTPException(422,"Department does not exist.")
    initial_setup_token=secrets.token_urlsafe(48)
    user=User(full_name=data.full_name,email=str(data.email).lower(),mobile=data.mobile,consultant_id=data.consultant_id,role=data.role,department_id=data.department_id,status=AccountStatus.ACTIVE if data.role!=Role.END_USER else AccountStatus.PENDING_ACTIVATION,password_hash=passwords.hash(data.password),password_change_required=True,reset_token_hash=token_hash(initial_setup_token),reset_expires_at=datetime.now(timezone.utc)+timedelta(minutes=30)); db.add(user); db.flush(); audit(db,admin.id,"USER_CREATED","USER",user.id,user.email); db.commit(); db.refresh(user)
    email_sent=True
    try:
        public_url=os.getenv("PUBLIC_APP_URL","http://localhost:5173").rstrip("/")
        send_initial_password(user.email,data.password,f"{public_url}/reset-password?token={initial_setup_token}",f"{public_url}/login",user.status==AccountStatus.PENDING_ACTIVATION)
    except Exception:
        email_sent=False;logger.exception("Could not email initial password for user %s",user.id)
        audit(db,admin.id,"USER_INITIAL_PASSWORD_EMAIL_FAILED","USER",user.id,"SMTP delivery failed");db.commit()
    message="Initial password and permanent-password setup link emailed. The user must set a permanent password before signing in." if email_sent else "User created, but the initial-password email could not be sent. Check SMTP settings before sharing the initial password and setup link securely."
    return UserCreateOut.model_validate({**UserOut.model_validate(user).model_dump(),"initial_password_email_sent":email_sent,"message":message})

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
def admin_reset_password(user_id:int,db:DB,admin:Annotated[User,Depends(require(Role.ADMIN))]):
    user=db.get(User,user_id)
    if not user: raise HTTPException(404,"User not found.")
    raw=secrets.token_urlsafe(48);user.reset_token_hash=token_hash(raw);user.reset_expires_at=datetime.now(timezone.utc)+timedelta(minutes=30)
    for session in db.scalars(select(AuthSession).where(AuthSession.user_id==user.id)): session.revoked=True
    audit(db,admin.id,"USER_PASSWORD_RESET_REQUESTED","USER",user.id,"Admin sent a secure permanent-password setup link");db.commit()
    email_sent=True
    try:
        public_url=os.getenv("PUBLIC_APP_URL","http://localhost:5173").rstrip("/")
        send_password_reset(user.email,f"{public_url}/reset-password?token={raw}")
    except Exception:
        email_sent=False;logger.exception("Could not email password reset link for user %s",user.id)
        audit(db,admin.id,"USER_PASSWORD_RESET_EMAIL_FAILED","USER",user.id,"SMTP delivery failed");db.commit()
    response={"success":True,"email_sent":email_sent,"message":"Password setup link emailed. The user will choose and confirm a new permanent password." if email_sent else "Password setup link could not be emailed. Check SMTP settings and retry."}
    if ENV in ("development","test"): response["development_reset_token"]=raw
    return response

@app.post("/api/users/{user_id}/reset-mfa")
def admin_reset_mfa(user_id:int,db:DB,admin:Annotated[User,Depends(require(Role.ADMIN))]):
    user=db.get(User,user_id)
    if not user: raise HTTPException(404,"User not found.")
    user.mfa_enabled=False;user.mfa_secret_encrypted=None;user.mfa_last_counter=None
    for session in db.scalars(select(AuthSession).where(AuthSession.user_id==user.id)): session.revoked=True
    audit(db,admin.id,"USER_MFA_RESET","USER",user.id,"Authenticator enrollment reset; next sign-in requires setup.")
    notify(db,user.id,"Your authenticator setup was reset by Admin. Set it up again the next time you sign in.","/login")
    db.commit()
    return {"success":True,"message":"Authenticator reset; the user must enroll again at next sign-in."}

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
    service_name=data.service_name or ""
    if data.source_enquiry_id:
        # The onboarding lookup is validated below; resolve the persisted service plan here for ZED defaults.
        early_onboarding=db.scalar(select(EnquiryOnboarding).where(EnquiryOnboarding.enquiry_id==data.source_enquiry_id))
        if early_onboarding:
            eligible_services=[part.strip() for part in early_onboarding.service_name.split(",") if part.strip()]
            if data.service_name and data.service_name not in eligible_services and data.service_name!=early_onboarding.service_name: raise HTTPException(422,"Choose a service that was selected on the source enquiry.")
            service_name=data.service_name or early_onboarding.service_name
    if "ZED Bronze" in [part.strip() for part in service_name.split(",") if part.strip()]: raise HTTPException(409,"ZED Bronze certification does not use a Job Card. Create one only after the client accepts a paid ZED Silver or Gold upgrade.")
    zed_plan=any(name.strip().casefold() in ("zed silver","zed gold") for name in service_name.split(","))
    if package and service_name and not _package_supports_service(package,[part.strip() for part in service_name.split(",") if part.strip()]): raise HTTPException(422,"Select a package configured for the selected service.")
    if not package and data.required_md is None and not zed_plan: raise HTTPException(422,"Planned mandays are required when a service package is not selected.")
    required_md=Decimal("14") if zed_plan else (Decimal(str(package.planned_mandays)) if package else data.required_md)
    package_scope=package.scope if package else data.scope
    if data.project_end_date < data.project_start_date: raise HTTPException(422,"Project end date cannot be before the start date.")
    msme=db.get(MSME,data.msme_id)
    if not msme or msme.deleted_at or msme.status!="ACTIVE": raise HTTPException(422,"Selected MSME is not active.")
    source_enquiry=db.get(Enquiry,data.source_enquiry_id) if data.source_enquiry_id else None
    onboarding=None
    if data.source_enquiry_id and not source_enquiry: raise HTTPException(404,"Source enquiry not found.")
    if source_enquiry:
        if not _visible_enquiry(source_enquiry,admin): raise HTTPException(404,"Source enquiry not found.")
        if source_enquiry.status!="CONVERTED": raise HTTPException(409,"Only a converted enquiry can be onboarded into a Job Card.")
        if source_enquiry.management_approval_status!="APPROVED": raise HTTPException(409,"Management approval is required before creating this Job Card.")
        onboarding=db.scalar(select(EnquiryOnboarding).where(EnquiryOnboarding.enquiry_id==source_enquiry.id).with_for_update())
        if not onboarding or onboarding.status not in ("ONBOARDED","PAYMENT_CONFIRMED"): raise HTTPException(409,"Complete Admin onboarding before creating this Job Card.")
        if [part.strip() for part in onboarding.service_name.split(",") if part.strip()]==["ZED Bronze"]: raise HTTPException(409,"ZED Bronze certification does not require a Job Card. Create one only if the client later accepts a paid ZED Silver or Gold upgrade.")
        if onboarding.msme_id!=data.msme_id: raise HTTPException(422,"Use the MSME linked during onboarding.")
        eligible_services=[part.strip() for part in onboarding.service_name.split(",") if part.strip()]
        if data.service_name and data.service_name not in eligible_services and data.service_name!=onboarding.service_name: raise HTTPException(422,"Choose a service that was selected on the source enquiry.")
        if onboarding.package_code and data.package_code!=onboarding.package_code: raise HTTPException(422,"Use the service package selected during onboarding.")
        if package and not _package_supports_service(package,[data.service_name] if data.service_name in eligible_services else eligible_services): raise HTTPException(422,"Select a package configured for the selected service.")
        if db.scalar(select(JobCard.id).where(JobCard.source_enquiry_id==source_enquiry.id)): raise HTTPException(409,"This enquiry already has a Job Card linked.")
    if any((data.lead_consultant_id,data.co_consultant_id,data.subject_expert_id)):
        raise HTTPException(409,"Consultants can be assigned only after Finance confirms the client’s M1 payment to QCI.")
    ids=[(data.lead_consultant_id,"LEAD_CONSULTANT"),(data.co_consultant_id,"CO_CONSULTANT"),(data.subject_expert_id,"SUBJECT_EXPERT")]
    seen=set()
    for user_id,_ in ids:
        if user_id:
            if user_id in seen: raise HTTPException(422,"A consultant cannot occupy more than one role on a Job Card.")
            seen.add(user_id); active_consultant(db,user_id)
    if data.director_id: active_reviewer(db,data.director_id,Role.DIRECTOR)
    hours_per_manday=data.hours_per_manday or default_hours_per_manday(db)
    service_name=(data.service_name or onboarding.service_name) if onboarding else data.service_name
    zed_plan=any(name.strip().casefold() in ("zed silver","zed gold") for name in (service_name or "").split(","))
    project_name=onboarding.project_name if onboarding else next_business_number(db,"project")
    card=JobCard(number=next_business_number(db,"job_card"),source_enquiry_id=source_enquiry.id if source_enquiry else None,project_name=project_name,service_name=service_name,package_code=package.code if package else data.package_code,scope=(onboarding.scope if onboarding else package_scope),commercial_amount=(Decimal(str(onboarding.commercial_amount)) if onboarding and onboarding.commercial_amount is not None else (package.commercial_amount if package else None)),project_payment_percent=package.project_payment_percent if package else None,msme_id=data.msme_id,location=(onboarding.location if onboarding else data.location),project_start_date=data.project_start_date,project_end_date=data.project_end_date,required_md=required_md,hours_per_manday=hours_per_manday,required_hours=required_md*hours_per_manday,director_id=data.director_id,remarks=data.remarks,created_by=admin.id,payment_gate_enabled=True,status="AWAITING_PAYMENT")
    db.add(card)
    try: db.flush()
    except IntegrityError:
        db.rollback();raise HTTPException(409,"This enquiry already has a Job Card linked.")
    if zed_plan:
        configured={item["code"]:item for item in (package.milestones if package else [])}
        defaults=(("M1","Initial assessment",Decimal("2.00")),("M2","Implementation support",Decimal("6.00")),("M3","Final review and recommendations",Decimal("6.00")))
        for code,name,planned in defaults:
            item=configured.get(code,{})
            # ZED Silver and Gold always use the agreed 2/6/6 manday split;
            # package edits may customize content, but cannot change this plan.
            db.add(Milestone(job_card_id=card.id,code=code,name=item.get("name",name),description=package.scope if package else (onboarding.scope if onboarding else data.scope),planned_visits=item.get("planned_visits",1),planned_mandays=planned,amount=item.get("amount"),payment_percent=item.get("payment_percent"),activities=item.get("activities",[])))
    elif package:
        for configured in package.milestones:
            db.add(Milestone(job_card_id=card.id,code=configured["code"],name=configured["name"],planned_visits=configured["planned_visits"],planned_mandays=configured["planned_mandays"],amount=configured["amount"],payment_percent=configured["payment_percent"],activities=configured.get("activities",[]),description=package.scope))
    if source_enquiry:
        was_converted=source_enquiry.status=="CONVERTED"
        source_enquiry.status="CONVERTED";source_enquiry.updated_at=datetime.now(timezone.utc)
        audit(db,admin.id,"ENQUIRY_ONBOARDED" if was_converted else "ENQUIRY_CONVERTED","ENQUIRY",None,f"{source_enquiry.number} -> {card.number}",entity_ref=str(source_enquiry.id))
    for user_id,kind in ids:
        if user_id:
            db.add(Assignment(job_card_id=card.id,user_id=user_id,assignment_type=kind,assigned_by=admin.id,allocation_start_date=card.project_start_date,allocation_end_date=card.project_end_date)); notify(db,user_id,f"You were assigned as {kind.replace('_',' ').title()} on Job Card {card.number}.",f"/job-cards/{card.id}")
    for user_id,kind in ((data.director_id,"DIRECTOR"),):
        if user_id:
            db.add(Assignment(job_card_id=card.id,user_id=user_id,assignment_type=kind,assigned_by=admin.id));notify(db,user_id,f"You were assigned as {kind.replace('_',' ').title()} on Job Card {card.number}.",f"/job-cards/{card.id}")
    if onboarding:
        onboarding.status="JOB_CARD_CREATED";onboarding.job_card_id=card.id
    db.flush()
    first_milestone=db.scalar(select(Milestone).where(Milestone.job_card_id==card.id,Milestone.code=="M1"))
    if first_milestone:
        _start_triggered_workflows(db,"JOB_CARD_CREATED",card,admin,first_milestone)
        for finance_user in db.scalars(select(User).where(User.role==Role.FINANCE,User.status==AccountStatus.ACTIVE)):
            notify(db,finance_user.id,f"Confirm that the client’s M1 payment to QCI is complete for Job Card {card.number}. Consultant assignment is blocked until confirmation.",f"/finance/payments?job_card={card.id}&milestone_id={first_milestone.id if first_milestone else ''}")
    audit(db,admin.id,"JOB_CARD_CREATED","JOB_CARD",card.id,card.number)
    if package: audit(db,admin.id,"PACKAGE_SNAPSHOT_APPLIED","JOB_CARD",card.id,f"{package.code}; {len(package.milestones)} milestones")
    db.commit(); db.refresh(card); return _job_card_out_for_user(card,admin)

@app.get("/api/job-cards",response_model=list[JobCardListOut])
def list_job_cards(db:DB,user:Annotated[User,Depends(current_user)],status_filter:str|None=Query(default=None,alias="status"),msme_id:int|None=None,consultant_id:int|None=None,search:str|None=None,location:str|None=None,zed_id:str|None=None,certificate_level:str|None=None,start_date:date|None=None,end_date:date|None=None,delayed:bool|None=None,overrun:bool|None=None,page:int=Query(default=1,ge=1),page_size:int=Query(default=25,ge=1,le=100)):
    if user.role in (Role.MARKETING,Role.FINANCE): raise HTTPException(403,"This role cannot access Job Card activities.")
    query=select(JobCard).where(JobCard.deleted_at==None)
    if user.role==Role.END_USER: query=query.join(Assignment,Assignment.job_card_id==JobCard.id).where(Assignment.user_id==user.id,Assignment.active==True)
    elif user.role==Role.UNIT_REPRESENTATIVE: query=query.where(JobCard.id<0)
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
    if user.role==Role.DIRECTOR:
        ready=[]
        for card in cards:
            latest=int(db.scalar(select(func.coalesce(func.max(Approval.cycle),0)).where(Approval.job_card_id==card.id)) or 1)
            consultant_approved=db.scalar(select(Approval.id).where(Approval.job_card_id==card.id,Approval.cycle==latest,Approval.stage=="CONSULTANT_CONFIRMATION",Approval.decision=="APPROVED"))
            director_done=db.scalar(select(Approval.id).where(Approval.job_card_id==card.id,Approval.cycle==latest,Approval.stage=="DIRECTOR_REVIEW"))
            if consultant_approved and not director_done: ready.append(card)
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
    planned_visits=db.execute(select(PlannedVisit,User.full_name,Milestone.code).join(User,User.id==PlannedVisit.consultant_id).join(Milestone,Milestone.id==PlannedVisit.milestone_id).where(PlannedVisit.job_card_id==card.id).order_by(PlannedVisit.visit_date.desc(),PlannedVisit.start_time.desc()).limit(250)).all()
    documents=list(db.scalars(select(Document).where(Document.job_card_id==card.id,Document.deleted_at.is_(None)).order_by(Document.uploaded_at.desc()).limit(250)))
    record_documents=list(db.scalars(select(RecordDocument).where(RecordDocument.owner_job_card_id==card.id,RecordDocument.deleted_at.is_(None)).order_by(RecordDocument.uploaded_at.desc()).limit(500)))
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
        item={**MilestoneOut(**{**milestone.__dict__,**milestone_summary(db,milestone),**_milestone_staffing_summary(db,milestone)}).model_dump(mode="json"),"reviews":[MilestoneReviewOut.model_validate(event).model_dump(mode="json") for event in reviews if event.milestone_id==milestone.id]}
        if user.role==Role.ADMIN:
            item["amount"]=None
            item["payment_percent"]=None
            item["payment_status"]=None
            item["payment_date"]=None
            if item.get("status")=="LOCKED": item["status"]="PENDING_ACTIVATION"
        milestone_rows.append(item)
    planned_visit_rows=[{**PlannedVisitOut.model_validate(row).model_dump(mode="json"),"consultant_name":consultant_name,"milestone_code":milestone_code} for row,consultant_name,milestone_code in planned_visits]
    activity_rows=[_job_activity_out(db,row).model_dump(mode="json") for row in activities]
    attachments=list(db.scalars(select(JobActivityAttachment).join(JobActivity,JobActivity.id==JobActivityAttachment.activity_id).where(JobActivity.job_card_id==card.id).order_by(JobActivityAttachment.uploaded_at.desc())))
    attachments_by_activity:dict[int,list[dict]]={}
    for attachment in attachments:
        attachments_by_activity.setdefault(attachment.activity_id,[]).append(JobActivityAttachmentOut.model_validate(attachment).model_dump(mode="json"))
    for activity in activity_rows: activity["attachments"]=attachments_by_activity.get(activity["id"],[])
    team_rows=[{"user_id":allocation.consultant_id,"full_name":(db.get(User,allocation.consultant_id).full_name if db.get(User,allocation.consultant_id) else ""),"assignment_type":"MILESTONE_CONSULTANT","milestone_code":milestone.code,"allocated_mandays":str(allocation.allocated_mandays)} for milestone in milestones for allocation in _milestone_allocations(db,milestone) if db.get(User,allocation.consultant_id)]
    team_rows.extend({"user_id":u.id,"full_name":u.full_name,"assignment_type":a.assignment_type,"milestone_code":None} for a,u in assignments if a.assignment_type=="DIRECTOR")
    return {"job_card":_job_card_out_for_user(card,user).model_dump(mode="json"),"client":MSMEOut.model_validate(msme).model_dump(mode="json"),"source_ticket":source_ticket_row,"source_enquiry":enquiry_row,"related_tickets":ticket_rows,"team":team_rows,"mandays":manday_summary(db,card),"milestones":milestone_rows,"activities":activity_rows,"visits":[VisitOut.model_validate(row).model_dump(mode="json") for row in visits],"planned_visits":planned_visit_rows,"documents":[DocumentOut.model_validate(row).model_dump(mode="json") for row in documents],"record_documents":[RecordDocumentOut.model_validate(row).model_dump(mode="json") for row in record_documents],"payments":[_payment_out(row) for row in payments],"actions":[_workflow_out(db,row).model_dump(mode="json") for row in actions],"approvals":[ApprovalOut.model_validate(row).model_dump(mode="json") for row in approvals],"closure":ClosureOut.model_validate(closure).model_dump(mode="json") if closure else None,"history":[AuditOut.model_validate(row).model_dump(mode="json") for row in audit_rows]}

@app.get("/api/job-cards/{card_id}/assignments")
def list_assignments(card_id:int,db:DB,user:Annotated[User,Depends(current_user)]):
    assigned_card(db,user,card_id)
    rows=db.execute(select(Assignment,User).join(User,User.id==Assignment.user_id).where(Assignment.job_card_id==card_id).order_by(Assignment.assigned_at.desc(),Assignment.id.desc())).all()
    return [{"id":a.id,"user_id":u.id,"full_name":u.full_name,"assignment_type":a.assignment_type,"assigned_at":a.assigned_at,"allocation_start_date":a.allocation_start_date,"allocation_end_date":a.allocation_end_date,"ended_at":a.ended_at,"active":a.active} for a,u in rows]

@app.post("/api/planned-visits/{planned_visit_id}/consultant-report",response_model=RecordDocumentOut,status_code=201)
def create_consultant_visit_report(planned_visit_id:int,data:ConsultantVisitReportIn,db:DB,user:Annotated[User,Depends(require(Role.END_USER))]):
    planned=db.get(PlannedVisit,planned_visit_id)
    if not planned: raise HTTPException(404,"Planned visit not found.")
    card=mutable_card(assigned_card(db,user,planned.job_card_id))
    if user.id not in {planned.consultant_id,planned.co_consultant_id,planned.subject_expert_id}: raise HTTPException(403,"Only a consultant assigned to this visit can submit its report.")
    milestone=db.get(Milestone,planned.milestone_id)
    _require_work_payment(db,card,milestone)
    visit=db.scalar(select(Visit).where(Visit.planned_visit_id==planned.id,Visit.deleted_at.is_(None),Visit.status.in_(("SUBMITTED","APPROVED"))))
    if not visit: raise HTTPException(409,"Submit the visit report before creating the milestone report.")
    tasks=list(db.scalars(select(JobActivity).where(JobActivity.planned_visit_id==planned.id)))
    if not tasks or not visit_deliverables_complete(db,milestone,planned.id):
        raise HTTPException(409,"Every required visit activity and its evidence must be completed, submitted, and approved by Admin before submitting the visit report.")
    active=db.scalar(select(RecordDocument.id).where(RecordDocument.visit_id==visit.id,RecordDocument.document_type=="Consultant Visit Report",RecordDocument.status.in_(("DRAFT","SUBMITTED","UNDER_REVIEW","APPROVED"))).order_by(RecordDocument.version.desc()).limit(1))
    if active: raise HTTPException(409,"A report for this visit is already submitted or approved.")
    evidence_rows=[]
    for task in tasks:
        attachment=db.scalar(select(JobActivityAttachment).where(JobActivityAttachment.activity_id==task.id).order_by(JobActivityAttachment.id.desc()).limit(1))
        evidence_rows.append(f"{task.name}: {attachment.file_name if attachment else 'evidence pending'}")
    rows=[{"Section":"Title","Details":data.title},{"Section":"Job Card","Details":card.number},{"Section":"Milestone","Details":f"{milestone.code} {milestone.name}"},{"Section":"Visit date","Details":str(visit.visit_date)},{"Section":"Consultant","Details":user.full_name},{"Section":"Required activities and evidence","Details":"\n".join(evidence_rows)},{"Section":"Summary","Details":data.summary},{"Section":"Findings","Details":data.findings},{"Section":"Actions and recommendations","Details":data.actions or "None"},{"Section":"Conclusion","Details":data.conclusion}]
    if data.format=="xlsx":
        content=to_xlsx(rows);extension="xlsx";mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        paragraphs="".join(f'<w:p><w:r><w:t xml:space="preserve">{xml_escape(row["Section"])}: {xml_escape(row["Details"])}</w:t></w:r></w:p>' for row in rows)
        content_types='<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'
        document=f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{paragraphs}<w:sectPr/></w:body></w:document>'
        buffer=BytesIO()
        with ZipFile(buffer,"w",ZIP_DEFLATED) as package:
            package.writestr("[Content_Types].xml",content_types)
            package.writestr("_rels/.rels",'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
            package.writestr("word/document.xml",document)
        content=buffer.getvalue();extension="docx";mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    filename=f"{card.number}-{milestone.code}-visit-{visit.visit_number}-report.{extension}"
    try: storage_key=save_file(filename,mime,content)
    except ValueError as exc: raise HTTPException(422,str(exc))
    version=int(db.scalar(select(func.coalesce(func.max(RecordDocument.version),0)).where(RecordDocument.visit_id==visit.id,RecordDocument.document_type=="Consultant Visit Report")) or 0)+1
    document=RecordDocument(owner_job_card_id=card.id,visit_id=visit.id,document_type="Consultant Visit Report",file_name=filename,storage_key=storage_key,file_size=len(content),mime_type=mime,version=version,status="SUBMITTED",remarks="Submitted by consultant for Admin approval.",uploaded_by=user.id)
    db.add(document);db.flush();audit(db,user.id,"CONSULTANT_REPORT_SUBMITTED","DOCUMENT",document.id,f"{card.number}; {milestone.code}; planned visit {planned.id}")
    for admin in db.scalars(select(User).where(User.role==Role.ADMIN,User.status==AccountStatus.ACTIVE)):
        notify(db,admin.id,f"Consultant visit report for {card.number} {milestone.code} is awaiting approval. Please review it within 24 hours.",f"/admin/documents?document={document.id}")
    db.commit();db.refresh(document);return document

@app.get("/api/job-cards/{card_id}/record-documents",response_model=list[RecordDocumentOut])
def list_job_card_record_documents(card_id:int,db:DB,user:Annotated[User,Depends(current_user)]):
    assigned_card(db,user,card_id)
    query=select(RecordDocument).where(RecordDocument.owner_job_card_id==card_id,RecordDocument.deleted_at.is_(None))
    if user.role==Role.ADMIN: query=query.where(RecordDocument.payment_id.is_(None))
    return list(db.scalars(query.order_by(RecordDocument.uploaded_at.desc(),RecordDocument.id.desc()).limit(500)))

@app.get("/api/my/record-documents")
def list_my_record_documents(db:DB,user:Annotated[User,Depends(require(Role.END_USER))]):
    rows=db.execute(select(RecordDocument,JobCard.number)
        .join(JobCard,JobCard.id==RecordDocument.owner_job_card_id)
        .where(RecordDocument.uploaded_by==user.id,RecordDocument.owner_job_card_id.is_not(None),RecordDocument.payment_id.is_(None),RecordDocument.deleted_at.is_(None),JobCard.deleted_at.is_(None))
        .order_by(RecordDocument.uploaded_at.desc(),RecordDocument.id.desc()).limit(500)).all()
    return [{**RecordDocumentOut.model_validate(document).model_dump(mode="json"),"job_card_number":job_card_number} for document,job_card_number in rows]

@app.get("/api/record-documents/review-queue")
def record_document_review_queue(db:DB,user:Annotated[User,Depends(require(Role.ADMIN))],page:int=Query(default=1,ge=1),page_size:int=Query(default=25,ge=1,le=100)):
    latest=(select(RecordDocument.id.label("id"),func.row_number().over(partition_by=(RecordDocument.owner_job_card_id,RecordDocument.document_type,RecordDocument.ticket_id,RecordDocument.enquiry_id,RecordDocument.job_card_id,RecordDocument.milestone_id,RecordDocument.action_id,RecordDocument.visit_id,RecordDocument.payment_id,RecordDocument.milestone_review_id,RecordDocument.visit_review_id,RecordDocument.approval_id,RecordDocument.activity_id),order_by=(RecordDocument.version.desc(),RecordDocument.id.desc())).label("rank")).where(RecordDocument.owner_job_card_id.is_not(None),RecordDocument.payment_id.is_(None),RecordDocument.deleted_at.is_(None),RecordDocument.status.in_(("SUBMITTED","UNDER_REVIEW"))).subquery())
    conditions=(RecordDocument.owner_job_card_id.is_not(None),RecordDocument.payment_id.is_(None),RecordDocument.deleted_at.is_(None),RecordDocument.status.in_(("SUBMITTED","UNDER_REVIEW")),RecordDocument.id.in_(select(latest.c.id).where(latest.c.rank==1)))
    base=select(RecordDocument,JobCard.number,MSME.name,Milestone.code,User.full_name).join(JobCard,JobCard.id==RecordDocument.owner_job_card_id).join(MSME,MSME.id==JobCard.msme_id).outerjoin(Visit,Visit.id==RecordDocument.visit_id).outerjoin(Milestone,or_(Milestone.id==RecordDocument.milestone_id,Milestone.id==Visit.milestone_id)).join(User,User.id==RecordDocument.uploaded_by).where(*conditions,JobCard.deleted_at.is_(None))
    total=int(db.scalar(select(func.count()).select_from(base.subquery())) or 0)
    rows=db.execute(base.order_by(RecordDocument.uploaded_at.desc(),RecordDocument.id.desc()).offset((page-1)*page_size).limit(page_size)).all()
    items=[{**RecordDocumentOut.model_validate(document).model_dump(mode="json"),"job_card_number":number,"msme_name":msme_name,"milestone_code":milestone_code,"uploaded_by_name":uploaded_by_name} for document,number,msme_name,milestone_code,uploaded_by_name in rows]
    return {"items":items,"total":total,"page":page,"page_size":page_size}

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
    sla_due=row.sla_due_at
    if sla_due and sla_due.tzinfo is None: sla_due=sla_due.replace(tzinfo=timezone.utc)
    visit=db.get(Visit,row.visit_id) if row.visit_id else None
    plan=db.get(PlannedVisit,row.planned_visit_id) if row.planned_visit_id else None
    milestone=db.get(Milestone,row.milestone_id)
    msme=db.get(MSME,card.msme_id) if card else None
    activity_visit=visit.visit_number if visit else plan.visit_number if plan else None
    refs=[]
    if milestone and activity_visit:
        candidates=list(db.scalars(select(ZedReferenceDocument).where(ZedReferenceDocument.active.is_(True),ZedReferenceDocument.milestone_code==milestone.code,ZedReferenceDocument.visit_number==activity_visit).order_by(ZedReferenceDocument.uploaded_at.desc())))
        activity_name=" ".join(row.name.split()).casefold()
        candidates=[ref for ref in candidates if " ".join(ref.activity_name.split()).casefold()==activity_name]
        industry=(msme.industry or "").strip().casefold() if msme else ""
        specific=[ref for ref in candidates if ref.industry and ref.industry.strip().casefold()==industry]
        refs=[specific[0]] if specific else [ref for ref in candidates if not ref.industry][:1]
    return JobActivityOut(id=row.id,job_card_id=row.job_card_id,milestone_id=row.milestone_id,visit_id=row.visit_id,visit_number=activity_visit,planned_visit_id=row.planned_visit_id,activity_type_id=row.activity_type_id,activity_type_code=kind.code if kind else "",activity_type_name=kind.name if kind else "",name=row.name,description=row.description,assigned_user_id=row.assigned_user_id,assigned_user_name=assignee.full_name if assignee else "",start_date=row.start_date,end_date=row.end_date,expected_completion_date=row.expected_completion_date,sla_due_at=sla_due,duration_minutes=row.duration_minutes,mandays=mandays,status=row.status,review_status=row.review_status,review_remarks=row.review_remarks,reviewed_by=row.reviewed_by,reviewed_at=row.reviewed_at,remarks=row.remarks,created_by=row.created_by,created_at=row.created_at,reference_documents=[{"id":ref.id,"industry":ref.industry,"milestone_code":ref.milestone_code,"visit_number":ref.visit_number,"activity_name":ref.activity_name,"file_name":ref.file_name,"file_size":ref.file_size,"mime_type":ref.mime_type} for ref in refs])

def _activity_record_documents(db:Session,row:JobActivity)->list[RecordDocument]:
    legacy_type_pattern=f"%Activity-{row.id}\\_%"
    query=select(RecordDocument).where(
        or_(RecordDocument.job_card_id==row.job_card_id,RecordDocument.owner_job_card_id==row.job_card_id),
        RecordDocument.deleted_at.is_(None),
        or_(RecordDocument.activity_id==row.id,and_(RecordDocument.activity_id.is_(None),RecordDocument.document_type.like(legacy_type_pattern,escape="\\"))),
    ).order_by(RecordDocument.version.desc(),RecordDocument.id.desc())
    return list(db.scalars(query))

def _ensure_activity_draft_document(db:Session,row:JobActivity,user:User)->None:
    documents=_activity_record_documents(db,row)
    if any(document.status=="DRAFT" for document in documents):return
    prior=next((document for document in documents if document.status in ("SUBMITTED","UNDER_REVIEW","APPROVED","RETURNED","REJECTED") and document.editor_provider=="GOOGLE" and document.google_file_id),None)
    if not prior:return
    prior_path=STORAGE_ROOT/prior.storage_key
    if not prior_path.is_file():raise HTTPException(409,"Sync the submitted Google Doc before starting an in-progress draft.")
    latest_version=max((document.version for document in documents),default=prior.version)
    suffix=Path(prior.file_name).suffix
    google_title=f"{Path(prior.file_name).stem}_Draft_v{latest_version+1}"
    copied=google_workspace.copy_file(_google_access_for_user(db,user.id),prior.google_file_id,google_title)
    file_id=str(copied.get("id", ""));url=str(copied.get("webViewLink", ""))
    if not file_id or not url:raise HTTPException(502,"Google copied the draft but did not return its editor link.")
    try:key=save_file(prior.file_name,prior.mime_type,prior_path.read_bytes())
    except ValueError as exc:raise HTTPException(422,str(exc)) from None
    revision=RecordDocument(owner_job_card_id=prior.owner_job_card_id,job_card_id=row.job_card_id,activity_id=row.id,document_type=prior.document_type,file_name=f"{Path(prior.file_name).stem}_Draft_v{latest_version+1}{suffix}",storage_key=key,file_size=prior.file_size,mime_type=prior.mime_type,editor_provider=prior.editor_provider,google_file_id=file_id,google_file_url=url,version=latest_version+1,status="DRAFT",remarks=f"Draft copy opened while activity {row.name} is in progress.",uploaded_by=user.id)
    db.add(revision);db.flush()
    audit(db,user.id,"ACTIVITY_DOCUMENT_DRAFT_STARTED","DOCUMENT",revision.id,f"Activity {row.id}; copied from version {prior.version}.")

@app.get("/api/job-cards/{card_id}/activities",response_model=list[JobActivityOut])
def list_job_activities(card_id:int,db:DB,user:Annotated[User,Depends(current_user)],milestone_id:int|None=None,status_filter:str|None=Query(default=None,alias="status")):
    assigned_card(db,user,card_id);query=select(JobActivity).outerjoin(PlannedVisit,JobActivity.planned_visit_id==PlannedVisit.id).where(JobActivity.job_card_id==card_id,or_(JobActivity.planned_visit_id.is_(None),PlannedVisit.status!="CANCELLED"))
    if milestone_id:query=query.where(JobActivity.milestone_id==milestone_id)
    if status_filter:query=query.where(JobActivity.status==status_filter)
    if user.role==Role.END_USER:query=query.where(JobActivity.assigned_user_id==user.id)
    return [_job_activity_out(db,row) for row in db.scalars(query.order_by(JobActivity.start_date,JobActivity.id.desc()).limit(250))]

@app.get("/api/zed-m1/references")
def list_zed_m1_references(db:DB,user:Annotated[User,Depends(current_user)]):
    rows=list(db.scalars(select(ZedReferenceDocument).where(ZedReferenceDocument.active.is_(True)).order_by(ZedReferenceDocument.visit_number,ZedReferenceDocument.activity_name,ZedReferenceDocument.uploaded_at.desc())))
    return [{"id":r.id,"industry":r.industry,"milestone_code":r.milestone_code,"visit_number":r.visit_number,"activity_name":r.activity_name,"file_name":r.file_name,"file_size":r.file_size,"mime_type":r.mime_type,"uploaded_at":r.uploaded_at} for r in rows]

@app.post("/api/zed-m1/references",status_code=201)
def upload_zed_m1_reference(data:RecordFileUpload,industry:Annotated[str,Query(min_length=2,max_length=120)],milestone_code:Annotated[str,Query(pattern="^M[123]$")],visit_number:Annotated[int,Query(ge=1,le=20)],activity_name:Annotated[str,Query(min_length=2,max_length=240)],db:DB,user:Annotated[User,Depends(require(Role.ADMIN))]):
    if data.mime_type!="application/vnd.openxmlformats-officedocument.wordprocessingml.document" or not data.file_name.lower().endswith(".docx"):
        raise HTTPException(422,"Industry reference templates must be Word (.docx) files so consultants can open them as Google Docs.")
    try: key,size=_save_encoded_file(data.file_name,data.mime_type,data.content_base64)
    except HTTPException: raise
    for prior in db.scalars(select(ZedReferenceDocument).where(ZedReferenceDocument.industry==industry,ZedReferenceDocument.milestone_code==milestone_code,ZedReferenceDocument.visit_number==visit_number,ZedReferenceDocument.activity_name==activity_name,ZedReferenceDocument.active.is_(True))):
        prior.active=False
    row=ZedReferenceDocument(industry=industry,milestone_code=milestone_code,visit_number=visit_number,activity_name=activity_name,file_name=data.file_name,storage_key=key,file_size=size,mime_type=data.mime_type,uploaded_by=user.id)
    db.add(row);db.commit();db.refresh(row)
    return {"id":row.id,"industry":row.industry,"milestone_code":row.milestone_code,"visit_number":row.visit_number,"activity_name":row.activity_name,"file_name":row.file_name,"file_size":row.file_size,"mime_type":row.mime_type,"uploaded_at":row.uploaded_at}

@app.delete("/api/zed-m1/references/{reference_id}")
def archive_zed_m1_reference(reference_id:int,db:DB,user:Annotated[User,Depends(require(Role.ADMIN))]):
    row=db.get(ZedReferenceDocument,reference_id)
    if not row: raise HTTPException(404,"Reference document not found.")
    row.active=False;db.commit();return {"success":True}

@app.get("/api/zed-m1/references/{reference_id}/download")
def download_zed_m1_reference(reference_id:int,db:DB,user:Annotated[User,Depends(current_user)]):
    row=db.get(ZedReferenceDocument,reference_id)
    if not row or not row.active: raise HTTPException(404,"Reference document not found.")
    return _download_attachment(row.file_name,row.mime_type,row.storage_key)

@app.get("/api/my/tasks")
def list_my_job_tasks(db:DB,user:Annotated[User,Depends(require(Role.END_USER))]):
    """Return only milestone activities assigned to the authenticated consultant."""
    rows=db.execute(select(JobActivity,JobCard.number,JobCard.location,Milestone.code,Milestone.name)
        .join(JobCard,JobCard.id==JobActivity.job_card_id)
        .join(Milestone,Milestone.id==JobActivity.milestone_id)
        .where(JobActivity.assigned_user_id==user.id,JobCard.deleted_at.is_(None))
        .order_by(JobActivity.status,JobActivity.end_date,JobActivity.id.desc()).limit(250)).all()
    return [{**_job_activity_out(db,activity).model_dump(mode="json"),"job_card_number":number,"job_card_location":location,"milestone_code":milestone_code,"milestone_name":milestone_name}
        for activity,number,location,milestone_code,milestone_name in rows]

@app.post("/api/job-cards/{card_id}/activities",response_model=JobActivityOut,status_code=201)
def create_job_activity(card_id:int,data:JobActivityCreate,db:DB,user:Annotated[User,Depends(require(Role.ADMIN,Role.END_USER))]):
    card=mutable_card(assigned_card(db,user,card_id));milestone=db.scalar(select(Milestone).where(Milestone.id==data.milestone_id,Milestone.job_card_id==card_id))
    if not milestone:raise HTTPException(422,"Choose a milestone belonging to this Job Card.")
    _require_work_payment(db,card,milestone)
    kind=db.scalar(select(ActivityType).where(ActivityType.id==data.activity_type_id,ActivityType.active.is_(True)))
    if not kind:raise HTTPException(422,"Choose an active activity type.")
    assignee_id=data.assigned_user_id if user.role==Role.ADMIN and data.assigned_user_id else user.id
    if user.role==Role.ADMIN and data.assigned_user_id is None:raise HTTPException(422,"Admin must select an assigned consultant.")
    if assignee_id not in _milestone_consultant_ids(db,milestone): raise HTTPException(422,"Assign activities to a consultant allocated to this milestone.")
    if not db.scalar(select(Assignment.id).where(Assignment.job_card_id==card_id,Assignment.user_id==assignee_id,Assignment.active.is_(True))):raise HTTPException(422,"The assigned user must be an active member of this Job Card team.")
    if data.visit_id and not db.scalar(select(Visit.id).where(Visit.id==data.visit_id,Visit.job_card_id==card_id,Visit.milestone_id==milestone.id)):raise HTTPException(422,"The selected visit must belong to this Job Card milestone.")
    if data.status=="COMPLETED" and data.duration_minutes==0:raise HTTPException(422,"Record the activity duration before completing it.")
    row=JobActivity(job_card_id=card_id,milestone_id=milestone.id,visit_id=data.visit_id,activity_type_id=kind.id,name=data.name,description=data.description,assigned_user_id=assignee_id,start_date=data.start_date,end_date=data.end_date,duration_minutes=data.duration_minutes,status=data.status,review_status="SUBMITTED" if data.status=="COMPLETED" else "NOT_SUBMITTED",remarks=data.remarks,created_by=user.id)
    db.add(row);db.flush();audit(db,user.id,"ACTIVITY_CREATED","JOB_ACTIVITY",row.id,f"{card.number}; {kind.code}; {row.name}")
    if assignee_id!=user.id:notify(db,assignee_id,f"Activity assigned on Job Card {card.number}: {row.name}.",f"/user/job-cards?card={card_id}")
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
    activity_documents=_activity_record_documents(db,row)
    latest_documents_by_type={}
    for document in activity_documents:
        if document.status in ("SUBMITTED","UNDER_REVIEW","APPROVED"):
            latest_documents_by_type.setdefault(document.document_type,document)
    reviewable_documents=list(latest_documents_by_type.values())
    if updates.get("status")=="COMPLETED":
        milestone=db.get(Milestone,row.milestone_id);_require_work_payment(db,card,milestone)
        if duration==0 and row.planned_visit_id is None:raise HTTPException(422,"Record the activity duration before completing it.")
        has_activity_evidence=db.scalar(select(JobActivityAttachment.id).where(JobActivityAttachment.activity_id==row.id)) is not None
        if row.planned_visit_id and not (has_activity_evidence or reviewable_documents):
            raise HTTPException(422,"Upload the activity deliverable for Admin review before completing this task.")
    if updates.get("status")=="IN_PROGRESS" and row.planned_visit_id:
        _ensure_activity_draft_document(db,row,user)
    previous=row.status
    for field,value in updates.items():setattr(row,field,value)
    if updates.get("status")=="COMPLETED":
        row.review_status="SUBMITTED";row.review_remarks=None;row.reviewed_by=None;row.reviewed_at=None
        pending_files=list(db.scalars(select(JobActivityAttachment).where(JobActivityAttachment.activity_id==row.id,JobActivityAttachment.status=="DRAFT")))
        for attachment in pending_files: attachment.status="SUBMITTED"
        if pending_files or reviewable_documents:
            for admin in db.scalars(select(User).where(User.role==Role.ADMIN,User.status==AccountStatus.ACTIVE)):
                notify(db,admin.id,f"{row.name} and its deliverable were submitted for review on Job Card {card.number}. Please review them within 24 hours.",f"/admin/documents")
        if row.planned_visit_id:
            planned=db.get(PlannedVisit,row.planned_visit_id)
            milestone=db.get(Milestone,row.milestone_id)
            submitted_visit=db.scalar(select(Visit.id).where(Visit.planned_visit_id==planned.id,Visit.deleted_at.is_(None),Visit.status.in_(("SUBMITTED","APPROVED"))).limit(1)) if planned else None
            if planned and milestone and submitted_visit and milestone_visit_deliverables(milestone,planned.visit_number,card) and visit_deliverables_complete(db,milestone,planned.id):
                planned.status="COMPLETED"
                planned.outcome=planned.outcome or "Visit report submitted and all configured milestone tasks completed."
                audit(db,user.id,"VISIT_TASKS_COMPLETED","PLANNED_VISIT",planned.id,f"All required tasks completed for visit plan {planned.id}.")
                notify(db,card.created_by,f"All required tasks are complete for visit plan {planned.id} on Job Card {card.number}.",f"/job-cards/{card.id}")
    audit(db,user.id,"ACTIVITY_UPDATED","JOB_ACTIVITY",row.id,f"status {previous}->{row.status}; duration={row.duration_minutes}; remarks={row.remarks or ''}")
    db.commit();db.refresh(row);return _job_activity_out(db,row)

@app.get("/api/activities/review-queue")
def activity_review_queue(db:DB,user:Annotated[User,Depends(require(Role.ADMIN))],page:int=Query(default=1,ge=1),page_size:int=Query(default=25,ge=1,le=100)):
    query=(select(JobActivity,JobCard.number,MSME.name,Milestone.code,User.full_name)
        .join(JobCard,JobCard.id==JobActivity.job_card_id).join(MSME,MSME.id==JobCard.msme_id)
        .join(Milestone,Milestone.id==JobActivity.milestone_id).join(User,User.id==JobActivity.assigned_user_id)
        .where(JobActivity.review_status.in_(("SUBMITTED","UNDER_REVIEW")),JobCard.deleted_at.is_(None)))
    total=int(db.scalar(select(func.count()).select_from(query.subquery())) or 0)
    rows=db.execute(query.order_by(JobActivity.updated_at.desc(),JobActivity.id.desc()).offset((page-1)*page_size).limit(page_size)).all()
    return {"items":[{"id":activity.id,"job_card_id":activity.job_card_id,"job_card_number":number,"msme_name":msme,"milestone_id":activity.milestone_id,"milestone_code":code,"activity_name":activity.name,"status":activity.status,"review_status":activity.review_status,"review_remarks":activity.review_remarks,"duration_minutes":activity.duration_minutes,"consultant_name":consultant,"updated_at":activity.updated_at} for activity,number,msme,code,consultant in rows],"total":total,"page":page,"page_size":page_size}

@app.post("/api/activities/{activity_id}/review")
def review_job_activity(activity_id:int,data:ActivityAttachmentReviewIn,db:DB,user:Annotated[User,Depends(require(Role.ADMIN))]):
    activity=db.scalar(select(JobActivity).where(JobActivity.id==activity_id).with_for_update())
    if not activity:raise HTTPException(404,"Activity not found.")
    card=assigned_card(db,user,activity.job_card_id)
    if activity.review_status not in ("SUBMITTED","UNDER_REVIEW"):raise HTTPException(409,"Only submitted activities can be reviewed.")
    if data.decision=="UNDER_REVIEW":activity.review_status="UNDER_REVIEW"
    else:activity.review_status=data.decision
    activity.review_remarks=data.comment;activity.reviewed_by=user.id;activity.reviewed_at=datetime.now(timezone.utc)
    if data.decision in ("RETURNED","REJECTED"):
        activity.status="IN_PROGRESS"
        if activity.planned_visit_id:
            planned=db.get(PlannedVisit,activity.planned_visit_id)
            if planned:
                planned.status="IN_PROGRESS"
                activity.sla_due_at=datetime.now(timezone.utc)+timedelta(hours=24)
                activity.end_date=activity.sla_due_at.astimezone(ZoneInfo("Asia/Kolkata")).date()
        notify(db,activity.assigned_user_id,f"Activity {activity.name} was {data.decision.lower()}: {data.comment}",f"/user/job-cards?card={card.id}")
    milestone=db.get(Milestone,activity.milestone_id)
    if data.decision=="APPROVED" and milestone and _milestone_admin_review_ready(db,milestone):
        for manager in db.scalars(select(User).where(User.role==Role.MANAGEMENT,User.status==AccountStatus.ACTIVE)):
            notify(db,manager.id,f"Admin approved milestone activities and files for {milestone.code} on Job Card {card.number}. Ready for milestone review.",f"/management/reviews?milestone={milestone.id}")
    if data.decision=="APPROVED" and activity.planned_visit_id:
        planned=db.get(PlannedVisit,activity.planned_visit_id)
        if planned:finish_approved_visit_deliverables(db,planned,user)
    audit(db,user.id,f"ACTIVITY_REVIEW_{data.decision}","JOB_ACTIVITY",activity.id,f"{activity.name}; {data.comment}")
    db.commit();return _job_activity_out(db,activity)

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
    attachment=JobActivityAttachment(activity_id=row.id,file_name=data.file_name,storage_key=key,file_size=size,mime_type=data.mime_type,uploaded_by=user.id,status="DRAFT")
    db.add(attachment);db.flush();audit(db,user.id,"ACTIVITY_ATTACHMENT_UPLOADED","JOB_ACTIVITY",row.id,data.file_name);db.commit();db.refresh(attachment);return attachment

@app.get("/api/activity-attachments/review-queue")
def activity_attachment_review_queue(db:DB,user:Annotated[User,Depends(require(Role.ADMIN))],page:int=Query(default=1,ge=1),page_size:int=Query(default=25,ge=1,le=100)):
    query=(select(JobActivityAttachment,JobActivity,JobCard.number,MSME.name,Milestone.code,User.full_name)
        .join(JobActivity,JobActivity.id==JobActivityAttachment.activity_id)
        .join(JobCard,JobCard.id==JobActivity.job_card_id)
        .join(MSME,MSME.id==JobCard.msme_id)
        .join(Milestone,Milestone.id==JobActivity.milestone_id)
        .join(User,User.id==JobActivityAttachment.uploaded_by)
        .where(JobActivityAttachment.status.in_(("SUBMITTED","UNDER_REVIEW")),JobCard.deleted_at.is_(None)))
    total=int(db.scalar(select(func.count()).select_from(query.subquery())) or 0)
    rows=db.execute(query.order_by(JobActivityAttachment.uploaded_at.desc(),JobActivityAttachment.id.desc()).offset((page-1)*page_size).limit(page_size)).all()
    items=[{"id":attachment.id,"activity_id":activity.id,"job_card_id":activity.job_card_id,"job_card_number":card_number,"msme_name":msme_name,"milestone_code":milestone_code,"activity_name":activity.name,"file_name":attachment.file_name,"file_size":attachment.file_size,"mime_type":attachment.mime_type,"status":attachment.status,"remarks":attachment.review_remarks,"uploaded_by":attachment.uploaded_by,"uploaded_by_name":uploader_name,"uploaded_at":attachment.uploaded_at,"submitted_at":activity.updated_at,"reviewed_by":attachment.reviewed_by,"reviewed_at":attachment.reviewed_at} for attachment,activity,card_number,msme_name,milestone_code,uploader_name in rows]
    return {"items":items,"total":total,"page":page,"page_size":page_size}

@app.post("/api/activity-attachments/{attachment_id}/review")
def review_activity_attachment(attachment_id:int,data:ActivityAttachmentReviewIn,db:DB,user:Annotated[User,Depends(require(Role.ADMIN))]):
    attachment=db.scalar(select(JobActivityAttachment).where(JobActivityAttachment.id==attachment_id).with_for_update())
    if not attachment:raise HTTPException(404,"Activity deliverable not found.")
    activity=db.get(JobActivity,attachment.activity_id)
    if not activity:raise HTTPException(404,"Activity not found.")
    card=assigned_card(db,user,activity.job_card_id)
    if attachment.status not in ("SUBMITTED","UNDER_REVIEW"):raise HTTPException(409,"Only submitted activity deliverables can be reviewed.")
    previous=attachment.status;attachment.status=data.decision;attachment.review_remarks=data.comment;attachment.reviewed_by=user.id;attachment.reviewed_at=datetime.now(timezone.utc)
    if data.decision in ("RETURNED","REJECTED"):
        activity.status="IN_PROGRESS";activity.remarks=data.comment
        activity.review_status=data.decision;activity.review_remarks=data.comment;activity.reviewed_by=user.id;activity.reviewed_at=datetime.now(timezone.utc)
        if activity.planned_visit_id:
            planned=db.get(PlannedVisit,activity.planned_visit_id)
            if planned:
                planned.status="IN_PROGRESS"
                activity.sla_due_at=datetime.now(timezone.utc)+timedelta(hours=24)
                activity.end_date=activity.sla_due_at.astimezone(ZoneInfo("Asia/Kolkata")).date()
        notify(db,activity.assigned_user_id,f"{activity.name} deliverable was {data.decision.lower()}: {data.comment}",f"/user/job-cards?card={card.id}")
    if data.decision=="APPROVED":
        milestone=db.get(Milestone,activity.milestone_id)
        if milestone and _milestone_admin_review_ready(db,milestone):
            for manager in db.scalars(select(User).where(User.role==Role.MANAGEMENT,User.status==AccountStatus.ACTIVE)):
                notify(db,manager.id,f"Admin approved the submitted activities and files for {milestone.code} on Job Card {card.number}. It is ready for your milestone review.",f"/management/reviews?milestone={milestone.id}")
        if activity.planned_visit_id:
            planned=db.get(PlannedVisit,activity.planned_visit_id)
            if planned:finish_approved_visit_deliverables(db,planned,user)
    audit(db,user.id,f"ACTIVITY_DELIVERABLE_{data.decision}","JOB_ACTIVITY",activity.id,f"attachment={attachment.file_name}; status {previous}->{data.decision}; {data.comment}")
    db.commit();db.refresh(attachment);return JobActivityAttachmentOut.model_validate(attachment)

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
    if card.payment_gate_enabled and updates.get("status") in ("ASSIGNED","IN_PROGRESS") and not _m1_payment_confirmed(db,card):
        raise HTTPException(423,"Finance must confirm the client’s M1 payment to QCI before this Job Card can move into work.")
    if updates.get("status") in ("ASSIGNED","IN_PROGRESS"):
        first=db.scalar(select(Milestone).where(Milestone.job_card_id==card.id,Milestone.code=="M1"))
        if card.payment_gate_enabled and (not first or not first.responsible_consultant_id): raise HTTPException(422,"Assign a consultant to M1 before moving this Job Card into work.")
    start=updates.get("project_start_date",card.project_start_date); end=updates.get("project_end_date",card.project_end_date)
    if end<start: raise HTTPException(422,"Project end date cannot be before the start date.")
    if "msme_id" in updates:
        msme=db.get(MSME,updates["msme_id"])
        if not msme or msme.deleted_at or msme.status!="ACTIVE": raise HTTPException(422,"Selected MSME is not active.")
    if updates.get("unit_representative_id") is not None: raise HTTPException(422,"Unit Representative assignment is no longer part of the Job Card workflow.")
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
            role_type={"director_id":"DIRECTOR"}.get(field)
            if role_type:
                for assignment in db.scalars(select(Assignment).where(Assignment.job_card_id==card_id,Assignment.assignment_type==role_type,Assignment.active==True)):
                    assignment.active=False;assignment.ended_at=datetime.now(timezone.utc)
                db.add(Assignment(job_card_id=card.id,user_id=value,assignment_type=role_type,assigned_by=admin.id));notify(db,value,f"You were assigned as {role_type.replace('_',' ').title()} on Job Card {card.number}.",f"/job-cards/{card.id}")
    if changes: audit(db,admin.id,"JOB_CARD_UPDATED","JOB_CARD",card.id,"; ".join(changes))
    db.commit(); db.refresh(card); return card
@app.post("/api/job-cards/{card_id}/reassign",response_model=JobCardOut)
def reassign(card_id:int,data:ReassignmentIn,db:DB,admin:Annotated[User,Depends(require(Role.ADMIN))]):
    assigned_card(db,admin,card_id)
    raise HTTPException(409,"Consultants are assigned milestone by milestone. Open the Job Card and assign the consultant on the relevant paid milestone.")
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
    if user.role in (Role.MARKETING,Role.FINANCE): raise HTTPException(403,"This role cannot access Job Card activities.")
    card=db.get(JobCard,card_id)
    if not card or card.deleted_at: raise HTTPException(404,"Job Card not found.")
    if user.role==Role.END_USER and not db.scalar(select(Assignment).where(Assignment.job_card_id==card_id,Assignment.user_id==user.id,Assignment.active==True)): raise HTTPException(403,"You are not assigned to this Job Card.")
    if user.role==Role.DIRECTOR and card.director_id!=user.id: raise HTTPException(403,"You are not assigned to this Job Card.")
    return card

def mutable_card(card:JobCard):
    if card.status in ("CLOSURE_PENDING","COMPLETED"): raise HTTPException(409,"This Job Card is locked for project closure. An Admin must reopen it before operational changes can be made.")
    return card

@app.post("/api/job-cards/{card_id}/milestones",response_model=MilestoneOut,status_code=201)
def create_milestone(card_id:int,data:MilestoneCreate,db:DB,admin:Annotated[User,Depends(require(Role.ADMIN))]):
    mutable_card(assigned_card(db,admin,card_id))
    if {"amount","payment_percent"}&data.model_fields_set: raise HTTPException(403,"Milestone payment amounts and confirmation are managed by Finance.")
    if "payment_date" in data.model_fields_set: raise HTTPException(403,"System Admin cannot view or change payment confirmation dates.")
    if data.closing_date and data.start_date and data.closing_date<data.start_date: raise HTTPException(422,"Milestone closing date cannot be before its start date.")
    if data.payment_date and data.closing_date and data.payment_date<data.closing_date: raise HTTPException(422,"Milestone payment date cannot be before its closing date.")
    if data.portal_report_submission_date and data.start_date and data.portal_report_submission_date<data.start_date: raise HTTPException(422,"Portal report submission date cannot be before the milestone start date.")
    if db.scalar(select(Milestone).where(Milestone.job_card_id==card_id,Milestone.code==data.code)): raise HTTPException(409,"Milestone code already exists on this Job Card.")
    if data.responsible_consultant_id:
        if db.get(JobCard,card_id).payment_gate_enabled:
            raise HTTPException(423,f"Confirm the client’s {data.code} payment to QCI before allocating this milestone to a consultant.")
        active_consultant(db,data.responsible_consultant_id)
    milestone=Milestone(job_card_id=card_id,**data.model_dump()); db.add(milestone)
    try: db.flush()
    except IntegrityError: db.rollback();raise HTTPException(409,"Milestone code already exists on this Job Card.")
    if milestone.responsible_consultant_id and milestone.planned_mandays:
        allocation=MilestoneConsultantAllocation(milestone_id=milestone.id,consultant_id=milestone.responsible_consultant_id,allocated_mandays=milestone.planned_mandays,assigned_by=admin.id,active=True);db.add(allocation);db.flush()
        _record_milestone_consultant_assignment(db,milestone,milestone.responsible_consultant_id,admin)
    if milestone.code=="M1" and db.scalar(select(ClientMilestonePayment.id).where(ClientMilestonePayment.milestone_id==milestone.id)):
        _start_triggered_workflows(db,"JOB_CARD_CREATED",db.get(JobCard,card_id),admin,milestone)
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
    recipient=document_owner(db,card.id) if stage=="UNIT" else None
    if recipient: notify(db,recipient,f"Milestone {milestone.code} on Job Card {card.number}: {decision.lower()}.{(' '+comment) if comment else ''}",f"/job-cards/{card.id}")
    return row

def _milestone_admin_review_details(db:Session,milestone:Milestone):
    activities=list(db.scalars(select(JobActivity).where(JobActivity.milestone_id==milestone.id,JobActivity.status!="CANCELLED").order_by(JobActivity.id)))
    ready=all(activity.status=="COMPLETED" and activity.review_status=="APPROVED" for activity in activities)
    deliverables=[]
    for activity in activities:
        deliverables.append({"activity_id":activity.id,"activity_name":activity.name,"attachment_id":None,"file_name":None,"status":activity.review_status,"review_remarks":activity.review_remarks})
    latest_by_activity={}
    for activity in activities:
        attachments=list(db.scalars(select(JobActivityAttachment).where(JobActivityAttachment.activity_id==activity.id).order_by(JobActivityAttachment.id.desc())))
        latest=attachments[0] if attachments else None
        latest_by_activity[activity.id]=latest
        if latest:
            deliverables.append({"activity_id":activity.id,"activity_name":activity.name,"attachment_id":latest.id,"file_name":latest.file_name,"status":latest.status,"review_remarks":latest.review_remarks})
            if latest.status!="APPROVED": ready=False
    if milestone.code.upper()=="M1":
        completed_plans=list(db.scalars(select(PlannedVisit).where(PlannedVisit.milestone_id==milestone.id,PlannedVisit.status=="COMPLETED")))
        if not completed_plans:
            ready=False
        for plan in completed_plans:
            plan_visit=db.scalar(select(Visit).where(Visit.planned_visit_id==plan.id,Visit.deleted_at.is_(None)).order_by(Visit.visit_number.desc()).limit(1))
            card=db.get(JobCard,plan.job_card_id)
            required_names=tuple(name for name,_ in milestone_visit_deliverables(milestone,plan_visit.visit_number if plan_visit else plan.visit_number,card))
            for required_name in required_names:
                activity=next((row for row in activities if row.planned_visit_id==plan.id and row.name==required_name),None)
                attachment=latest_by_activity.get(activity.id) if activity else None
                if not activity or activity.status!="COMPLETED" or not attachment or attachment.status!="APPROVED":
                    ready=False
    completed_plans=list(db.scalars(select(PlannedVisit).where(PlannedVisit.milestone_id==milestone.id,PlannedVisit.status=="COMPLETED")))
    for plan in completed_plans:
        visit=db.scalar(select(Visit).where(Visit.planned_visit_id==plan.id,Visit.deleted_at.is_(None),Visit.status.in_(("SUBMITTED","APPROVED"))))
        report=db.scalar(select(RecordDocument).where(RecordDocument.visit_id==visit.id,RecordDocument.document_type=="Consultant Visit Report").order_by(RecordDocument.version.desc(),RecordDocument.id.desc()).limit(1)) if visit else None
        if not report or report.status!="APPROVED": ready=False
    latest_records=list(db.scalars(select(RecordDocument).where(RecordDocument.milestone_id==milestone.id,RecordDocument.payment_id.is_(None)).order_by(RecordDocument.document_type,RecordDocument.version.desc(),RecordDocument.id.desc())))
    seen_types=set()
    for document in latest_records:
        if document.document_type in seen_types: continue
        seen_types.add(document.document_type)
        if document.status not in ("APPROVED","DRAFT"): ready=False
    return ready,deliverables

def _milestone_admin_review_ready(db:Session,milestone:Milestone)->bool:
    return _milestone_admin_review_details(db,milestone)[0]

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
def management_milestone_review_queue(db:DB,user:Annotated[User,Depends(require(Role.MANAGEMENT))]):
    result=[]
    for event in db.scalars(select(MilestoneReviewHistory).where(MilestoneReviewHistory.stage=="CONSULTANT",MilestoneReviewHistory.decision=="SUBMITTED").order_by(MilestoneReviewHistory.created_at)):
        events=milestone_review_events(db,event.milestone_id,event.cycle)
        if any(e.stage=="MANAGEMENT" for e in events): continue
        milestone=db.get(Milestone,event.milestone_id);card=db.get(JobCard,milestone.job_card_id)
        admin_ready,deliverables=_milestone_admin_review_details(db,milestone)
        if not admin_ready: continue
        result.append({"milestone_id":milestone.id,"job_card_id":card.id,"job_card_number":card.number,"code":milestone.code,"name":milestone.name,"planned_visits":milestone.planned_visits,"actual_visits":milestone_summary(db,milestone)["actual_visits"],"cycle":event.cycle,"submitted_at":event.created_at,"admin_approved_deliverables":deliverables})
    return result

@app.post("/api/milestones/{milestone_id}/management-review",response_model=MilestoneReviewOut,status_code=201)
def decide_milestone_management_review(milestone_id:int,data:MilestoneReviewDecision,db:DB,user:Annotated[User,Depends(require(Role.MANAGEMENT))]):
    milestone=db.get(Milestone,milestone_id)
    if not milestone: raise HTTPException(404,"Milestone not found.")
    events=milestone_review_events(db,milestone_id);cycle=max((event.cycle for event in events),default=0);current=[e for e in events if e.cycle==cycle]
    if not any(e.stage=="CONSULTANT" and e.decision=="SUBMITTED" for e in current): raise HTTPException(409,"Milestone has not been submitted for review.")
    if any(e.stage=="MANAGEMENT" for e in current): raise HTTPException(409,"Management has already reviewed this cycle.")
    if not _milestone_admin_review_ready(db,milestone): raise HTTPException(409,"Admin must approve all milestone activities and required deliverables before Management can review this milestone.")
    if data.decision in ("RETURNED","REJECTED") and not (data.comment and data.comment.strip()): raise HTTPException(422,"A reason is required when returning or rejecting a milestone.")
    card=db.get(JobCard,milestone.job_card_id)
    row=milestone_review_record(db,milestone,cycle,"MANAGEMENT",data.decision,data.comment,user)
    if data.decision=="APPROVED":
        milestone.status="COMPLETED"
        next_stage=_next_milestone(db,card.id,milestone)
        if next_stage:
            _start_triggered_workflows(db,"MILESTONE_COMPLETED",card,user,next_stage)
            audit(db,user.id,"NEXT_MILESTONE_PAYMENT_REQUESTED","MILESTONE",next_stage.id,f"{milestone.code} approved by Management; Finance workflow started for {next_stage.code}.")
    db.commit();db.refresh(row);return row

@app.patch("/api/milestones/{milestone_id}",response_model=MilestoneOut)
def update_milestone(milestone_id:int,data:MilestoneUpdate,db:DB,admin:Annotated[User,Depends(require(Role.ADMIN))]):
    milestone=db.get(Milestone,milestone_id)
    if not milestone: raise HTTPException(404,"Milestone not found.")
    card=mutable_card(assigned_card(db,admin,milestone.job_card_id)); updates=data.model_dump(exclude_unset=True)
    zed_fixed_mandays={"M1":Decimal("2.00"),"M2":Decimal("6.00"),"M3":Decimal("6.00")}
    zed_services={part.strip().casefold() for part in (card.service_name or "").split(",")}
    if zed_services.intersection({"zed silver","zed gold"}) and milestone.code in zed_fixed_mandays:
        fixed=zed_fixed_mandays[milestone.code]
        if "planned_mandays" in updates and Decimal(str(updates["planned_mandays"])).quantize(Decimal(".01"))!=fixed:
            raise HTTPException(422,f"{milestone.code} mandays are fixed at {fixed} for ZED Silver and Gold.")
        updates["planned_mandays"]=fixed
    if {"amount","payment_percent"}&updates.keys(): raise HTTPException(403,"Milestone payment amounts and confirmation are managed by Finance.")
    if "payment_date" in updates: raise HTTPException(403,"System Admin cannot view or change payment confirmation dates.")
    start=updates.get("start_date",milestone.start_date); closing=updates.get("closing_date",milestone.closing_date); payment=updates.get("payment_date",milestone.payment_date); portal=updates.get("portal_report_submission_date",milestone.portal_report_submission_date)
    if closing and start and closing<start: raise HTTPException(422,"Milestone closing date cannot be before its start date.")
    if payment and closing and payment<closing: raise HTTPException(422,"Milestone payment date cannot be before its closing date.")
    if portal and start and portal<start: raise HTTPException(422,"Portal report submission date cannot be before the milestone start date.")
    consultant_id=updates.get("responsible_consultant_id",milestone.responsible_consultant_id)
    if consultant_id:
        if consultant_id!=milestone.responsible_consultant_id and not db.scalar(select(ClientMilestonePayment.id).where(ClientMilestonePayment.milestone_id==milestone.id)):
            raise HTTPException(423,f"Confirm the client’s {milestone.code} payment to QCI before allocating this milestone to a consultant.")
        active_consultant(db,consultant_id)
    previous_consultant=milestone.responsible_consultant_id
    if "responsible_consultant_id" in updates and consultant_id!=previous_consultant and milestone_summary(db,milestone)["status"]=="COMPLETED": raise HTTPException(409,"A completed milestone's consultant assignment cannot be changed.")
    active_allocations=list(db.scalars(select(MilestoneConsultantAllocation).where(MilestoneConsultantAllocation.milestone_id==milestone.id,MilestoneConsultantAllocation.active.is_(True))))
    next_planned=updates.get("planned_mandays",milestone.planned_mandays)
    if "planned_mandays" in updates and len(active_allocations)>1 and sum((Decimal(str(row.allocated_mandays)) for row in active_allocations),Decimal(0)).quantize(Decimal(".01"))!=Decimal(str(next_planned)).quantize(Decimal(".01")):
        raise HTTPException(409,"Update the milestone to one consultant before changing its manday total, then allocate the new total across the team.")
    if "planned_mandays" in updates and len(active_allocations)==1: active_allocations[0].allocated_mandays=next_planned
    changes=[]
    for field,value in updates.items():
        previous=getattr(milestone,field)
        if previous!=value: setattr(milestone,field,value); changes.append(f"{field}:{previous}->{value}")
    if "responsible_consultant_id" in updates and consultant_id!=previous_consultant:
        for allocation in active_allocations: allocation.active=False;allocation.ended_at=datetime.now(timezone.utc)
        if consultant_id and milestone.planned_mandays:
            db.add(MilestoneConsultantAllocation(milestone_id=milestone.id,consultant_id=consultant_id,allocated_mandays=milestone.planned_mandays,assigned_by=admin.id,active=True))
    if consultant_id and consultant_id!=previous_consultant:
        _record_milestone_consultant_assignment(db,milestone,consultant_id,admin)
    if previous_consultant and previous_consultant!=consultant_id:
        still_assigned=db.scalar(select(Milestone.id).where(Milestone.job_card_id==milestone.job_card_id,Milestone.id!=milestone.id,Milestone.responsible_consultant_id==previous_consultant))
        if not still_assigned:
            for assignment in db.scalars(select(Assignment).where(Assignment.job_card_id==milestone.job_card_id,Assignment.user_id==previous_consultant,Assignment.assignment_type=="LEAD_CONSULTANT",Assignment.active.is_(True))):
                assignment.active=False;assignment.ended_at=datetime.now(timezone.utc)
    if changes: audit(db,admin.id,"MILESTONE_UPDATED","MILESTONE",milestone.id,"; ".join(changes))
    db.commit();db.refresh(milestone);return _milestone_out_for_user(db,milestone,admin)

@app.put("/api/milestones/{milestone_id}/consultant-allocations",response_model=MilestoneOut)
def allocate_milestone_consultants(milestone_id:int,data:MilestoneConsultantAllocationRequest,db:DB,admin:Annotated[User,Depends(require(Role.ADMIN))]):
    milestone=db.get(Milestone,milestone_id)
    if not milestone: raise HTTPException(404,"Milestone not found.")
    card=mutable_card(assigned_card(db,admin,milestone.job_card_id))
    if milestone_summary(db,milestone)["status"]=="COMPLETED": raise HTTPException(409,"Consultant allocations cannot be changed after the milestone is completed.")
    if not db.scalar(select(ClientMilestonePayment.id).where(ClientMilestonePayment.milestone_id==milestone.id)):
        raise HTTPException(423,f"Finance must confirm the {milestone.code} payment before assigning its consultants.")
    if not milestone.planned_mandays: raise HTTPException(422,"Set planned mandays before assigning consultants.")
    consultant_ids=[item.consultant_id for item in data.allocations]
    if len(consultant_ids)!=len(set(consultant_ids)): raise HTTPException(422,"Each consultant can appear only once in a milestone allocation.")
    allocated_total=sum((item.allocated_mandays for item in data.allocations),Decimal(0))
    planned=Decimal(str(milestone.planned_mandays)).quantize(Decimal("0.01"))
    if allocated_total.quantize(Decimal("0.01"))!=planned:
        raise HTTPException(422,f"Allocated mandays must total the milestone plan of {planned} MD.")
    for consultant_id in consultant_ids: active_consultant(db,consultant_id)
    previous_ids=_milestone_consultant_ids(db,milestone)
    for row in db.scalars(select(MilestoneConsultantAllocation).where(MilestoneConsultantAllocation.milestone_id==milestone.id,MilestoneConsultantAllocation.active.is_(True))):
        row.active=False;row.ended_at=datetime.now(timezone.utc)
    for item in data.allocations:
        db.add(MilestoneConsultantAllocation(milestone_id=milestone.id,consultant_id=item.consultant_id,allocated_mandays=item.allocated_mandays,assigned_by=admin.id,active=True))
    milestone.responsible_consultant_id=data.allocations[0].consultant_id
    db.flush()
    for consultant_id in consultant_ids: _record_milestone_consultant_assignment(db,milestone,consultant_id,admin)
    for consultant_id in previous_ids-set(consultant_ids):
        still_allocated=db.scalar(select(MilestoneConsultantAllocation.id).join(Milestone,Milestone.id==MilestoneConsultantAllocation.milestone_id).where(Milestone.job_card_id==card.id,MilestoneConsultantAllocation.consultant_id==consultant_id,MilestoneConsultantAllocation.active.is_(True)))
        if not still_allocated:
            for assignment in db.scalars(select(Assignment).where(Assignment.job_card_id==card.id,Assignment.user_id==consultant_id,Assignment.assignment_type=="LEAD_CONSULTANT",Assignment.active.is_(True))):
                assignment.active=False;assignment.ended_at=datetime.now(timezone.utc)
    allocation_note=", ".join(f"{item.consultant_id}:{item.allocated_mandays} MD" for item in data.allocations)
    audit(db,admin.id,"MILESTONE_CONSULTANTS_ALLOCATED","MILESTONE",milestone.id,allocation_note)
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
    milestone=db.get(Milestone,data.milestone_id)
    for field,assignment_type in VISIT_TEAM_FIELDS:
        explicit=field in data.model_fields_set
        selected=getattr(data,field) if explicit else (getattr(existing,field) if existing else None)
        if field=="consultant_id" and milestone:
            allowed=_milestone_consultant_ids(db,milestone)
            if explicit and selected is not None and selected not in allowed: raise HTTPException(422,"Visit team members must be allocated to this milestone.")
            if selected is None or selected not in allowed: selected=milestone.responsible_consultant_id
        elif not explicit and not existing:
            selected=db.scalar(select(Assignment.user_id).where(Assignment.job_card_id==card_id,Assignment.assignment_type==assignment_type,Assignment.active==True))
        if selected is not None:
            active=(selected in _milestone_consultant_ids(db,milestone)) if milestone else db.scalar(select(Assignment.id).where(Assignment.job_card_id==card_id,Assignment.assignment_type==assignment_type,Assignment.user_id==selected,Assignment.active==True))
            same_snapshot=existing is not None and selected==getattr(existing,field)
            if milestone and selected not in _milestone_consultant_ids(db,milestone) and not same_snapshot: raise HTTPException(422,"Every consultant on a visit must be allocated to its milestone.")
            if not active and not same_snapshot: raise HTTPException(422,f"The selected {assignment_type.replace('_',' ').title()} is not actively assigned to this Job Card.")
        values[field]=selected
    selected_team=[consultant_id for consultant_id in values.values() if consultant_id is not None]
    if len(selected_team)!=len(set(selected_team)): raise HTTPException(422,"A consultant can be selected only once for a visit.")
    return values

def required_planned_visit(db:Session,user:User,card:JobCard,data:VisitCreate,existing:Visit|None=None)->PlannedVisit:
    if not data.planned_visit_id:
        raise HTTPException(422,"Plan this visit in My Planner before submitting its visit report.")
    planned=db.get(PlannedVisit,data.planned_visit_id)
    if not planned or planned.job_card_id!=card.id or planned.status in ("CANCELLED",):
        raise HTTPException(422,"Select an active planned visit for this Job Card.")
    if planned.milestone_id!=data.milestone_id or planned.visit_date!=data.visit_date:
        raise HTTPException(422,"The visit report milestone and date must match the planned visit. Reschedule the plan first if needed.")
    team={person_id for person_id in (planned.consultant_id,planned.co_consultant_id,planned.subject_expert_id) if person_id is not None}
    if user.id not in team:
        raise HTTPException(403,"Only a consultant assigned to the planned visit can submit its report.")
    duplicate_query=select(Visit.id).where(Visit.planned_visit_id==planned.id,Visit.deleted_at.is_(None))
    if existing is not None: duplicate_query=duplicate_query.where(Visit.id!=existing.id)
    if db.scalar(duplicate_query):
        raise HTTPException(409,"A visit report is already linked to this planned visit.")
    return planned

def planner_conflicts(db:Session,consultant_id:int,visit_date:date,start_time:TimeOfDay,duration:int,exclude_id:int|None=None):
    rows=db.scalars(select(PlannedVisit).where(or_(PlannedVisit.consultant_id==consultant_id,PlannedVisit.co_consultant_id==consultant_id,PlannedVisit.subject_expert_id==consultant_id),PlannedVisit.visit_date==visit_date,PlannedVisit.status.in_(("PLANNED","COMPLETED")),*([PlannedVisit.id!=exclude_id] if exclude_id else [])))
    return [{"id":row.id,"job_card_id":row.job_card_id,"start_time":row.start_time,"expected_duration_minutes":row.expected_duration_minutes,"objective":row.objective} for row in rows]

@app.get("/api/planner/conflicts")
def get_planner_conflicts(db:DB,user:Annotated[User,Depends(require(Role.END_USER))],visit_date:date,start_time:TimeOfDay,duration_minutes:int=Query(gt=0,le=1440),milestone_id:int|None=None,co_consultant_id:int|None=None):
    team=[user.id,*([co_consultant_id] if co_consultant_id else [])]
    if milestone_id:
        milestone=db.get(Milestone,milestone_id);allowed=_milestone_consultant_ids(db,milestone) if milestone else set()
        if user.id not in allowed or any(person_id not in allowed for person_id in team): raise HTTPException(403,"Visit team members must be assigned to the selected milestone.")
    conflicts_by_id={}
    for person_id in team:
        for conflict in planner_conflicts(db,person_id,visit_date,start_time,duration_minutes): conflicts_by_id[conflict["id"]]=conflict
    return {"conflicts":list(conflicts_by_id.values())}

@app.get("/api/planned-visits",response_model=list[PlannedVisitOut])
def list_planned_visits(db:DB,user:Annotated[User,Depends(require(Role.END_USER,Role.ADMIN,Role.MANAGEMENT))],consultant_id:int|None=None,msme_id:int|None=None,job_card_id:int|None=None,milestone_id:int|None=None,start_date:date|None=None,end_date:date|None=None):
    query=select(PlannedVisit,JobCard.number,MSME.name,Milestone.code,Milestone.name,User.full_name).join(JobCard,JobCard.id==PlannedVisit.job_card_id).join(MSME,MSME.id==JobCard.msme_id).join(Milestone,Milestone.id==PlannedVisit.milestone_id).join(User,User.id==PlannedVisit.consultant_id).where(JobCard.deleted_at.is_(None))
    if user.role==Role.END_USER: query=query.where(or_(PlannedVisit.consultant_id==user.id,PlannedVisit.co_consultant_id==user.id,PlannedVisit.subject_expert_id==user.id))
    elif consultant_id: query=query.where(or_(PlannedVisit.consultant_id==consultant_id,PlannedVisit.co_consultant_id==consultant_id,PlannedVisit.subject_expert_id==consultant_id))
    if msme_id: query=query.where(JobCard.msme_id==msme_id)
    if job_card_id: query=query.where(PlannedVisit.job_card_id==job_card_id)
    if milestone_id: query=query.where(PlannedVisit.milestone_id==milestone_id)
    if start_date: query=query.where(PlannedVisit.visit_date>=start_date)
    if end_date: query=query.where(PlannedVisit.visit_date<=end_date)
    return [{**PlannedVisitOut.model_validate(row).model_dump(),"job_card_number":card_number,"msme_name":msme_name,"milestone_code":milestone_code,"milestone_name":milestone_name,"consultant_name":consultant_name} for row,card_number,msme_name,milestone_code,milestone_name,consultant_name in db.execute(query.order_by(PlannedVisit.visit_date,PlannedVisit.start_time).limit(500))]

@app.get("/api/planned-visits/{visit_id}/attachment")
def download_planned_visit_attachment(visit_id:int,db:DB,user:Annotated[User,Depends(require(Role.END_USER,Role.ADMIN,Role.MANAGEMENT))]):
    row=db.get(PlannedVisit,visit_id)
    if not row or not row.attachment_key or not row.attachment_name or not row.attachment_mime_type: raise HTTPException(404,"Visit evidence not found.")
    assigned_card(db,user,row.job_card_id)
    return _download_attachment(row.attachment_name,row.attachment_mime_type,row.attachment_key)

@app.get("/api/planner/availability")
def planner_daily_availability(visit_date:date,db:DB,user:Annotated[User,Depends(require(Role.ADMIN,Role.MANAGEMENT))]):
    consultants=db.execute(select(User.id,User.full_name).where(User.role==Role.END_USER,User.status==AccountStatus.ACTIVE).order_by(User.full_name)).all()
    planned=db.execute(select(PlannedVisit.consultant_id,PlannedVisit.co_consultant_id,PlannedVisit.subject_expert_id,PlannedVisit.id,PlannedVisit.start_time,PlannedVisit.expected_duration_minutes,JobCard.number,JobCard.location).join(JobCard,JobCard.id==PlannedVisit.job_card_id).where(PlannedVisit.visit_date==visit_date,PlannedVisit.status.in_(("PLANNED","COMPLETED")),JobCard.deleted_at.is_(None)).order_by(PlannedVisit.consultant_id,PlannedVisit.start_time)).all()
    by_consultant:dict[int,list[dict]]={}
    for lead_id,co_id,expert_id,visit_id,start_time,duration,job_card_number,location in planned:
        for consultant_id in {person_id for person_id in (lead_id,co_id,expert_id) if person_id is not None}:
            by_consultant.setdefault(consultant_id,[]).append({"visit_id":visit_id,"start_time":str(start_time),"duration_minutes":duration,"job_card_number":job_card_number,"location":location})
    return {"visit_date":visit_date,"consultants":[{"consultant_id":consultant_id,"consultant_name":name,"available":not bool(by_consultant.get(consultant_id)),"planned_visits":by_consultant.get(consultant_id,[])} for consultant_id,name in consultants]}

@app.get("/api/planner/availability-range")
def planner_range_availability(start_date:date,end_date:date,db:DB,user:Annotated[User,Depends(require(Role.ADMIN,Role.MANAGEMENT))]):
    if end_date<start_date: raise HTTPException(422,"Availability end date cannot be before its start date.")
    if (end_date-start_date).days>730: raise HTTPException(422,"Availability range cannot exceed two years.")
    consultants=db.execute(select(User.id,User.full_name).where(User.role==Role.END_USER,User.status==AccountStatus.ACTIVE).order_by(User.full_name)).all()
    rows=db.execute(select(PlannedVisit.consultant_id,PlannedVisit.co_consultant_id,PlannedVisit.subject_expert_id,PlannedVisit.id,PlannedVisit.visit_date,PlannedVisit.start_time,PlannedVisit.expected_duration_minutes,JobCard.number,JobCard.location).join(JobCard,JobCard.id==PlannedVisit.job_card_id).where(PlannedVisit.visit_date>=start_date,PlannedVisit.visit_date<=end_date,PlannedVisit.status.in_(("PLANNED","COMPLETED")),JobCard.deleted_at.is_(None)).order_by(PlannedVisit.visit_date,PlannedVisit.start_time)).all()
    by_consultant:dict[int,list[dict]]={}
    for lead_id,co_id,expert_id,visit_id,visit_day,start_time,duration,card_number,location in rows:
        for consultant_id in {person_id for person_id in (lead_id,co_id,expert_id) if person_id is not None}:
            by_consultant.setdefault(consultant_id,[]).append({"visit_id":visit_id,"visit_date":visit_day,"start_time":str(start_time),"duration_minutes":duration,"job_card_number":card_number,"location":location})
    return {"start_date":start_date,"end_date":end_date,"consultants":[{"consultant_id":consultant_id,"consultant_name":name,"available":not bool(by_consultant.get(consultant_id)),"planned_visits":by_consultant.get(consultant_id,[])} for consultant_id,name in consultants]}

def planner_notify(db:Session,card:JobCard,message:str):
    recipients=set(db.scalars(select(User.id).where(User.role.in_((Role.ADMIN,Role.MANAGEMENT)),User.status==AccountStatus.ACTIVE)))
    recipients.update(db.scalars(select(Assignment.user_id).where(Assignment.job_card_id==card.id,Assignment.active.is_(True))))
    for recipient in recipients: notify(db,recipient,message,f"/job-cards/{card.id}")

ZED_M1_VISIT_1=(
    ("Parameters 1–14 allocation","Allocate parameters 1 to 14."),
    ("Site exterior and interior photos","Capture site exterior and interior photos."),
    ("Opening meeting","Conduct and record the opening meeting."),
    ("Basic information upload in app","Complete basic information in the ZED app."),
    ("Site tour – 20 photos","Conduct the site tour and upload 20 photos."),
    ("ZED awareness training","Conduct ZED awareness training."),
    ("Baseline Diagnostic Study: Gap Assessment","Complete the baseline diagnostic study and gap assessment."),
)
ZED_M1_VISIT_2=(
    ("Parameters 1–14 handholding action plan","Prepare the handholding action plan against gaps found."),
    ("Technology selection and upgradation recommendations","Recommend technology selection and upgradation."),
    ("Handholding implementation plan training","Train the client on the handholding implementation plan."),
    ("Closing meeting","Conduct and record the closing meeting."),
    ("Final submission from web module","Complete the final submission from the web module."),
)
M1_VISIT_DELIVERABLES=(("Case study","Prepare and submit the M1 case study for this visit."),("Photo","Upload a photo record from this M1 visit."),("Plan","Submit the plan and next steps for this M1 visit."))

def milestone_visit_deliverables(milestone:Milestone,visit_number:int|None=None,card:JobCard|None=None)->tuple[tuple[str,str],...]:
    if milestone.code.upper()=="M1":
        if visit_number in (1,2) and card and (card.service_name or "").upper().startswith("ZED") and "SILVER" in f"{card.service_name} {card.package_code}".upper():
            return ZED_M1_VISIT_1 if visit_number==1 else ZED_M1_VISIT_2
        return M1_VISIT_DELIVERABLES
    configured=milestone.activities if isinstance(milestone.activities,list) else []
    names=list(dict.fromkeys(str(name).strip() for name in configured if str(name).strip()))
    return tuple((name,f"Complete the {name} milestone deliverable for this visit.") for name in names)

def ensure_visit_deliverable_tasks(db:Session,row:PlannedVisit,user:User,visit:Visit|None,submitted_at:datetime)->int:
    milestone=db.get(Milestone,row.milestone_id)
    if not milestone: return 0
    card=db.get(JobCard,row.job_card_id)
    visit_number=visit.visit_number if visit else row.visit_number
    if visit_number and row.visit_number is None: row.visit_number=visit_number
    deliverables=milestone_visit_deliverables(milestone,visit_number,card)
    if not deliverables: return 0
    kind=db.scalar(select(ActivityType).where(ActivityType.code=="DOCUMENTATION"))
    if not kind:
        kind=ActivityType(code="DOCUMENTATION",name="Documentation",active=True);db.add(kind);db.flush()
    due_at=submitted_at+timedelta(hours=24)
    due_date=due_at.astimezone(ZoneInfo("Asia/Kolkata")).date()
    started_date=submitted_at.astimezone(ZoneInfo("Asia/Kolkata")).date()
    for name,description in deliverables:
        task=db.scalar(select(JobActivity).where(JobActivity.planned_visit_id==row.id,JobActivity.name==name))
        if not task:
            task=JobActivity(job_card_id=row.job_card_id,milestone_id=row.milestone_id,visit_id=visit.id if visit else None,planned_visit_id=row.id,activity_type_id=kind.id,name=name,description=description,assigned_user_id=user.id,start_date=started_date,end_date=due_date,sla_due_at=due_at,duration_minutes=0,status="PLANNED",remarks=f"Required {milestone.code} visit deliverable; due within 24 hours of planned visit",created_by=user.id)
            db.add(task)
        else:
            if visit: task.visit_id=visit.id
            task.assigned_user_id=user.id
            task.start_date=started_date
            task.end_date=due_date
            task.sla_due_at=due_at
            if task.status not in ("COMPLETED","CANCELLED"):
                task.remarks=f"Required {milestone.code} visit deliverable; due within 24 hours of visit report submission"
    db.flush()
    return len(deliverables)

def visit_deliverables_complete(db:Session,milestone:Milestone,planned_visit_id:int)->bool:
    planned=db.get(PlannedVisit,planned_visit_id)
    visit=db.scalar(select(Visit).where(Visit.planned_visit_id==planned_visit_id,Visit.deleted_at.is_(None))) if planned else None
    card=db.get(JobCard,planned.job_card_id) if planned else None
    for name,_ in milestone_visit_deliverables(milestone,visit.visit_number if visit else planned.visit_number if planned else None,card):
        task=db.scalar(select(JobActivity).where(JobActivity.planned_visit_id==planned_visit_id,JobActivity.name==name))
        attachment=db.scalar(select(JobActivityAttachment).where(JobActivityAttachment.activity_id==task.id).order_by(JobActivityAttachment.id.desc()).limit(1)) if task else None
        if not task or task.status!="COMPLETED" or task.review_status!="APPROVED" or not attachment or attachment.status!="APPROVED":
            return False
    return True

def finish_approved_visit_deliverables(db:Session,planned:PlannedVisit,actor:User):
    milestone=db.get(Milestone,planned.milestone_id);card=db.get(JobCard,planned.job_card_id)
    if not milestone or not card or planned.status=="COMPLETED":return False
    visit=db.scalar(select(Visit).where(Visit.planned_visit_id==planned.id,Visit.deleted_at.is_(None),Visit.status.in_(("SUBMITTED","APPROVED"))).order_by(Visit.visit_number.desc()).limit(1))
    if not visit or not milestone_visit_deliverables(milestone,visit.visit_number,card) or not visit_deliverables_complete(db,milestone,planned.id):return False
    planned.status="COMPLETED";planned.outcome=planned.outcome or "Visit record submitted and all required activities and evidence approved."
    audit(db,actor.id,"VISIT_DELIVERABLES_APPROVED","PLANNED_VISIT",planned.id,"All required visit activities and evidence were approved.")
    notify(db,visit.created_by,f"All required activities and evidence for visit {visit.visit_number} on Job Card {card.number} were approved. The visit is now valid.",f"/job-cards/{card.id}")
    return True

@app.post("/api/planned-visits",response_model=PlannedVisitOut,status_code=201)
def create_planned_visit(data:PlannedVisitCreate,db:DB,user:Annotated[User,Depends(require(Role.END_USER))]):
    card=mutable_card(assigned_card(db,user,data.job_card_id));milestone=db.get(Milestone,data.milestone_id)
    if not milestone or milestone.job_card_id!=card.id: raise HTTPException(422,"The selected milestone does not belong to this Job Card.")
    _require_work_payment(db,card,milestone)
    allowed=_milestone_consultant_ids(db,milestone)
    if user.id not in allowed: raise HTTPException(403,"You are not assigned to this milestone.")
    team=[user.id,*[person_id for person_id in (data.co_consultant_id,data.subject_expert_id) if person_id is not None]]
    if len(team)!=len(set(team)) or any(person_id not in allowed for person_id in team): raise HTTPException(422,"Every visit team member must be different and assigned to this milestone.")
    if not card.project_start_date<=data.visit_date<=card.project_end_date: raise HTTPException(422,"Planned date must fall within the project dates.")
    conflicts_by_id={}
    for person_id in team:
        for conflict in planner_conflicts(db,person_id,data.visit_date,data.start_time,data.expected_duration_minutes): conflicts_by_id[conflict["id"]]=conflict
    conflicts=list(conflicts_by_id.values())
    if conflicts: raise HTTPException(409,{"message":"This consultant already has a planned visit on this date. Choose another date or an available consultant.","conflicts":conflicts})
    active_plan_count=int(db.scalar(select(func.count(PlannedVisit.id)).where(PlannedVisit.milestone_id==milestone.id,PlannedVisit.status!="CANCELLED")) or 0)
    next_visit_number=int(db.scalar(select(func.coalesce(func.max(Visit.visit_number),0)).where(Visit.milestone_id==milestone.id,Visit.deleted_at.is_(None))) or 0)+1
    fields=data.model_dump(exclude={"allow_conflict"});row=PlannedVisit(**fields,visit_number=max(active_plan_count+1,next_visit_number),consultant_id=user.id,created_by=user.id);db.add(row);db.flush()
    zed_silver_m1=milestone.code.upper()=="M1" and (card.service_name or "").upper().startswith("ZED") and "SILVER" in f"{card.service_name} {card.package_code}".upper()
    if zed_silver_m1: ensure_visit_deliverable_tasks(db,row,user,None,datetime.combine(row.visit_date,row.start_time,tzinfo=ZoneInfo("Asia/Kolkata")))
    audit(db,user.id,"PLANNED_VISIT_CREATED","PLANNED_VISIT",row.id,f"{card.number} visit {row.visit_number} {row.visit_date} {row.start_time}; team={team}");planner_notify(db,card,f"Consultant {user.full_name} planned visit {row.visit_number} for {card.number} on {row.visit_date}. The visit checklist is available on the Job Card visit records.");db.commit();db.refresh(row);return row

@app.patch("/api/planned-visits/{visit_id}",response_model=PlannedVisitOut)
def update_planned_visit(visit_id:int,data:PlannedVisitUpdate,db:DB,user:Annotated[User,Depends(current_user)]):
    row=db.get(PlannedVisit,visit_id)
    if not row or row.status=="CANCELLED": raise HTTPException(404,"Planned visit not found.")
    card=mutable_card(assigned_card(db,user,row.job_card_id))
    if user.role not in (Role.ADMIN,Role.MANAGEMENT) and (user.role!=Role.END_USER or user.id not in {row.consultant_id,row.co_consultant_id,row.subject_expert_id}): raise HTTPException(403,"Only a consultant on this planned visit or authorized Admin/Management can update it.")
    changes=data.model_dump(exclude_unset=True,exclude={"attachment_base64"});reason=changes.pop("reason",None);changes.pop("allow_conflict",None);next_status=changes.get("status",row.status)
    # Only edits to the scheduled visit require a reschedule reason. Actuals,
    # findings, evidence metadata, and completion status belong to the visit
    # report and must not be mistaken for a plan change.
    planned_fields={"visit_date","start_time","expected_duration_minutes","milestone_id","location","objective","co_consultant_id","subject_expert_id"}
    changed_plan=any(getattr(row,key)!=changes[key] for key in planned_fields if key in changes)
    if (changed_plan or next_status=="CANCELLED") and not (reason and reason.strip()): raise HTTPException(422,"A reason is required when rescheduling or cancelling a visit.")
    attachment_values={}
    if any((data.attachment_name,data.attachment_mime_type,data.attachment_base64)):
        if not all((data.attachment_name,data.attachment_mime_type,data.attachment_base64)): raise HTTPException(422,"Evidence file name, type, and content are all required.")
        try: attachment_values={"attachment_name":data.attachment_name,"attachment_mime_type":data.attachment_mime_type,"attachment_key":_save_encoded_file(data.attachment_name,data.attachment_mime_type,data.attachment_base64)[0]}
        except HTTPException: raise
        except Exception: raise HTTPException(422,"The visit evidence file is invalid.")
    if "status" in changes and changes["status"]=="COMPLETED":
        if not all(changes.get(k,getattr(row,k)) is not None for k in ("actual_date","actual_start_time","actual_duration_minutes")): raise HTTPException(422,"Actual date, start time and duration are required to complete a visit.")
        if not (changes.get("outcome",row.outcome) or "").strip(): raise HTTPException(422,"Record visit findings or outcome before completing the planned visit.")
        milestone=db.get(Milestone,row.milestone_id)
        visit_for_plan=db.scalar(select(Visit).where(Visit.planned_visit_id==row.id,Visit.deleted_at.is_(None)).order_by(Visit.visit_number.desc()).limit(1))
        if milestone and milestone_visit_deliverables(milestone,visit_for_plan.visit_number if visit_for_plan else None,card):
            visit_report=db.scalar(select(Visit).where(Visit.planned_visit_id==row.id,Visit.status.in_(("SUBMITTED","REVIEWED","APPROVED")),Visit.deleted_at.is_(None)))
            if not visit_report: raise HTTPException(409,"Submit the visit report first. Configured milestone tasks are created after report submission.")
            if not visit_deliverables_complete(db,milestone,row.id):
                raise HTTPException(409,"Complete all configured milestone visit tasks and attach a file to each before completing the planned visit.")
        elif not (attachment_values or row.attachment_key): raise HTTPException(422,"Attach visit evidence before completing the visit.")
        _require_work_payment(db,card,milestone)
    target_date=changes.get("visit_date",row.visit_date);target_time=changes.get("start_time",row.start_time);target_duration=changes.get("expected_duration_minutes",row.expected_duration_minutes)
    if not card.project_start_date<=target_date<=card.project_end_date: raise HTTPException(422,"Planned date must fall within the project dates.")
    milestone=db.get(Milestone,changes.get("milestone_id",row.milestone_id));team=[row.consultant_id,changes.get("co_consultant_id",row.co_consultant_id),changes.get("subject_expert_id",row.subject_expert_id)]
    team=[person_id for person_id in team if person_id is not None]
    if not milestone or milestone.job_card_id!=card.id or len(team)!=len(set(team)) or any(person_id not in _milestone_consultant_ids(db,milestone) for person_id in team): raise HTTPException(422,"Every visit team member must be different and assigned to this milestone.")
    if next_status=="PLANNED":
        conflicts_by_id={}
        for person_id in team:
            for conflict in planner_conflicts(db,person_id,target_date,target_time,target_duration,row.id): conflicts_by_id[conflict["id"]]=conflict
        conflicts=list(conflicts_by_id.values())
        if conflicts: raise HTTPException(409,{"message":"A member of this visit team already has a planned visit on this date. Choose another date or an available consultant.","conflicts":conflicts})
    for key,value in changes.items(): setattr(row,key,value)
    for key,value in attachment_values.items(): setattr(row,key,value)
    linked_tasks=list(db.scalars(select(JobActivity).where(JobActivity.planned_visit_id==row.id)))
    if "visit_date" in changes:
        for task in linked_tasks: task.start_date=row.visit_date;task.end_date=row.visit_date+timedelta(days=1)
    if next_status=="CANCELLED":
        for task in linked_tasks:
            if task.status not in ("COMPLETED","CANCELLED"): task.status="CANCELLED"
    if reason: row.change_reason=reason
    audit(db,user.id,"PLANNED_VISIT_UPDATED","PLANNED_VISIT",row.id,f"status={row.status}; reason={reason or 'details updated'}")
    planner_notify(db,card,f"The planned visit for {card.number} on {row.visit_date} is now {row.status.lower()}. {reason or ''}".strip());db.commit();db.refresh(row);return row

@app.post("/api/job-cards/{card_id}/visits/draft",response_model=VisitOut,status_code=201)
def save_draft(card_id:int,data:VisitCreate,db:DB,user:Annotated[User,Depends(require(Role.END_USER))]):
    card=mutable_card(assigned_card(db,user,card_id)); milestone=db.get(Milestone,data.milestone_id)
    if not milestone or milestone.job_card_id!=card_id: raise HTTPException(422,"The selected milestone is not available for this Job Card.")
    if not card.project_start_date<=data.visit_date<=card.project_end_date: raise HTTPException(422,"Visit date must fall within the project dates.")
    planned=required_planned_visit(db,user,card,data)
    team={"consultant_id":planned.consultant_id,"co_consultant_id":planned.co_consultant_id,"subject_expert_id":planned.subject_expert_id}
    _require_work_payment(db,card,milestone)
    if user.id not in _milestone_consultant_ids(db,milestone): raise HTTPException(403,"You are not assigned to this milestone.")
    attachment={}
    if any((data.attachment_name,data.attachment_mime_type,data.attachment_base64)):
        if not all((data.attachment_name,data.attachment_mime_type,data.attachment_base64)): raise HTTPException(422,"Visit attachment name, type, and content are all required.")
        try: attachment={"attachment_name":data.attachment_name,"attachment_mime_type":data.attachment_mime_type,"attachment_key":save_file(data.attachment_name,data.attachment_mime_type,_decode_encoded_file(data.attachment_base64))}
        except HTTPException: raise
        except Exception: raise HTTPException(422,"The visit attachment is invalid.")
    next_number=int(db.scalar(select(func.coalesce(func.max(Visit.visit_number),0)+1).where(Visit.job_card_id==card_id)) or 1)

@app.patch("/api/visits/{visit_id}",response_model=VisitOut)
def edit_visit(visit_id:int,data:VisitUpdate,db:DB,user:Annotated[User,Depends(require(Role.END_USER))]):
    visit=db.get(Visit,visit_id)
    if not visit or visit.created_by!=user.id or visit.status not in ("DRAFT","RETURNED","REJECTED"): raise HTTPException(409,"Only your draft or returned visits can be edited.")
    card=mutable_card(assigned_card(db,user,visit.job_card_id)); milestone=db.get(Milestone,data.milestone_id)
    if not milestone or milestone.job_card_id!=card.id or not card.project_start_date<=data.visit_date<=card.project_end_date: raise HTTPException(422,"Invalid milestone or visit date.")
    _require_work_payment(db,card,milestone)
    if user.id not in _milestone_consultant_ids(db,milestone): raise HTTPException(403,"You are not assigned to this milestone.")
    planned=required_planned_visit(db,user,card,data,visit)
    if visit.planned_visit_id!=planned.id: raise HTTPException(422,"A visit report must remain linked to its original planned visit.")
    old={"milestone_id":visit.milestone_id,"visit_date":str(visit.visit_date),"working_minutes":visit.working_minutes,"rating_code":visit.rating_code,"location":visit.location,**{field:getattr(visit,field) for field,_ in VISIT_TEAM_FIELDS}};work_details_changed=visit.work_details!=data.work_details;team={"consultant_id":planned.consultant_id,"co_consultant_id":planned.co_consultant_id,"subject_expert_id":planned.subject_expert_id}
    visit.milestone_id=data.milestone_id; visit.visit_date=data.visit_date;visit.start_time=data.start_time;visit.end_time=data.end_time;visit.location=data.location or card.location;visit.findings=data.findings; visit.working_minutes=visit_duration(data); visit.work_details=data.work_details; visit.rating_code=data.rating_code; visit.rating_description=RATINGS[data.rating_code]; visit.remarks=data.remarks
    visit.planned_visit_id=planned.id
    for field,value in team.items(): setattr(visit,field,value)
    if any((data.attachment_name,data.attachment_mime_type,data.attachment_base64)):
        if not all((data.attachment_name,data.attachment_mime_type,data.attachment_base64)): raise HTTPException(422,"Visit attachment name, type, and content are all required.")
        try: visit.attachment_name=data.attachment_name;visit.attachment_mime_type=data.attachment_mime_type;visit.attachment_key=save_file(data.attachment_name,data.attachment_mime_type,_decode_encoded_file(data.attachment_base64))
        except HTTPException: raise
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
    milestone=db.get(Milestone,visit.milestone_id)
    _require_work_payment(db,card,milestone)
    planned=db.get(PlannedVisit,visit.planned_visit_id) if visit.planned_visit_id else None
    if not planned or planned.job_card_id!=card.id or planned.milestone_id!=visit.milestone_id or planned.visit_date!=visit.visit_date or planned.status=="CANCELLED":
        raise HTTPException(422,"A valid visit plan for this Job Card, milestone, and date is required before submission.")
    planned_team={person_id for person_id in (planned.consultant_id,planned.co_consultant_id,planned.subject_expert_id) if person_id is not None}
    if user.id not in planned_team: raise HTTPException(403,"Only a consultant assigned to the planned visit can submit its report.")
    if (not milestone or milestone.code.upper()!="M1") and not visit.attachment_key:
        raise HTTPException(422,"Attach a visit report, photo, or other supporting evidence before submission.")
    submitted_at=datetime.now(timezone.utc)
    visit.status="SUBMITTED"
    deliverables=milestone_visit_deliverables(milestone,visit.visit_number,card) if milestone else ()
    planned.status="IN_PROGRESS" if deliverables else "COMPLETED"
    planned.actual_date=visit.visit_date
    planned.actual_start_time=visit.start_time or planned.start_time
    planned.actual_duration_minutes=visit.working_minutes
    planned.outcome=(visit.findings or visit.work_details).strip()
    if visit.attachment_key:
        planned.attachment_key=visit.attachment_key
        planned.attachment_name=visit.attachment_name
        planned.attachment_mime_type=visit.attachment_mime_type
    card.status="IN_PROGRESS"
    audit(db,user.id,"VISIT_SUBMITTED","VISIT",visit.id,f"Visit {visit.visit_number}; planned visit {planned.id}")
    if deliverables:
        task_count=ensure_visit_deliverable_tasks(db,planned,user,visit,submitted_at)
        due_at=submitted_at+timedelta(hours=24)
        if visit_deliverables_complete(db,milestone,planned.id):
            planned.status="COMPLETED"
        due_label=due_at.astimezone(ZoneInfo("Asia/Kolkata")).strftime("%d-%m-%Y %H:%M")
        notify(db,user.id,f"Visit {visit.visit_number} was submitted. Complete and submit its {task_count} {milestone.code} activities by {due_label}.",f"/user/job-cards?card={card.id}")
        notify(db,card.created_by,f"Visit {visit.visit_number} was submitted for Job Card {card.number}; {task_count} {milestone.code} tasks are due within 24 hours.",f"/job-cards/{card.id}")
    else:
        audit(db,user.id,"PLANNED_VISIT_COMPLETED","PLANNED_VISIT",planned.id,f"Linked visit report {visit.visit_number}")
        notify(db,card.created_by,f"Visit {visit.visit_number} was submitted and linked to its planned visit for Job Card {card.number}.",f"/job-cards/{card.id}")
    db.commit(); db.refresh(visit); return visit

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
    planned=db.get(PlannedVisit,visit.planned_visit_id) if visit.planned_visit_id else None
    if not planned or planned.job_card_id!=card.id or planned.milestone_id!=visit.milestone_id or planned.visit_date!=visit.visit_date or planned.status=="CANCELLED": raise HTTPException(422,"The visit report must remain linked to its original valid visit plan.")
    if user.id not in {person_id for person_id in (planned.consultant_id,planned.co_consultant_id,planned.subject_expert_id) if person_id is not None}: raise HTTPException(403,"Only a consultant assigned to the planned visit can resubmit its report.")
    milestone=db.get(Milestone,visit.milestone_id)
    if milestone and milestone.code.upper()!="M1" and not visit.attachment_key: raise HTTPException(422,"Attach a visit report, photo, or other supporting evidence before resubmission.")
    visit.status="SUBMITTED"; visit.rejection_reason=None; visit.rejected_by=None; visit.rejected_at=None; card.status="IN_PROGRESS"; audit(db,user.id,"VISIT_RESUBMITTED","VISIT",visit.id); notify(db,card.created_by,f"Visit {visit.visit_number} was resubmitted for Job Card {card.number}.",f"/job-cards/{card.id}"); db.commit(); db.refresh(visit); return visit
@app.delete("/api/visits/{visit_id}",status_code=204)
def archive_visit(visit_id:int,db:DB,user:Annotated[User,Depends(require(Role.END_USER))]):
    visit=db.get(Visit,visit_id)
    if not visit or visit.created_by!=user.id or visit.status!="DRAFT": raise HTTPException(404,"Draft visit not found.")
    mutable_card(assigned_card(db,user,visit.job_card_id))
    visit.deleted_at=datetime.now(timezone.utc); visit.deleted_by=user.id; audit(db,user.id,"VISIT_ARCHIVED","VISIT",visit.id); db.commit()

@app.get("/api/job-cards/{card_id}/mandays")
def get_mandays(card_id:int,db:DB,user:Annotated[User,Depends(current_user)]): return manday_summary(db,assigned_card(db,user,card_id))

def _work_product_file(data:WorkProductDraftIn)->tuple[str,str,bytes,str]:
    safe_title=re.sub(r"[^A-Za-z0-9._ -]","",data.title).strip()[:100] or "Draft"
    payload={"kind":data.kind,"title":data.title,"body":data.body,"rows":data.rows}
    if data.kind=="SHEET":
        rows=data.rows[:300]
        width=max((len(row) for row in rows),default=1)
        letters=[]
        for n in range(width):
            value=n+1;label=""
            while value: value,rem=divmod(value-1,26);label=chr(65+rem)+label
            letters.append(label)
        items=[{letters[i]:str(cell)[:20000] if i<len(row) else "" for i,cell in enumerate(row[:width])} for row in rows] or [{"A":""}]
        return safe_title+".xlsx","application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",to_xlsx(items),json.dumps(payload,ensure_ascii=False)
    paragraphs=[f'<w:p><w:r><w:t>{xml_escape(data.title)}</w:t></w:r></w:p>']
    paragraphs.extend(f'<w:p><w:r><w:t xml:space="preserve">{xml_escape(line)}</w:t></w:r></w:p>' for line in data.body.splitlines())
    content_types='<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'
    document='<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'+''.join(paragraphs)+'<w:sectPr/></w:body></w:document>'
    buffer=BytesIO()
    with ZipFile(buffer,"w",ZIP_DEFLATED) as package:
        package.writestr("[Content_Types].xml",content_types)
        package.writestr("_rels/.rels",'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        package.writestr("word/document.xml",document)
    return safe_title+".docx","application/vnd.openxmlformats-officedocument.wordprocessingml.document",buffer.getvalue(),json.dumps(payload,ensure_ascii=False)

def _save_native_work_product(db:Session,document:RecordDocument,data:WorkProductDraftIn,user:User):
    card=mutable_card(assigned_card(db,user,document.owner_job_card_id or document.job_card_id))
    _require_work_payment(db,card)
    payload={"kind":data.kind,"title":data.title.strip(),"body":data.body,"rows":data.rows,"snapshot":data.snapshot}
    document.remarks=json.dumps(payload,ensure_ascii=False)
    document.editor_provider="PORTAL"
    db.commit();db.refresh(document)
    return document

@app.post("/api/job-cards/{card_id}/native-work-products",response_model=RecordDocumentOut,status_code=201)
def create_native_work_product(card_id:int,data:WorkProductDraftIn,db:DB,user:Annotated[User,Depends(require(Role.END_USER))]):
    card=mutable_card(assigned_card(db,user,card_id));_require_work_payment(db,card)
    filename,mime,content,_=_work_product_file(data)
    try: storage_key=save_file(filename,mime,content)
    except ValueError as exc: raise HTTPException(422,str(exc)) from None
    label="Document" if data.kind=="DOCUMENT" else "Spreadsheet"
    document_type=f"Portal {label}: {data.title.strip()}"[:80]
    prior=int(db.scalar(select(func.coalesce(func.max(RecordDocument.version),0)).where(RecordDocument.job_card_id==card.id,RecordDocument.document_type==document_type)) or 0)
    payload={"kind":data.kind,"title":data.title.strip(),"body":data.body,"rows":data.rows,"snapshot":data.snapshot}
    document=RecordDocument(owner_job_card_id=card.id,job_card_id=card.id,document_type=document_type,file_name=filename,storage_key=storage_key,file_size=len(content),mime_type=mime,version=prior+1,status="DRAFT",remarks=json.dumps(payload,ensure_ascii=False),uploaded_by=user.id,editor_provider="PORTAL")
    db.add(document);db.flush();audit(db,user.id,"PORTAL_WORK_PRODUCT_CREATED","DOCUMENT",document.id,document_type);db.commit();db.refresh(document)
    return document

@app.get("/api/record-documents/{document_id}/editor-content")
def get_native_work_product(document_id:int,db:DB,user:Annotated[User,Depends(require(Role.END_USER))]):
    document=db.get(RecordDocument,document_id)
    if not document or document.uploaded_by!=user.id or document.editor_provider!="PORTAL" or document.status not in ("DRAFT","RETURNED","REJECTED"):
        raise HTTPException(404,"Editable portal document not found.")
    return json.loads(document.remarks or "{}")

@app.post("/api/record-documents/{document_id}/native-revision",response_model=RecordDocumentOut,status_code=201)
def create_native_work_product_revision(document_id:int,db:DB,user:Annotated[User,Depends(require(Role.END_USER))]):
    prior=db.get(RecordDocument,document_id)
    if not prior or prior.uploaded_by!=user.id or prior.editor_provider!="PORTAL" or prior.status not in ("RETURNED","REJECTED"): raise HTTPException(404,"Returned or rejected portal document not found.")
    card=mutable_card(assigned_card(db,user,prior.owner_job_card_id or prior.job_card_id));_require_work_payment(db,card)
    path=STORAGE_ROOT/prior.storage_key
    if not path.is_file(): raise HTTPException(404,"Saved draft file is missing.")
    try: key=save_file(prior.file_name,prior.mime_type,path.read_bytes())
    except ValueError as exc: raise HTTPException(422,str(exc)) from None
    columns=("ticket_id","enquiry_id","job_card_id","milestone_id","action_id","visit_id","payment_id","milestone_review_id","visit_review_id","approval_id")
    relations={name:getattr(prior,name) for name in columns}
    revision=RecordDocument(owner_job_card_id=prior.owner_job_card_id,**relations,activity_id=prior.activity_id,document_type=prior.document_type,file_name=prior.file_name,storage_key=key,file_size=prior.file_size,mime_type=prior.mime_type,editor_provider="PORTAL",version=prior.version+1,status="DRAFT",remarks=prior.remarks,uploaded_by=user.id)
    db.add(revision);db.flush();audit(db,user.id,"PORTAL_WORK_PRODUCT_REVISION_STARTED","DOCUMENT",revision.id,f"Revision of returned document {prior.id}");db.commit();db.refresh(revision)
    return revision

@app.put("/api/record-documents/{document_id}/editor-content",response_model=RecordDocumentOut)
def update_native_work_product(document_id:int,data:WorkProductDraftIn,db:DB,user:Annotated[User,Depends(require(Role.END_USER))]):
    document=db.get(RecordDocument,document_id)
    if not document or document.uploaded_by!=user.id or document.editor_provider!="PORTAL" or document.status not in ("DRAFT","RETURNED","REJECTED"):
        raise HTTPException(404,"Editable portal document not found.")
    if document.status in ("RETURNED","REJECTED"): raise HTTPException(409,"Create a revised version before editing this returned submission.")
    return _save_native_work_product(db,document,data,user)

@app.delete("/api/record-documents/{document_id}/editor-content",status_code=204)
def delete_native_work_product(document_id:int,db:DB,user:Annotated[User,Depends(require(Role.END_USER))]):
    document=db.get(RecordDocument,document_id)
    if not document or document.uploaded_by!=user.id or document.editor_provider!="PORTAL" or document.status!="DRAFT":
        raise HTTPException(404,"Editable portal draft not found.")
    path=STORAGE_ROOT/document.storage_key
    db.delete(document);audit(db,user.id,"PORTAL_WORK_PRODUCT_DELETED","DOCUMENT",document_id,"Draft deleted by its author.");db.commit()
    if path.is_file(): path.unlink(missing_ok=True)
    return Response(status_code=204)

def _finalize_native_work_product(document:RecordDocument):
    try: payload=json.loads(document.remarks or "{}")
    except ValueError: raise HTTPException(409,"The saved editor content could not be read.") from None
    data=WorkProductDraftIn(kind=payload.get("kind","DOCUMENT"),title=payload.get("title") or document.file_name,body=payload.get("body", ""),rows=payload.get("rows",[]),snapshot=payload.get("snapshot"))
    if data.kind=="DOCUMENT" and data.snapshot:
        data.body=data.body or _document_snapshot_text(data.snapshot)
    if data.kind=="SHEET" and data.snapshot:
        data.rows=data.rows or _sheet_snapshot_rows(data.snapshot)
    filename,mime,content,_=_work_product_file(data)
    try: new_key=save_file(filename,mime,content)
    except ValueError as exc: raise HTTPException(422,str(exc)) from None
    old_key=document.storage_key;document.storage_key=new_key;document.file_name=filename;document.file_size=len(content);document.mime_type=mime
    return old_key

def _document_snapshot_text(value:dict)->str:
    chunks=[]
    def walk(item):
        if isinstance(item,dict):
            stream=item.get("dataStream")
            if isinstance(stream,str): chunks.append(stream.replace("\r","\n"))
            else:
                for child in item.values(): walk(child)
        elif isinstance(item,list):
            for child in item: walk(child)
    walk(value)
    text="\n".join(chunks).replace("\n\n","\n").strip()
    return "".join(ch for ch in text if ch in "\t\n\r" or ord(ch)>=32)

def _sheet_snapshot_rows(value:dict)->list[list[str]]:
    sheets=value.get("sheets") or value.get("sheetOrder")
    if isinstance(sheets,list): return []
    if not isinstance(sheets,dict): return []
    sheet=next(iter(sheets.values()),{})
    cells=sheet.get("cellData",{}) if isinstance(sheet,dict) else {}
    if not isinstance(cells,dict): return []
    max_row=max((int(r) for r in cells if str(r).isdigit()),default=-1)
    max_col=max((int(c) for r in cells.values() if isinstance(r,dict) for c in r if str(c).isdigit()),default=-1)
    if max_row>299 or max_col>49: raise HTTPException(413,"The spreadsheet exceeds the 300-row or 50-column export limit.")
    return [[str((cells.get(str(r),{}).get(str(c),{}) or {}).get("f") or (cells.get(str(r),{}).get(str(c),{}) or {}).get("v") or "") for c in range(max_col+1)] for r in range(max_row+1)]

@app.get("/api/google-workspace/status")
def google_workspace_status(db:DB,response:Response,user:Annotated[User,Depends(require(Role.ADMIN,Role.END_USER))]):
    response.headers["Cache-Control"]="no-store, no-cache, must-revalidate"
    email_setting=db.get(SystemSetting,"google_master_account_email")
    token_setting=db.get(SystemSetting,"google_master_refresh_token")
    return {"configured":google_workspace.configured(),"configuration":google_workspace.configuration_status(),"connected":bool(token_setting and token_setting.value),"email":email_setting.value if email_setting else None,"shared":True}

@app.get("/api/google-workspace/master/status")
def google_workspace_master_status(db:DB,response:Response,admin:Annotated[User,Depends(require(Role.ADMIN))]):
    response.headers["Cache-Control"]="no-store, no-cache, must-revalidate"
    email_setting=db.get(SystemSetting,"google_master_account_email")
    token_setting=db.get(SystemSetting,"google_master_refresh_token")
    return {"configured":google_workspace.configured(),"configuration":google_workspace.configuration_status(),"connected":bool(token_setting and token_setting.value),"email":email_setting.value if email_setting else None}

@app.post("/api/google-workspace/master/connect")
def google_workspace_master_connect(admin:Annotated[User,Depends(require(Role.ADMIN))]):
    if not google_workspace.configured(): raise HTTPException(503,"Google OAuth client settings are not configured for this deployment.")
    state=jwt.encode({"sub":str(admin.id),"purpose":"google_workspace_master_connect","exp":datetime.now(timezone.utc)+timedelta(minutes=10)},JWT_SECRET,algorithm="HS256")
    return {"authorization_url":google_workspace.authorization_url(state)}

@app.post("/api/google-workspace/connect")
def google_workspace_connect(user:Annotated[User,Depends(require(Role.END_USER))]):
    raise HTTPException(409,"Google Docs and Sheets use the shared account. Ask an Admin to connect it from Settings.")

@app.get("/api/google-workspace/callback",response_class=HTMLResponse)
def google_workspace_callback(db:DB,code:str|None=None,state:str|None=None,error:str|None=None):
    portal=html.escape(os.getenv("PUBLIC_APP_URL","/"),quote=True)
    if error: return HTMLResponse(f"<main><h2>Google connection was not completed</h2><p>Close this tab and try connecting again.</p><a href='{portal}'>Return to the portal</a></main>",status_code=400)
    if not code or not state: raise HTTPException(400,"Google authorization response is incomplete.")
    try: claims=jwt.decode(state,JWT_SECRET,algorithms=["HS256"])
    except jwt.PyJWTError: raise HTTPException(400,"Google connection expired. Start again from the portal.") from None
    purpose=claims.get("purpose")
    if purpose not in ("google_workspace_connect","google_workspace_master_connect"): raise HTTPException(400,"Invalid Google connection request.")
    user=db.get(User,int(claims.get("sub",0)))
    is_master=purpose=="google_workspace_master_connect"
    if not user or user.status!=AccountStatus.ACTIVE or user.role!=(Role.ADMIN if is_master else Role.END_USER): raise HTTPException(403,"This account cannot connect Google Workspace.")
    tokens=google_workspace.exchange_code(code)
    account=None if is_master else db.scalar(select(GoogleWorkspaceAccount).where(GoogleWorkspaceAccount.user_id==user.id))
    old_master=db.get(SystemSetting,"google_master_refresh_token") if is_master else None
    refresh=tokens.get("refresh_token") or (_field_cipher().decrypt((old_master.value if is_master else account.refresh_token_encrypted).encode()).decode() if (old_master if is_master else account) else None)
    email=google_workspace.user_email(tokens["access_token"])
    if not email: raise HTTPException(502,"Google did not return the connected account email.")
    if not refresh: raise HTTPException(502,"Google did not provide offline access. Disconnect the app in your Google Account and connect again.")
    encrypted=_field_cipher().encrypt(refresh.encode()).decode()
    if is_master:
        for key,value in (("google_master_account_email",email),("google_master_refresh_token",encrypted)):
            setting=db.get(SystemSetting,key)
            if setting: setting.value=value;setting.updated_by=user.id;setting.updated_at=datetime.now(timezone.utc)
            else: db.add(SystemSetting(key=key,value=value,updated_by=user.id))
    elif account: account.google_email=email;account.refresh_token_encrypted=encrypted
    else: db.add(GoogleWorkspaceAccount(user_id=user.id,google_email=email,refresh_token_encrypted=encrypted))
    audit(db,user.id,"GOOGLE_WORKSPACE_CONNECTED","USER",user.id,email)
    db.commit()
    heading="Shared Google Drive account connected" if is_master else "Google Docs and Sheets connected"
    return HTMLResponse(f"<!doctype html><html><head><meta charset='utf-8'><title>Google connected</title></head><body><main style='font:16px Arial,sans-serif;max-width:520px;margin:12vh auto;padding:24px'><h2>{heading}</h2><p>{html.escape(email)}</p><p>You can close this tab and return to the portal.</p><a href='{portal}'>Return to the portal</a></main></body></html>")

def _google_access_for_user(db:Session,user_id:int)->str:
    master=db.get(SystemSetting,"google_master_refresh_token")
    account=None if master and master.value else db.scalar(select(GoogleWorkspaceAccount).where(GoogleWorkspaceAccount.user_id==user_id))
    if not account and not (master and master.value): raise HTTPException(409,"The Admin team must connect the shared Google Drive account from Settings before creating documents.")
    try: refresh=_field_cipher().decrypt((master.value if master and master.value else account.refresh_token_encrypted).encode()).decode()
    except Exception: raise HTTPException(503,"Google authorization cannot be read. Reconnect your Google account.") from None
    return google_workspace.access_token(refresh)

@app.post("/api/job-cards/{card_id}/google-work-products",response_model=RecordDocumentOut,status_code=201)
def create_google_work_product(card_id:int,data:GoogleWorkProductCreate,db:DB,user:Annotated[User,Depends(require(Role.END_USER))]):
    card=mutable_card(assigned_card(db,user,card_id));_require_work_payment(db,card)
    if data.activity_id is not None:
        activity=db.get(JobActivity,data.activity_id)
        if not activity or activity.job_card_id!=card.id or activity.assigned_user_id!=user.id: raise HTTPException(404,"Activity not found.")
    access=_google_access_for_user(db,user.id)
    created=google_workspace.create_file(access,data.title.strip(),data.kind)
    file_id=str(created.get("id", ""));url=str(created.get("webViewLink", ""))
    if not file_id or not url: raise HTTPException(502,"Google created the file but did not return its editor link.")
    filename,mime,content,draft_json=_work_product_file(WorkProductDraftIn(kind=data.kind,title=data.title.strip()))
    try: storage_key=save_file(filename,mime,content)
    except ValueError as exc: raise HTTPException(422,str(exc))
    document_type=f"Google {'Document' if data.kind=='DOCUMENT' else 'Sheet'}: {data.title.strip()}"[:80]
    prior=int(db.scalar(select(func.coalesce(func.max(RecordDocument.version),0)).where(RecordDocument.job_card_id==card.id,RecordDocument.document_type==document_type)) or 0)
    document=RecordDocument(owner_job_card_id=card.id,job_card_id=card.id,activity_id=data.activity_id,document_type=document_type,file_name=filename,storage_key=storage_key,file_size=len(content),mime_type=mime,version=prior+1,status="DRAFT",remarks=draft_json,uploaded_by=user.id,editor_provider="GOOGLE",google_file_id=file_id,google_file_url=url)
    db.add(document);db.flush();audit(db,user.id,"GOOGLE_WORK_PRODUCT_CREATED","DOCUMENT",document.id,f"{document_type}; google file {file_id}");db.commit();db.refresh(document)
    return document

@app.post("/api/activities/{activity_id}/google-work-products/from-reference",response_model=RecordDocumentOut,status_code=201)
def create_google_work_product_from_reference(activity_id:int,data:GoogleReferenceWorkProductCreate,db:DB,user:Annotated[User,Depends(require(Role.END_USER))]):
    activity=db.get(JobActivity,activity_id)
    if not activity or activity.assigned_user_id!=user.id: raise HTTPException(404,"Activity not found.")
    card=mutable_card(assigned_card(db,user,activity.job_card_id));_require_work_payment(db,card)
    compatible={ref["id"] for ref in _job_activity_out(db,activity).reference_documents}
    if data.reference_document_id not in compatible: raise HTTPException(404,"This reference is not assigned to the activity.")
    reference=db.get(ZedReferenceDocument,data.reference_document_id)
    if not reference or reference.mime_type!="application/vnd.openxmlformats-officedocument.wordprocessingml.document" or not reference.file_name.lower().endswith(".docx"):
        raise HTTPException(422,"Create using reference requires a Word (.docx) template.")
    source=STORAGE_ROOT/reference.storage_key
    if not source.is_file(): raise HTTPException(404,"Reference file not found.")
    title=data.title.strip()
    access=_google_access_for_user(db,user.id)
    created=google_workspace.create_doc_from_upload(access,title,source.read_bytes(),reference.mime_type)
    file_id=str(created.get("id", ""));url=str(created.get("webViewLink", ""))
    if not file_id or not url: raise HTTPException(502,"Google imported the reference but did not return its editor link.")
    filename,mime,content,draft_json=_work_product_file(WorkProductDraftIn(kind="DOCUMENT",title=title))
    try: storage_key=save_file(filename,mime,content)
    except ValueError as exc: raise HTTPException(422,str(exc))
    document_type=f"Google Document: {title}"[:80]
    prior=int(db.scalar(select(func.coalesce(func.max(RecordDocument.version),0)).where(RecordDocument.job_card_id==card.id,RecordDocument.document_type==document_type)) or 0)
    remarks=f"Created from Admin reference: {reference.file_name}. {draft_json}"
    document=RecordDocument(owner_job_card_id=card.id,job_card_id=card.id,activity_id=activity.id,document_type=document_type,file_name=filename,storage_key=storage_key,file_size=len(content),mime_type=mime,version=prior+1,status="DRAFT",remarks=remarks,uploaded_by=user.id,editor_provider="GOOGLE",google_file_id=file_id,google_file_url=url)
    db.add(document);db.flush();audit(db,user.id,"GOOGLE_WORK_PRODUCT_CREATED_FROM_REFERENCE","DOCUMENT",document.id,f"{document_type}; reference {reference.id}; Google file {file_id}");db.commit();db.refresh(document)
    return document

def _sync_google_work_product(db:Session,document:RecordDocument,user:User,actor:User|None=None)->str:
    if document.editor_provider!="GOOGLE" or not document.google_file_id: raise HTTPException(409,"This document is not linked to Google Docs or Sheets.")
    kind="SHEET" if document.document_type.startswith("Google Sheet:") else "DOCUMENT"
    content,mime=google_workspace.export_file(_google_access_for_user(db,user.id),document.google_file_id,kind)
    if len(content)>MAX_BYTES: raise HTTPException(413,"Each file must be smaller than 1 GB.")
    extension=".xlsx" if kind=="SHEET" else ".docx"
    filename=re.sub(r"[^A-Za-z0-9._ -]","",document.file_name.rsplit(".",1)[0]).strip()[:100] or ("Spreadsheet" if kind=="SHEET" else "Document")
    filename+=extension
    try: new_key=save_file(filename,mime,content)
    except ValueError as exc: raise HTTPException(422,f"Google export could not be saved to the portal: {exc}") from None
    old_key=document.storage_key;document.storage_key=new_key;document.file_name=filename;document.file_size=len(content);document.mime_type=mime
    audit(db,(actor or user).id,"GOOGLE_WORK_PRODUCT_SYNCED","DOCUMENT",document.id,f"Google Drive file {document.google_file_id}")
    return old_key

@app.get("/api/record-documents/consultant-google-files")
def list_consultant_google_files(db:DB,admin:Annotated[User,Depends(require(Role.ADMIN))]):
    rows=db.execute(select(RecordDocument,JobCard.number,MSME.name,Milestone.code,User.full_name)
        .join(JobCard,JobCard.id==RecordDocument.owner_job_card_id)
        .join(MSME,MSME.id==JobCard.msme_id)
        .outerjoin(Milestone,Milestone.id==RecordDocument.milestone_id)
        .join(User,User.id==RecordDocument.uploaded_by)
        .where(RecordDocument.editor_provider=="GOOGLE",RecordDocument.owner_job_card_id.is_not(None),RecordDocument.payment_id.is_(None),RecordDocument.deleted_at.is_(None),User.role==Role.END_USER,JobCard.deleted_at.is_(None))
        .order_by(RecordDocument.uploaded_at.desc(),RecordDocument.id.desc()).limit(500)).all()
    return [{**RecordDocumentOut.model_validate(document).model_dump(mode="json"),"job_card_number":card_number,"msme_name":msme_name,"milestone_code":milestone_code,"uploaded_by_name":consultant_name} for document,card_number,msme_name,milestone_code,consultant_name in rows]

@app.post("/api/record-documents/{document_id}/admin-sync-google",response_model=RecordDocumentOut)
def admin_sync_google_work_product(document_id:int,db:DB,admin:Annotated[User,Depends(require(Role.ADMIN))]):
    document=db.get(RecordDocument,document_id)
    if not document or document.editor_provider!="GOOGLE" or not document.owner_job_card_id: raise HTTPException(404,"Consultant Google document not found.")
    if document.status in ("APPROVED","REJECTED"): raise HTTPException(409,"This reviewed document is locked; its approved portal copy is available to download.")
    assigned_card(db,admin,document.owner_job_card_id)
    consultant=db.get(User,document.uploaded_by)
    if not consultant: raise HTTPException(404,"The consultant account for this document was not found.")
    old_key=_sync_google_work_product(db,document,consultant,admin);db.commit();db.refresh(document)
    old_path=STORAGE_ROOT/old_key
    if old_path.is_file(): old_path.unlink(missing_ok=True)
    return document

def _start_google_revision(db:Session,document:RecordDocument,user:User)->RecordDocument:
    if document.status not in ("RETURNED","REJECTED"): return document
    latest=int(db.scalar(select(func.coalesce(func.max(RecordDocument.version),0)).where(RecordDocument.job_card_id==document.job_card_id,RecordDocument.document_type==document.document_type)) or document.version)
    old_path=STORAGE_ROOT/document.storage_key
    if not old_path.is_file(): raise HTTPException(404,"The saved portal copy is missing. Sync the Google file before continuing.")
    try: storage_key=save_file(document.file_name,document.mime_type,old_path.read_bytes())
    except ValueError as exc: raise HTTPException(422,str(exc)) from None
    revision=RecordDocument(owner_job_card_id=document.owner_job_card_id,ticket_id=document.ticket_id,enquiry_id=document.enquiry_id,job_card_id=document.job_card_id,milestone_id=document.milestone_id,action_id=document.action_id,visit_id=document.visit_id,payment_id=document.payment_id,milestone_review_id=document.milestone_review_id,approval_id=document.approval_id,activity_id=document.activity_id,document_type=document.document_type,file_name=document.file_name,storage_key=storage_key,file_size=document.file_size,mime_type=document.mime_type,editor_provider=document.editor_provider,google_file_id=document.google_file_id,google_file_url=document.google_file_url,version=latest+1,status="DRAFT",remarks=None,uploaded_by=user.id)
    db.add(revision);db.flush();audit(db,user.id,"GOOGLE_WORK_PRODUCT_REVISION_STARTED","DOCUMENT",revision.id,f"Revised from returned version {document.version}")
    return revision

@app.post("/api/record-documents/{document_id}/sync-google",response_model=RecordDocumentOut)
def sync_google_work_product(document_id:int,db:DB,user:Annotated[User,Depends(require(Role.END_USER))]):
    document=db.get(RecordDocument,document_id)
    if not document or document.uploaded_by!=user.id or document.status not in ("DRAFT","RETURNED","REJECTED"): raise HTTPException(404,"Editable Google document not found.")
    document=_start_google_revision(db,document,user)
    card=mutable_card(assigned_card(db,user,document.owner_job_card_id));_require_work_payment(db,card)
    old_key=_sync_google_work_product(db,document,user);db.commit();db.refresh(document)
    old_path=STORAGE_ROOT/old_key
    if old_path.is_file(): old_path.unlink(missing_ok=True)
    return document

@app.get("/api/job-cards/{card_id}/work-products",response_model=list[RecordDocumentOut])
def list_work_product_drafts(card_id:int,db:DB,user:Annotated[User,Depends(require(Role.END_USER))]):
    assigned_card(db,user,card_id)
    return list(db.scalars(select(RecordDocument).where(RecordDocument.job_card_id==card_id,RecordDocument.uploaded_by==user.id,RecordDocument.editor_provider.in_(("GOOGLE","PORTAL")),RecordDocument.status.in_(("DRAFT","RETURNED","REJECTED"))).order_by(RecordDocument.uploaded_at.desc())))

@app.post("/api/job-cards/{card_id}/documents",response_model=DocumentOut,status_code=201)
def upload_document(card_id:int,data:DocumentUpload,db:DB,user:Annotated[User,Depends(require(Role.END_USER))]):
    card=mutable_card(assigned_card(db,user,card_id));_require_work_payment(db,card)
    try: content=_decode_encoded_file(data.content_base64); storage_key=save_file(data.file_name,data.mime_type,content)
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
def document_review_queue(db:DB,user:Annotated[User,Depends(require(Role.ADMIN))],page:int=Query(default=1,ge=1),page_size:int=Query(default=25,ge=1,le=100)):
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
def review_document(document_id:int,data:DocumentReview,db:DB,user:Annotated[User,Depends(require(Role.ADMIN))]):
    document=db.get(Document,document_id)
    if not document or document.deleted_at: raise HTTPException(404,"Document not found.")
    if document.status not in ("SUBMITTED","RESUBMITTED"): raise HTTPException(409,"Only submitted document versions can be reviewed.")
    mutable_card(assigned_card(db,user,document.job_card_id))
    latest_id=db.scalar(select(Document.id).where(Document.job_card_id==document.job_card_id,Document.document_type==document.document_type,Document.deleted_at==None).order_by(Document.version.desc(),Document.id.desc()).limit(1))
    if latest_id!=document.id: raise HTTPException(409,"Only the latest document version can be reviewed.")
    previous=document.status
    document.status=data.decision; document.remarks=data.comment;document.reviewed_by=user.id;document.reviewed_at=datetime.now(timezone.utc); audit(db,user.id,f"DOCUMENT_{data.decision}","DOCUMENT",document.id,f"status:{previous}->{data.decision}; comment:{data.comment}"); owner=document_owner(db,document.job_card_id); notify(db,owner,f"Your {document.document_type} was {data.decision.lower()}. {data.comment}",f"/job-cards/{document.job_card_id}"); db.commit(); db.refresh(document); return document

STAGES=("CONSULTANT_CONFIRMATION","DIRECTOR_REVIEW")
STAGE_ROLES={"CONSULTANT_CONFIRMATION":Role.END_USER,"DIRECTOR_REVIEW":Role.DIRECTOR}
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
    return card.director_id
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
    if user.role==Role.UNIT_REPRESENTATIVE: return False
    if user.role==Role.MARKETING: return False
    if user.role in (Role.ADMIN,Role.MANAGEMENT): return True
    return action.created_by==user.id or action.assigned_user_id==user.id or action.assigned_role==user.role.value or (user.department_id is not None and user.department_id==action.department_id)

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
    recipient_query=select(User).where(User.status==AccountStatus.ACTIVE)
    recipient_query=recipient_query.where(User.role==step["role"]) if step.get("role") else recipient_query.where(User.department_id==department.id)
    recipients=[assignee] if assignee else list(db.scalars(recipient_query))
    for recipient in recipients:
        if recipient: notify(db,recipient.id,f"Action {action.action_number} is ready: {action.title}.","/actions")
    return action

def _start_triggered_workflows(db:Session,event:str,card:JobCard,actor:User,milestone:Milestone|None=None):
    for template in db.scalars(select(WorkflowTemplate).where(WorkflowTemplate.active.is_(True),WorkflowTemplate.trigger_event==event)):
        duplicate=select(WorkflowAction.id).where(WorkflowAction.template_code==template.code,WorkflowAction.job_card_id==card.id,WorkflowAction.milestone_id==(milestone.id if milestone else None))
        if db.scalar(duplicate):continue
        action=_action_step(db,template,0,{"job_card_id":card.id,"milestone_id":milestone.id if milestone else None},actor.id,None)
        audit(db,actor.id,"WORKFLOW_TRIGGERED","ACTION",None,f"{template.code}; event={event}; action={action.action_number}")

def _next_milestone(db:Session,card_id:int,milestone:Milestone)->Milestone|None:
    order={"M1":1,"M2":2,"M3":3}
    current=order.get(milestone.code)
    if current is None:return None
    return db.scalar(select(Milestone).where(Milestone.job_card_id==card_id,Milestone.code==f"M{current+1}")) if current<3 else None

def _finish_milestone_payment_actions(db:Session,milestone:Milestone,actor:User):
    actions=list(db.scalars(select(WorkflowAction).where(WorkflowAction.milestone_id==milestone.id,WorkflowAction.template_code.in_(("INITIAL_MILESTONE_PAYMENT","MILESTONE_NEXT_PAYMENT")),WorkflowAction.status.in_(("OPEN","IN_PROGRESS","WAITING","ON_HOLD")))))
    for action in actions:
        action.status="COMPLETED";action.completed_at=datetime.now(timezone.utc);action.remarks="Finance confirmed the milestone payment; the payment gate has been evaluated."
        audit(db,actor.id,"PAYMENT_WORKFLOW_COMPLETED","ACTION",None,action.action_number)

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
def start_workflow_action(data:ActionStart,db:DB,user:Annotated[User,Depends(require(Role.ADMIN))]):
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
    if user.role in (Role.MARKETING,Role.UNIT_REPRESENTATIVE): raise HTTPException(403,"This role does not have a workflow action queue.")
    query=select(WorkflowAction).order_by(WorkflowAction.created_at.desc())
    if user.role not in (Role.ADMIN,Role.MANAGEMENT): query=query.where(or_(WorkflowAction.assigned_user_id==user.id,WorkflowAction.created_by==user.id,WorkflowAction.assigned_role==user.role.value,WorkflowAction.department_id==user.department_id if user.department_id else False))
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
    if action.assigned_role and user.role.value!=action.assigned_role:
        raise HTTPException(403,"You are not eligible to claim this role action.")
    if not action.assigned_role and user.department_id!=action.department_id:
        raise HTTPException(403,"You are not eligible to claim this department action.")
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
def transfer_workflow_action(action_id:UUID,data:ActionTransfer,db:DB,user:Annotated[User,Depends(require(Role.ADMIN,Role.MANAGEMENT))]):
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
        if user.role not in (Role.FINANCE,Role.MANAGEMENT): raise HTTPException(403,"Payment records are restricted to Finance and Management.")
        row=db.get(Payment,key)
        if not row: raise HTTPException(404,"Payment not found.")
        if row.job_card_id and user.role==Role.MANAGEMENT: assigned_card(db,user,row.job_card_id)
        owner_card_id=row.job_card_id
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
    if user.role==Role.MARKETING: raise HTTPException(403,"Marketing can access enquiry records only.")
    if user.role==Role.FINANCE and record_type.upper()!="PAYMENT": raise HTTPException(403,"Finance can access payment records only.")
    _,column,key,_,_=resolve_record_document_target(db,user,record_type,record_id)
    return list(db.scalars(select(RecordDocument).where(getattr(RecordDocument,column)==key,RecordDocument.deleted_at.is_(None)).order_by(RecordDocument.document_type,RecordDocument.version.desc())))

@app.post("/api/records/{record_type}/{record_id}/documents",response_model=RecordDocumentOut,status_code=201)
def upload_record_document(record_type:str,record_id:str,data:RecordDocumentUpload,db:DB,user:Annotated[User,Depends(current_user)]):
    if user.role==Role.MARKETING: raise HTTPException(403,"Marketing can access enquiry records only.")
    if user.role==Role.FINANCE and record_type.upper()!="PAYMENT": raise HTTPException(403,"Finance can access payment records only.")
    kind,column,key,row,owner_card_id=resolve_record_document_target(db,user,record_type,record_id)
    if user.role==Role.FINANCE and kind!="PAYMENT": raise HTTPException(403,"Finance can only attach payment evidence.")
    if kind=="PAYMENT" and user.role!=Role.FINANCE: raise HTTPException(403,"Only Finance can attach payment documents.")
    if user.role not in (Role.ADMIN,Role.MANAGEMENT,Role.END_USER,Role.FINANCE): raise HTTPException(403,"You cannot upload documents to this record.")
    if data.status=="DRAFT" and user.role!=Role.END_USER: raise HTTPException(403,"Only consultants can save consultant document drafts.")
    if kind=="VISIT" and data.document_type=="Consultant Visit Report":
        if user.role!=Role.END_USER or data.status!="SUBMITTED": raise HTTPException(403,"Only consultants can submit visit reports for approval.")
        planned=db.get(PlannedVisit,row.planned_visit_id) if row.planned_visit_id else None
        if not planned or planned.status!="COMPLETED" or user.id not in (planned.consultant_id,planned.co_consultant_id,planned.subject_expert_id): raise HTTPException(409,"The visit plan must be completed and assigned to you before submitting its report.")
        tasks=list(db.scalars(select(JobActivity).where(JobActivity.planned_visit_id==planned.id)))
        if not tasks or any(task.status!="COMPLETED" or not db.scalar(select(JobActivityAttachment.id).where(JobActivityAttachment.activity_id==task.id)) for task in tasks): raise HTTPException(409,"Complete every required visit task and attach its evidence before submitting the report.")
        pending=db.scalar(select(RecordDocument.id).where(RecordDocument.visit_id==row.id,RecordDocument.document_type=="Consultant Visit Report",RecordDocument.status.in_(("DRAFT","SUBMITTED","UNDER_REVIEW","APPROVED"))).limit(1))
        if pending: raise HTTPException(409,"A report for this visit is already submitted or approved.")
    if owner_card_id and kind in ("JOB_CARD","MILESTONE","VISIT"): _require_work_payment(db,db.get(JobCard,owner_card_id),db.get(Milestone,row.id) if kind=="MILESTONE" else None)
    try: content=_decode_encoded_file(data.content_base64);storage_key=save_file(data.file_name,data.mime_type,content)
    except ValueError as exc: raise HTTPException(422,str(exc))
    except Exception: raise HTTPException(422,"The uploaded file is invalid.")
    prior=int(db.scalar(select(func.coalesce(func.max(RecordDocument.version),0)).where(getattr(RecordDocument,column)==key,RecordDocument.document_type==data.document_type)) or 0)
    fields={column:key,"owner_job_card_id":owner_card_id,"document_type":data.document_type,"file_name":data.file_name,"storage_key":storage_key,"file_size":len(content),"mime_type":data.mime_type,"version":prior+1,"status":data.status,"remarks":data.remarks,"uploaded_by":user.id}
    document=RecordDocument(**fields);db.add(document);db.flush();audit(db,user.id,"RECORD_DOCUMENT_UPLOADED","DOCUMENT",document.id,f"{kind}:{record_id}; {data.document_type} v{document.version}")
    if owner_card_id and data.status=="SUBMITTED":
        owner=document_owner(db,owner_card_id) or card_created_by(db,owner_card_id)
        if owner!=user.id: notify(db,owner,f"{data.document_type} was attached to {kind.lower().replace('_',' ')} {record_id}.",f"/job-cards/{owner_card_id}")
    if kind=="VISIT" and data.document_type=="Consultant Visit Report" and data.status=="SUBMITTED":
        card=db.get(JobCard,owner_card_id);milestone=db.get(Milestone,row.milestone_id)
        for admin in db.scalars(select(User).where(User.role==Role.ADMIN,User.status==AccountStatus.ACTIVE)):
            notify(db,admin.id,f"Consultant visit report for {card.number if card else 'Job Card'} {milestone.code if milestone else ''} is awaiting approval. Please review it within 24 hours.","/admin/documents")
    db.commit();db.refresh(document);return document

@app.post("/api/record-documents/{document_id}/submit",response_model=RecordDocumentOut)
def submit_record_document(document_id:int,db:DB,user:Annotated[User,Depends(require(Role.END_USER))]):
    document=db.get(RecordDocument,document_id)
    if not document or document.uploaded_by!=user.id or document.status not in ("DRAFT","RETURNED","REJECTED"): raise HTTPException(404,"Draft, returned, or rejected document not found.")
    synced_old_key=None
    if document.owner_job_card_id:
        card=mutable_card(assigned_card(db,user,document.owner_job_card_id));milestone=db.get(Milestone,document.milestone_id) if document.milestone_id else None;_require_work_payment(db,card,milestone)
    if document.editor_provider=="PORTAL":
        synced_old_key=_finalize_native_work_product(document)
    elif document.editor_provider=="GOOGLE":
        document=_start_google_revision(db,document,user)
        synced_old_key=_sync_google_work_product(db,document,user)
    document.status="SUBMITTED";document.reviewed_by=None;document.reviewed_at=None
    audit(db,user.id,"RECORD_DOCUMENT_SUBMITTED","DOCUMENT",document.id,f"{document.document_type} v{document.version}")
    if document.owner_job_card_id:
        owner=document_owner(db,document.owner_job_card_id) or card_created_by(db,document.owner_job_card_id)
        if owner and owner!=user.id: notify(db,owner,f"{document.document_type} was submitted for review.",f"/job-cards/{document.owner_job_card_id}")
    db.commit();db.refresh(document)
    if synced_old_key:
        old_path=STORAGE_ROOT/synced_old_key
        if old_path.is_file(): old_path.unlink(missing_ok=True)
    return document

@app.post("/api/record-documents/{document_id}/review",response_model=RecordDocumentOut)
def review_record_document(document_id:int,data:RecordDocumentReview,db:DB,user:Annotated[User,Depends(require(Role.ADMIN))]):
    document=db.get(RecordDocument,document_id)
    if not document: raise HTTPException(404,"Document not found.")
    if document.payment_id is not None: raise HTTPException(403,"Payment evidence is managed by Finance and is not part of document review.")
    if document.owner_job_card_id: assigned_card(db,user,document.owner_job_card_id)
    if document.status not in ("SUBMITTED","UNDER_REVIEW"): raise HTTPException(409,"This document is not awaiting review.")
    target_column=next((column for column in ("activity_id","ticket_id","enquiry_id","job_card_id","milestone_id","action_id","visit_id","milestone_review_id","visit_review_id","approval_id") if getattr(document,column) is not None),None)
    if target_column:
        latest_id=db.scalar(select(RecordDocument.id).where(getattr(RecordDocument,target_column)==getattr(document,target_column),RecordDocument.document_type==document.document_type,RecordDocument.status.in_(("SUBMITTED","UNDER_REVIEW")),RecordDocument.deleted_at.is_(None)).order_by(RecordDocument.version.desc(),RecordDocument.id.desc()).limit(1))
        if latest_id!=document.id: raise HTTPException(409,"Only the latest document version can be reviewed.")
    document.status=data.decision;document.remarks=data.comment;document.reviewed_by=user.id;document.reviewed_at=datetime.now(timezone.utc);audit(db,user.id,f"RECORD_DOCUMENT_{data.decision}","DOCUMENT",document.id,data.comment)
    if document.owner_job_card_id:
        owner=document_owner(db,document.owner_job_card_id) or card_created_by(db,document.owner_job_card_id)
        if owner: notify(db,owner,f"{document.document_type} was {data.decision.lower()}: {data.comment}",f"/job-cards/{document.owner_job_card_id}")
    if data.decision in ("RETURNED","REJECTED") and document.activity_id:
        activity=db.scalar(select(JobActivity).where(JobActivity.id==document.activity_id).with_for_update())
        if activity:
            activity.status="IN_PROGRESS"
            activity.review_status=data.decision
            activity.review_remarks=data.comment
            activity.reviewed_by=user.id
            activity.reviewed_at=datetime.now(timezone.utc)
            if activity.planned_visit_id:
                planned=db.get(PlannedVisit,activity.planned_visit_id)
                if planned:
                    planned.status="IN_PROGRESS"
                    activity.sla_due_at=datetime.now(timezone.utc)+timedelta(hours=24)
                    activity.end_date=activity.sla_due_at.astimezone(ZoneInfo("Asia/Kolkata")).date()
            notify(db,activity.assigned_user_id,f"Activity document {document.file_name} was {data.decision.lower()}: {data.comment}. Revise the work and submit it again.",f"/user/job-cards?card={activity.job_card_id}")
    review_milestone_id=document.milestone_id
    if review_milestone_id is None and document.visit_id:
        reviewed_visit=db.get(Visit,document.visit_id)
        review_milestone_id=reviewed_visit.milestone_id if reviewed_visit else None
    if data.decision=="APPROVED" and review_milestone_id:
        milestone=db.get(Milestone,review_milestone_id)
        card=db.get(JobCard,milestone.job_card_id) if milestone else None
        if milestone and card and _milestone_admin_review_ready(db,milestone):
            for manager in db.scalars(select(User).where(User.role==Role.MANAGEMENT,User.status==AccountStatus.ACTIVE)):
                notify(db,manager.id,f"Admin approved the submitted documents and activities for {milestone.code} on Job Card {card.number}. It is ready for your milestone review.",f"/management/reviews?milestone={milestone.id}")
    db.commit();db.refresh(document);return document

@app.delete("/api/record-documents/{document_id}",status_code=204)
def delete_record_document(document_id:int,db:DB,user:Annotated[User,Depends(require(Role.END_USER))]):
    document=db.get(RecordDocument,document_id)
    if not document or document.deleted_at or document.uploaded_by!=user.id or not document.owner_job_card_id or document.payment_id is not None:
        raise HTTPException(404,"Document not found.")
    card=mutable_card(assigned_card(db,user,document.owner_job_card_id))
    if document.status not in ("DRAFT","RETURNED"):
        raise HTTPException(409,"Only draft or returned documents can be deleted.")
    chain=[document]
    if document.editor_provider=="GOOGLE" and document.google_file_id:
        chain=list(db.scalars(select(RecordDocument).where(RecordDocument.google_file_id==document.google_file_id,RecordDocument.uploaded_by==user.id,RecordDocument.owner_job_card_id==document.owner_job_card_id,RecordDocument.deleted_at.is_(None))))
        if any(item.status in ("APPROVED","SUBMITTED","UNDER_REVIEW") for item in chain):
            raise HTTPException(409,"This Google document is already submitted or approved and cannot be deleted.")
        latest=max(chain,key=lambda item:(item.version,item.id))
        if latest.id!=document.id:
            raise HTTPException(409,"Delete the latest draft version of this Google document.")
        google_workspace.delete_file(_google_access_for_user(db,user.id),document.google_file_id)
    old_keys=[item.storage_key for item in chain]
    for item in chain:
        item.deleted_at=datetime.now(timezone.utc);item.deleted_by=user.id
    audit(db,user.id,"RECORD_DOCUMENT_DELETED","DOCUMENT",document.id,f"{card.number}; {document.file_name}; versions {','.join(str(item.version) for item in chain)}")
    db.commit()
    for key in old_keys:
        file_path=STORAGE_ROOT/key
        if file_path.is_file(): file_path.unlink(missing_ok=True)

@app.get("/api/record-documents/{document_id}/download")
def download_record_document(document_id:int,db:DB,user:Annotated[User,Depends(current_user)]):
    document=db.get(RecordDocument,document_id)
    if not document or document.deleted_at: raise HTTPException(404,"Document not found.")
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
def prepare_project_closure(card_id:int,data:ClosureUpsert,db:DB,user:Annotated[User,Depends(require(Role.ADMIN))]):
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
def complete_project_closure(card_id:int,db:DB,user:Annotated[User,Depends(require(Role.ADMIN))]):
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
    # A completed ZED Silver engagement should enter Marketing's Gold follow-up
    # queue. Keep it as a linked ticket so the completed Silver enquiry/onboarding
    # remains unchanged and any accepted Gold work can use its own normal enquiry.
    source_enquiry=db.get(Enquiry,card.source_enquiry_id) if card.source_enquiry_id else None
    service_label=" ".join(value for value in (card.service_name,card.package_code) if value).casefold()
    is_zed_silver="zed" in service_label and "silver" in service_label and "gold" not in service_label
    if source_enquiry and is_zed_silver:
        title=f"ZED Gold follow-up after {card.number}"
        existing=db.scalar(select(Ticket.id).where(Ticket.job_card_id==card.id,Ticket.title==title))
        if not existing:
            marketing_department=db.scalar(select(Department).where(Department.code=="MARKETING",Department.active.is_(True)))
            referral=Ticket(
                number=next_business_number(db,"ticket"),
                title=title,
                description=(f"ZED Silver Job Card {card.number} has completed all milestones and project closure. "
                    f"Contact the client for the ZED Gold opportunity, record their interest and next steps, "
                    f"and if they accept, create a separate ZED Gold enquiry so Admin can use the standard "
                    f"onboarding, Job Card, and M1 payment workflow. Original enquiry: {source_enquiry.number}."),
                ticket_type="CLIENT_COMMUNICATION",priority="NORMAL",created_by=user.id,
                created_department_id=user.department_id,
                assigned_department_id=marketing_department.id if marketing_department else None,
                status="OPEN",enquiry_id=source_enquiry.id,job_card_id=card.id,
            )
            db.add(referral);db.flush()
            db.add(TicketRelationship(ticket_id=referral.id,related_enquiry_id=source_enquiry.id,relationship_type="REFERENCE",created_by=user.id))
            db.add(TicketRelationship(ticket_id=referral.id,related_job_card_id=card.id,relationship_type="REFERENCE",created_by=user.id))
            audit(db,user.id,"ZED_SILVER_GOLD_MARKETING_REFERRAL_CREATED","TICKET",None,f"{referral.number}: {source_enquiry.number}; {card.number}",entity_ref=str(referral.id))
            recipients=list(db.scalars(select(User).where(User.role==Role.MARKETING,User.status==AccountStatus.ACTIVE)))
            for recipient in recipients:
                notify(db,recipient.id,f"{source_enquiry.number}: ZED Silver is complete. Follow up with the client about ZED Gold (ticket {referral.number}).",f"/tickets/{referral.id}")
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
    activities=db.scalars(select(JobActivity).where(JobActivity.assigned_user_id==user.id,JobActivity.status.in_(("PLANNED","IN_PROGRESS")),or_(JobActivity.sla_due_at.is_not(None),and_(JobActivity.planned_visit_id.is_(None),JobActivity.end_date.is_not(None))))).all()
    for activity in activities:
        if activity.sla_due_at:
            due=activity.sla_due_at if activity.sla_due_at.tzinfo else activity.sla_due_at.replace(tzinfo=timezone.utc)
            due_label=due.astimezone(ZoneInfo("Asia/Kolkata")).isoformat()
            if due<now: state="OVERDUE"
            elif due<=now+timedelta(days=1): state="UPCOMING"
            else: continue
        else:
            due_label=activity.end_date.isoformat()
            if activity.end_date<today: state="OVERDUE"
            elif activity.end_date<=tomorrow: state="UPCOMING"
            else: continue
        notify_once(db,user.id,f"Activity {activity.name} is {state.lower()} (due {due_label}).",f"/user/job-cards?card={activity.job_card_id}",f"task:activity:{activity.id}:{state}:{due_label}")
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
def get_report(kind:str,db:DB,user:Annotated[User,Depends(require(Role.ADMIN,Role.MANAGEMENT,Role.FINANCE))],status_filter:str|None=Query(default=None,alias="status"),search:str|None=None,delayed:bool|None=None,overrun:bool|None=None,start_date:str|None=None,end_date:str|None=None,consultant_id:int|None=None,msme_id:int|None=None,job_card_id:int|None=None,milestone_id:int|None=None,project_status:str|None=None,page:int=Query(default=1,ge=1),page_size:int=Query(default=25,ge=1,le=100)):
    if user.role==Role.FINANCE and kind!="payments": raise HTTPException(403,"Finance can access payment reports only.")
    if kind=="payments" and user.role==Role.ADMIN: raise HTTPException(403,"Payment records are restricted to Finance and Management.")
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
def export_report(kind:str,format:str,db:DB,user:Annotated[User,Depends(require(Role.ADMIN,Role.MANAGEMENT,Role.FINANCE))],status_filter:str|None=Query(default=None,alias="status"),search:str|None=None,delayed:bool|None=None,overrun:bool|None=None,start_date:str|None=None,end_date:str|None=None,consultant_id:int|None=None,msme_id:int|None=None,job_card_id:int|None=None,milestone_id:int|None=None,project_status:str|None=None):
    if user.role==Role.FINANCE and kind!="payments": raise HTTPException(403,"Finance can access payment reports only.")
    if kind=="payments" and user.role==Role.ADMIN: raise HTTPException(403,"Payment records are restricted to Finance and Management.")
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
        query=(select(Milestone,JobCard.number)
            .join(JobCard,JobCard.id==Milestone.job_card_id)
            .where(Milestone.job_card_id.in_(visible_ids),JobCard.deleted_at==None)
            .order_by(Milestone.closing_date.asc(),Milestone.id.desc()).limit(8))
        for milestone,card_number in db.execute(query):
            summary=milestone_summary(db,milestone)
            milestone_rows.append({"id":milestone.id,"job_card_number":card_number,"code":milestone.code,"name":milestone.name,"planned_visits":milestone.planned_visits,"completed_visits":summary["actual_visits"],"progress":summary["progress"],"status":summary["status"],"review_status":summary["review_status"],"start_date":milestone.start_date,"closing_date":milestone.closing_date,"payment_date":milestone.payment_date,"portal_report_submission_date":milestone.portal_report_submission_date})
    payment_kpis={}
    if user.role==Role.MANAGEMENT:
        open_payment_states=("PENDING","INVOICE_RAISED","PARTIALLY_PAID","OVERDUE")
        pending_amount=db.scalar(select(func.coalesce(func.sum(Payment.amount_due-Payment.amount_paid),0)).where(Payment.status.in_(open_payment_states))) or 0
        initial_pending=int(db.scalar(select(func.count(JobCard.id)).join(Milestone,Milestone.job_card_id==JobCard.id).where(JobCard.deleted_at.is_(None),JobCard.payment_gate_enabled.is_(True),Milestone.code=="M1",~select(ClientMilestonePayment.id).where(ClientMilestonePayment.milestone_id==Milestone.id).exists())) or 0)
        milestone_payment_pending=int(db.scalar(select(func.count(Milestone.id)).join(JobCard,JobCard.id==Milestone.job_card_id).where(JobCard.deleted_at.is_(None),JobCard.payment_gate_enabled.is_(True),~select(ClientMilestonePayment.id).where(ClientMilestonePayment.milestone_id==Milestone.id).exists())) or 0)
        payment_kpis={"initial_payments_pending":initial_pending,"milestone_payments_pending":milestone_payment_pending,"pending_invoices":int(db.scalar(select(func.count(Payment.id)).where(Payment.status.in_(open_payment_states))) or 0),"pending_payment_amount":pending_amount,"paid_amount":db.scalar(select(func.coalesce(func.sum(Payment.amount_paid),0))) or 0}
    open_actions=int(db.scalar(select(func.count(WorkflowAction.id)).where(WorkflowAction.status.in_(("OPEN","IN_PROGRESS","WAITING","ON_HOLD")))) or 0)
    task_now=datetime.now(timezone.utc)
    overdue_activities=int(db.scalar(select(func.count(JobActivity.id)).where(JobActivity.status.in_(("PLANNED","IN_PROGRESS")),or_(and_(JobActivity.planned_visit_id.is_not(None),JobActivity.sla_due_at<task_now),and_(JobActivity.planned_visit_id.is_(None),JobActivity.end_date<date.today())))) or 0)
    overdue_tasks=int(db.scalar(select(func.count(WorkflowAction.id)).where(WorkflowAction.status.in_(("OPEN","IN_PROGRESS","WAITING","ON_HOLD")),WorkflowAction.due_at<task_now)) or 0)+overdue_activities
    pending_tasks=open_actions+int(db.scalar(select(func.count(JobActivity.id)).where(JobActivity.status.in_(("PLANNED","IN_PROGRESS")))) or 0)
    planned_upcoming=int(db.scalar(select(func.count(PlannedVisit.id)).where(PlannedVisit.status=="PLANNED",PlannedVisit.visit_date>=date.today())) or 0)
    milestone_progress={}
    for code in ("M1","M2","M3"):
        ms=list(db.scalars(select(Milestone).join(JobCard,JobCard.id==Milestone.job_card_id).where(Milestone.code==code,JobCard.deleted_at.is_(None))))
        summaries=[milestone_summary(db,m) for m in ms]
        progresses=[summary["progress"] for summary in summaries]
        milestone_progress[code]={"total":len(ms),"progress":(sum(progresses,Decimal(0))/Decimal(len(progresses))).quantize(Decimal(".01")) if progresses else Decimal(0),"completed":sum(1 for m,summary in zip(ms,summaries) if summary["status"]=="COMPLETED")}
    consultant_workload=consultant_rows(db)
    msme_progress=[]
    for msme_id in sorted({row["msme_id"] for row in cards}):
        owned=[row for row in cards if row["msme_id"]==msme_id];company=db.get(MSME,msme_id)
        msme_progress.append({"msme_id":msme_id,"msme":company.name if company else "","job_cards":len(owned),"progress":(sum((Decimal(str(row["progress"])) for row in owned),Decimal(0))/Decimal(len(owned))).quantize(Decimal(".01")) if owned else Decimal(0),"overruns":sum(1 for row in owned if row["overrun"])})
    completed_site_visits=sum(milestone_summary(db,m)["actual_visits"] for m in db.scalars(select(Milestone).join(JobCard,JobCard.id==Milestone.job_card_id).where(JobCard.deleted_at==None)))
    remaining_md=max(required-completed,Decimal(0)).quantize(MD_PRECISION)
    kpis={"total_enquiries":int(db.scalar(select(func.count(Enquiry.id))) or 0),"open_enquiries":int(db.scalar(select(func.count(Enquiry.id)).where(Enquiry.status.not_in(("CONVERTED","LOST","CANCELLED")))) or 0),"converted_enquiries":int(db.scalar(select(func.count(Enquiry.id)).where(Enquiry.status=="CONVERTED")) or 0),"conversion_percent":(Decimal(int(db.scalar(select(func.count(Enquiry.id)).where(Enquiry.status=="CONVERTED")) or 0)*100/ max(int(db.scalar(select(func.count(Enquiry.id))) or 0),1))).quantize(Decimal(".01")),"total_projects":len(cards),"active_projects":active,"completed_projects":sum(c["status"]=="COMPLETED" for c in cards),"on_hold_projects":sum(c["status"]=="ON_HOLD" for c in cards),"delayed_projects":len(delayed),"overrun_projects":sum(c["overrun"] for c in cards),"pending_visits":pending_visits,"planned_upcoming_visits":planned_upcoming,"completed_site_visits":completed_site_visits,"pending_documents":pending_documents,"pending_approvals":pending_approvals,"pending_tasks":pending_tasks,"overdue_tasks":overdue_tasks,"overdue_activities":overdue_activities,"milestones_due":milestone_due,**payment_kpis,"open_actions":open_actions,"closure_pending":int(db.scalar(select(func.count(JobCard.id)).where(JobCard.status=="CLOSURE_PENDING")) or 0),"required_md":required,"completed_md":completed.quantize(MD_PRECISION),"remaining_md":remaining_md}
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
    m1_assigned=select(MilestoneConsultantAllocation.id).join(Milestone,Milestone.id==MilestoneConsultantAllocation.milestone_id).join(ClientMilestonePayment,ClientMilestonePayment.milestone_id==Milestone.id).where(Milestone.job_card_id==JobCard.id,Milestone.code=="M1",MilestoneConsultantAllocation.active.is_(True)).exists()
    linked_enquiry=select(JobCard.id).where(JobCard.source_enquiry_id==Enquiry.id).exists()
    has_onboarding=select(EnquiryOnboarding.id).where(EnquiryOnboarding.enquiry_id==Enquiry.id).exists()
    payload["kpis"]["enquiries_awaiting_onboarding"]=int(db.scalar(select(func.count(Enquiry.id)).where(Enquiry.status=="CONVERTED",Enquiry.management_approval_status=="APPROVED",~linked_enquiry,~has_onboarding)) or 0)
    payload["kpis"].update({"total_users":int(db.scalar(select(func.count(User.id))) or 0),"total_consultants":int(db.scalar(select(func.count(User.id)).where(User.role==Role.END_USER)) or 0),"visits_recorded":int(db.scalar(select(func.count(Visit.id)).where(Visit.deleted_at==None)) or 0),"total_msmes":int(db.scalar(select(func.count(MSME.id)).where(MSME.deleted_at.is_(None))) or 0),"total_job_cards":int(db.scalar(select(func.count(JobCard.id)).where(JobCard.deleted_at.is_(None))) or 0),"job_cards_awaiting_consultant_assignment":int(db.scalar(select(func.count(JobCard.id)).where(JobCard.deleted_at.is_(None),~m1_assigned)) or 0),"work_in_progress":int(db.scalar(select(func.count(JobCard.id)).where(JobCard.deleted_at.is_(None),JobCard.status=="IN_PROGRESS")) or 0),"returned_documents":int(db.scalar(select(func.count(Document.id)).where(Document.status=="RETURNED",Document.deleted_at.is_(None))) or 0)+int(db.scalar(select(func.count(RecordDocument.id)).where(RecordDocument.status=="RETURNED")) or 0),"milestones_awaiting_action":int(db.scalar(select(func.count(Milestone.id)).where(Milestone.status.in_(("READY_FOR_REVIEW","AWAITING_UNIT_CONFIRMATION")))) or 0),"upcoming_visits":int(db.scalar(select(func.count(Visit.id)).where(Visit.visit_date>=date.today(),Visit.status=="DRAFT",Visit.deleted_at.is_(None))) or 0)+int(db.scalar(select(func.count(PlannedVisit.id)).where(PlannedVisit.visit_date>=date.today(),PlannedVisit.status=="PLANNED")) or 0),"overdue_activities":payload["kpis"]["overdue_activities"]})
    payload["kpis"]["pending_activities"]=payload["kpis"]["pending_visits"]+payload["kpis"]["pending_documents"]
    availability_rows=db.execute(select(User.id,User.full_name).where(User.role==Role.END_USER,User.status==AccountStatus.ACTIVE).order_by(User.full_name)).all()
    workload_rows=db.execute(select(MilestoneConsultantAllocation.consultant_id,Milestone.job_card_id,MilestoneConsultantAllocation.allocated_mandays).join(Milestone,Milestone.id==MilestoneConsultantAllocation.milestone_id).join(JobCard,JobCard.id==Milestone.job_card_id).where(MilestoneConsultantAllocation.active.is_(True),Milestone.status.not_in(("COMPLETED","CANCELLED")),JobCard.deleted_at.is_(None))).all()
    workload_by_consultant={}
    for consultant_id,job_card_id,planned_md in workload_rows:
        row=workload_by_consultant.setdefault(consultant_id,{"cards":set(),"mandays":Decimal(0)})
        row["cards"].add(job_card_id);row["mandays"]+=Decimal(str(planned_md or 0))
    payload["consultant_workload"]=[{"user_id":uid,"full_name":name,"active_job_cards":len(workload_by_consultant.get(uid,{"cards":set()})["cards"]),"planned_mandays":str(workload_by_consultant.get(uid,{"mandays":Decimal(0)})["mandays"])} for uid,name in availability_rows]
    current_allocation=select(MilestoneConsultantAllocation.id).join(Milestone,Milestone.id==MilestoneConsultantAllocation.milestone_id).join(JobCard,JobCard.id==Milestone.job_card_id).where(MilestoneConsultantAllocation.consultant_id==User.id,MilestoneConsultantAllocation.active.is_(True),Milestone.status.not_in(("COMPLETED","CANCELLED")),Milestone.start_date.is_not(None),Milestone.start_date<=date.today(),or_(Milestone.closing_date.is_(None),Milestone.closing_date>=date.today()),JobCard.deleted_at.is_(None)).exists()
    payload["consultant_availability"]=[{"user_id":uid,"full_name":name,"available":not bool(db.scalar(select(current_allocation).where(User.id==uid)))} for uid,name in availability_rows]
    upcoming=db.execute(select(PlannedVisit.id,PlannedVisit.visit_date,PlannedVisit.start_time,JobCard.id,JobCard.number,User.full_name).join(JobCard,JobCard.id==PlannedVisit.job_card_id).outerjoin(User,User.id==PlannedVisit.consultant_id).where(PlannedVisit.visit_date>=date.today(),PlannedVisit.status=="PLANNED",JobCard.deleted_at.is_(None)).order_by(PlannedVisit.visit_date,PlannedVisit.start_time).limit(12)).all()
    payload["upcoming_visits"]=[{"visit_id":vid,"visit_number":vid,"visit_date":visit_date,"start_time":str(start_time),"job_card_id":card_id,"job_card_number":card_number,"consultant":consultant} for vid,visit_date,start_time,card_id,card_number,consultant in upcoming]
    return payload
@app.get("/api/dashboard/marketing")
def marketing_dashboard(db:DB,user:Annotated[User,Depends(require(Role.MARKETING))]):
    _ensure_enquiry_sla_breaches(db,user);db.commit()
    open_statuses=("NEW","CONTACTED","QUALIFIED")
    enquiry_scope=[or_(Enquiry.created_by==user.id,Enquiry.assigned_marketing_user_id==user.id)]
    status_counts={str(status):int(count) for status,count in db.execute(select(Enquiry.status,func.count(Enquiry.id)).where(*enquiry_scope).group_by(Enquiry.status))}
    now=datetime.now(timezone.utc)
    followup_scope=[EnquiryFollowUp.next_follow_up_date.is_not(None),EnquiryFollowUp.status!="CANCELLED",Enquiry.status.in_(open_statuses),*enquiry_scope]
    followup_stmt=select(func.count(EnquiryFollowUp.id)).join(Enquiry,Enquiry.id==EnquiryFollowUp.enquiry_id).where(*followup_scope)
    followups_overdue=int(db.scalar(followup_stmt.where(EnquiryFollowUp.next_follow_up_date<now)) or 0)
    followups_upcoming=int(db.scalar(followup_stmt.where(EnquiryFollowUp.next_follow_up_date>=now)) or 0)
    total_enquiries=sum(status_counts.values())
    open_sla=int(db.scalar(select(func.count(EnquirySLABreach.id)).join(Enquiry,Enquiry.id==EnquirySLABreach.enquiry_id).where(EnquirySLABreach.status=="OPEN",*enquiry_scope)) or 0)
    return {"enquiries_by_status":status_counts,"total_enquiries":total_enquiries,"new_enquiries":status_counts.get("NEW",0),"converted_enquiries":status_counts.get("CONVERTED",0),"followups_due":followups_upcoming+followups_overdue,"followups_upcoming":followups_upcoming,"followups_overdue":followups_overdue,"open_sla_breaches":open_sla}
@app.get("/api/dashboard/user")
def user_dashboard(db:DB,user:Annotated[User,Depends(require(Role.END_USER))]):
    today=date.today();assigned=select(JobCard).join(Milestone,Milestone.job_card_id==JobCard.id).join(MilestoneConsultantAllocation,MilestoneConsultantAllocation.milestone_id==Milestone.id).where(MilestoneConsultantAllocation.consultant_id==user.id,MilestoneConsultantAllocation.active.is_(True),JobCard.deleted_at.is_(None)).distinct()
    assigned_count=int(db.scalar(select(func.count(func.distinct(JobCard.id))).select_from(JobCard).join(Milestone,Milestone.job_card_id==JobCard.id).join(MilestoneConsultantAllocation,MilestoneConsultantAllocation.milestone_id==Milestone.id).where(MilestoneConsultantAllocation.consultant_id==user.id,MilestoneConsultantAllocation.active.is_(True),JobCard.deleted_at.is_(None))) or 0)
    plan_rows=db.execute(select(Milestone.job_card_id,MilestoneConsultantAllocation.allocated_mandays).join(JobCard,JobCard.id==Milestone.job_card_id).join(MilestoneConsultantAllocation,MilestoneConsultantAllocation.milestone_id==Milestone.id).where(MilestoneConsultantAllocation.consultant_id==user.id,MilestoneConsultantAllocation.active.is_(True),Milestone.status.not_in(("COMPLETED","CANCELLED")),JobCard.deleted_at.is_(None))).all()
    planned=sum((Decimal(str(amount or 0)) for _,amount in plan_rows),Decimal(0))
    time_rows=db.execute(select(Visit.working_minutes,JobCard.hours_per_manday).join(JobCard,JobCard.id==Visit.job_card_id).where(or_(Visit.consultant_id==user.id,Visit.co_consultant_id==user.id,Visit.subject_expert_id==user.id,((Visit.consultant_id.is_(None))&(Visit.co_consultant_id.is_(None))&(Visit.subject_expert_id.is_(None))&(Visit.created_by==user.id))),Visit.status=="SUBMITTED",Visit.deleted_at.is_(None),JobCard.deleted_at.is_(None))).all()
    activity_time_rows=db.execute(select(JobActivity.duration_minutes,JobCard.hours_per_manday).join(JobCard,JobCard.id==JobActivity.job_card_id).where(JobActivity.assigned_user_id==user.id,JobActivity.status=="COMPLETED",JobActivity.visit_id.is_(None),JobCard.deleted_at.is_(None))).all()
    planned_visit_time_rows=[]
    for card_id in {card_id for card_id,_ in plan_rows}:
        card=db.get(JobCard,card_id)
        minutes=planner_actual_minutes(db,card,user.id) if card else 0
        if minutes: planned_visit_time_rows.append((minutes,card.hours_per_manday))
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
    planned_upcoming=int(db.scalar(select(func.count(PlannedVisit.id)).where(or_(PlannedVisit.consultant_id==user.id,PlannedVisit.co_consultant_id==user.id,PlannedVisit.subject_expert_id==user.id),PlannedVisit.visit_date>=today,PlannedVisit.status=="PLANNED")) or 0)
    overdue_activities=int(db.scalar(select(func.count(JobActivity.id)).where(JobActivity.assigned_user_id==user.id,JobActivity.status.in_(("PLANNED","IN_PROGRESS")),or_(and_(JobActivity.planned_visit_id.is_not(None),JobActivity.sla_due_at<datetime.now(timezone.utc)),and_(JobActivity.planned_visit_id.is_(None),JobActivity.end_date<today)))) or 0)
    due_today_tasks=int(db.scalar(select(func.count(WorkflowAction.id)).where(WorkflowAction.assigned_user_id==user.id,WorkflowAction.status.in_(("OPEN","IN_PROGRESS","WAITING","ON_HOLD")),func.date(WorkflowAction.due_at)==today)) or 0)
    active_milestones=int(db.scalar(select(func.count(MilestoneConsultantAllocation.id)).join(Milestone,Milestone.id==MilestoneConsultantAllocation.milestone_id).join(JobCard,JobCard.id==Milestone.job_card_id).where(MilestoneConsultantAllocation.consultant_id==user.id,MilestoneConsultantAllocation.active.is_(True),JobCard.deleted_at.is_(None),Milestone.status.not_in(("COMPLETED","CANCELLED")))) or 0)
    completed_activities=int(db.scalar(select(func.count(JobActivity.id)).where(JobActivity.assigned_user_id==user.id,JobActivity.status=="COMPLETED")) or 0)
    my_visits=int(db.scalar(select(func.count(Visit.id)).where(Visit.created_by==user.id,Visit.deleted_at.is_(None))) or 0)
    kpis={"total_projects":assigned_count,"assigned_job_cards":assigned_count,"active_milestones":active_milestones,"my_actions":len(open_actions),"my_visits":my_visits,"today_activities":today_visits,"today_visits":today_visits,"upcoming_visits":upcoming+planned_upcoming,"planned_visits":planned_upcoming,"pending_activities":pending,"pending_submissions":pending+pending_documents,"pending_documents":pending_documents,"returned_items":returned,"completed_activities":completed_activities,"due_today_tasks":due_today_tasks,"overdue_tasks":overdue_actions+overdue_activities,"overdue_activities":overdue_activities,"planned_mandays":planned.quantize(MD_PRECISION),"actual_mandays":actual,"completed_mandays":actual,"remaining_mandays":(planned-actual).quantize(MD_PRECISION)}
    recent=[{"id":card.id,"number":card.number,"status":projects[index]["status"],"location":card.location,"project_start_date":card.project_start_date,"project_end_date":card.project_end_date,"progress":projects[index]["progress"]} for index,card in enumerate(cards)]
    return {"role":user.role,"kpis":kpis,"projects":projects,"recent_job_cards":recent,"my_actions":[_workflow_out(db,row).model_dump(mode="json") for row in open_actions]}
