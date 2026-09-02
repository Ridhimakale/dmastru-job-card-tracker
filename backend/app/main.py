import base64
import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from datetime import date
from decimal import Decimal
from typing import Annotated
import logging
import time
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from .core import AccountStatus, ENV, REFRESH_EXPIRY_DAYS, Role, check_password_rules, decode_token, issue_token, passwords, JWT_EXPIRY_MINUTES
from .database import Base, engine, get_db
from .models import Approval, Assignment, AuditLog, AuthSession, Document, JobCard, MasterData, Milestone, MSME, Notification, SystemSetting, User, Visit
from .seed import seed
from .services import MD_PRECISION, card_dashboard_row, manday_summary, milestone_summary
from .storage import save as save_file
from .events import audit, notify
from .reports import consultant_rows, milestone_rows, project_rows, to_csv, to_pdf, to_xlsx

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
class UserCreate(EmailModel): full_name: str=Field(min_length=2,max_length=160); password: str; mobile: str|None=None; consultant_id: str|None=None; role: Role=Role.END_USER
class UserAdminUpdate(BaseModel): full_name:str|None=Field(default=None,min_length=2,max_length=160); mobile:str|None=None; consultant_id:str|None=None; role:Role|None=None
class AdminPasswordReset(BaseModel): new_password:str
class UserOut(EmailModel):
    model_config=ConfigDict(from_attributes=True)
    id:int; full_name:str; role:Role; status:AccountStatus; mobile:str|None=None
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
class JobCardUpdate(BaseModel): location:str|None=Field(default=None,min_length=2,max_length=160); project_start_date:date|None=None; project_end_date:date|None=None; required_md:Decimal|None=Field(default=None,gt=0); status:str|None=None
class ReassignmentIn(BaseModel): user_id:int; assignment_type:str=Field(pattern="^(LEAD_CONSULTANT|CO_CONSULTANT|SUBJECT_EXPERT)$")
class ReopenIn(BaseModel): reason:str=Field(min_length=3,max_length=5000)
class VisitRejectIn(BaseModel): reason:str=Field(min_length=3,max_length=5000)
class MSMECreate(BaseModel):
    name:str=Field(min_length=2,max_length=200); msme_code:str=Field(min_length=2,max_length=64); zed_id:str|None=None; certificate_level:str="Bronze"; address:str|None=None; city:str|None=None; state:str|None=None; contact_person:str|None=None
class MSMEUpdate(BaseModel):
    name:str|None=Field(default=None,min_length=2,max_length=200); msme_code:str|None=Field(default=None,min_length=2,max_length=64); zed_id:str|None=None; certificate_level:str|None=None; address:str|None=None; city:str|None=None; state:str|None=None; contact_person:str|None=None; status:str|None=Field(default=None,pattern="^(ACTIVE|INACTIVE)$")
class MSMEOut(MSMECreate):
    model_config=ConfigDict(from_attributes=True)
    id:int; status:str
class JobCardCreate(BaseModel):
    number:str=Field(min_length=3,max_length=64); msme_id:int; location:str=Field(min_length=2,max_length=160); project_start_date:date; project_end_date:date; required_md:Decimal=Field(gt=0); lead_consultant_id:int; co_consultant_id:int|None=None; subject_expert_id:int|None=None; unit_representative_id:int|None=None; director_id:int|None=None
class JobCardOut(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id:int; number:str; msme_id:int; location:str; project_start_date:date; project_end_date:date; required_md:Decimal; required_hours:Decimal; unit_representative_id:int|None=None; director_id:int|None=None; status:str
class MilestoneCreate(BaseModel): code:str=Field(min_length=1,max_length=24); name:str=Field(min_length=2,max_length=120); planned_visits:int=Field(ge=1); description:str|None=None; start_date:date|None=None; closing_date:date|None=None; payment_date:date|None=None; portal_report_submission_date:date|None=None
class MilestoneOut(MilestoneCreate):
    model_config=ConfigDict(from_attributes=True)
    id:int; job_card_id:int; status:str; actual_visits:int=0; progress:Decimal=Decimal(0)
class VisitCreate(BaseModel): milestone_id:int; visit_date:date; hours:int=Field(ge=0); minutes:int=Field(ge=0,lt=60); work_details:str=Field(min_length=3,max_length=10000); rating_code:str=Field(pattern="^[ABCDE]$"); remarks:str|None=None; attachment_name:str|None=None; attachment_mime_type:str|None=None; attachment_base64:str|None=None
class VisitUpdate(VisitCreate): pass
class VisitOut(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id:int; job_card_id:int; milestone_id:int; visit_number:int; visit_date:date; working_minutes:int; work_details:str; rating_code:str; rating_description:str; remarks:str|None=None; attachment_name:str|None=None; attachment_mime_type:str|None=None; status:str
class DocumentUpload(BaseModel):
    document_type:str=Field(min_length=2,max_length=80); file_name:str=Field(min_length=1,max_length=255); mime_type:str; content_base64:str; remarks:str|None=None
class DocumentOut(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id:int; job_card_id:int; document_type:str; file_name:str; file_size:int; mime_type:str; version:int; status:str; remarks:str|None=None; uploaded_by:int
class DocumentReview(BaseModel): decision:str=Field(pattern="^(APPROVED|REJECTED)$"); comment:str=Field(min_length=3,max_length=5000)
class ApprovalAction(BaseModel): decision:str=Field(pattern="^(APPROVED|REJECTED)$"); comment:str|None=Field(default=None,max_length=5000)
class ApprovalOut(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id:int; job_card_id:int; stage:str; cycle:int; decision:str; comment:str|None=None; decided_by:int
class NotificationOut(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id:int; message:str; link:str|None=None; read_at:datetime|None=None; created_at:datetime
class AuditOut(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id:int; actor_id:int|None=None; action:str; entity_type:str; entity_id:int|None=None; details:str|None=None; created_at:datetime

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

@app.on_event("startup")
def bootstrap():
    # Explicit Alembic migrations own production schema changes. This fallback keeps local tests/dev runnable.
    Base.metadata.create_all(engine)
    seed()

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
    if ENV=="test": response["development_reset_token"]=raw
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
    user=User(full_name=data.full_name,email=str(data.email).lower(),mobile=data.mobile,consultant_id=data.consultant_id,role=data.role,status=AccountStatus.ACTIVE if data.role!=Role.END_USER else AccountStatus.PENDING_ACTIVATION,password_hash=passwords.hash(data.password)); db.add(user); db.flush(); audit(db,admin.id,"USER_CREATED","USER",user.id,user.email); db.commit(); db.refresh(user); return user

@app.get("/api/users",response_model=list[UserOut])
def list_users(db:DB,admin:Annotated[User,Depends(require(Role.ADMIN))]): return list(db.scalars(select(User).order_by(User.full_name)))

@app.patch("/api/users/{user_id}",response_model=UserOut)
def update_user(user_id:int,data:UserAdminUpdate,db:DB,admin:Annotated[User,Depends(require(Role.ADMIN))]):
    user=db.get(User,user_id)
    if not user: raise HTTPException(404,"User not found.")
    updates=data.model_dump(exclude_none=True)
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
    msme=MSME(**data.model_dump()); db.add(msme); db.flush(); audit(db,admin.id,"MSME_CREATED","MSME",msme.id,msme.msme_code); db.commit(); db.refresh(msme); return msme

@app.patch("/api/msmes/{msme_id}",response_model=MSMEOut)
def update_msme(msme_id:int,data:MSMEUpdate,db:DB,admin:Annotated[User,Depends(require(Role.ADMIN))]):
    msme=db.get(MSME,msme_id)
    if not msme or msme.deleted_at: raise HTTPException(404,"MSME not found.")
    updates=data.model_dump(exclude_none=True)
    if "name" in updates and db.scalar(select(MSME).where(MSME.name==updates["name"],MSME.id!=msme_id)): raise HTTPException(409,"MSME name already exists.")
    if "msme_code" in updates and db.scalar(select(MSME).where(MSME.msme_code==updates["msme_code"],MSME.id!=msme_id)): raise HTTPException(409,"MSME code already exists.")
    changes=[]
    for field,value in updates.items():
        previous=getattr(msme,field)
        if previous!=value: setattr(msme,field,value); changes.append(f"{field}:{previous}->{value}")
    if changes: audit(db,admin.id,"MSME_UPDATED","MSME",msme.id,"; ".join(changes))
    db.commit(); db.refresh(msme); return msme

@app.get("/api/msmes",response_model=list[MSMEOut])
def list_msmes(db:DB,user:Annotated[User,Depends(require(Role.ADMIN,Role.MANAGEMENT))],search:str|None=None,certificate_level:str|None=None,status_filter:str|None=Query(default=None,alias="status")):
    query=select(MSME).where(MSME.deleted_at==None)
    if search:
        term=f"%{search}%"; query=query.where((MSME.name.ilike(term))|(MSME.msme_code.ilike(term))|(MSME.zed_id.ilike(term)))
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
    setting=db.get(SystemSetting,key)
    if setting: setting.value=data.value; setting.updated_by=admin.id; setting.updated_at=datetime.now(timezone.utc)
    else: setting=SystemSetting(key=key,value=data.value,updated_by=admin.id); db.add(setting)
    audit(db,admin.id,"SETTING_UPDATED","SETTING",None,key); db.commit(); db.refresh(setting); return setting
@app.get("/api/settings/{key}",response_model=SettingOut)
def get_setting(key:str,db:DB,user:Annotated[User,Depends(require(Role.ADMIN))]):
    setting=db.get(SystemSetting,key)
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

@app.post("/api/job-cards",response_model=JobCardOut,status_code=201)
def create_job_card(data:JobCardCreate,db:DB,admin:Annotated[User,Depends(require(Role.ADMIN))]):
    if data.project_end_date < data.project_start_date: raise HTTPException(422,"Project end date cannot be before the start date.")
    if not db.get(MSME,data.msme_id): raise HTTPException(422,"Selected MSME does not exist.")
    if db.scalar(select(JobCard).where(JobCard.number==data.number)): raise HTTPException(409,"Job Card number already exists.")
    ids=[(data.lead_consultant_id,"LEAD_CONSULTANT"),(data.co_consultant_id,"CO_CONSULTANT"),(data.subject_expert_id,"SUBJECT_EXPERT")]
    seen=set()
    for user_id,_ in ids:
        if user_id:
            if user_id in seen: raise HTTPException(422,"A consultant cannot occupy more than one role on a Job Card.")
            seen.add(user_id); active_consultant(db,user_id)
    if not data.unit_representative_id or not data.director_id: raise HTTPException(422,"A Unit Representative and Director must be assigned before creating a Job Card.")
    active_reviewer(db,data.unit_representative_id,Role.UNIT_REPRESENTATIVE); active_reviewer(db,data.director_id,Role.DIRECTOR)
    card=JobCard(number=data.number,msme_id=data.msme_id,location=data.location,project_start_date=data.project_start_date,project_end_date=data.project_end_date,required_md=data.required_md,required_hours=data.required_md*Decimal(8),unit_representative_id=data.unit_representative_id,director_id=data.director_id,created_by=admin.id)
    db.add(card); db.flush()
    for user_id,kind in ids:
        if user_id:
            db.add(Assignment(job_card_id=card.id,user_id=user_id,assignment_type=kind,assigned_by=admin.id)); notify(db,user_id,f"You were assigned as {kind.replace('_',' ').title()} on Job Card {card.number}.",f"/job-cards/{card.id}")
    audit(db,admin.id,"JOB_CARD_CREATED","JOB_CARD",card.id,card.number)
    db.commit(); db.refresh(card); return card

@app.get("/api/job-cards",response_model=list[JobCardOut])
def list_job_cards(db:DB,user:Annotated[User,Depends(current_user)],status_filter:str|None=Query(default=None,alias="status"),msme_id:int|None=None,consultant_id:int|None=None,search:str|None=None,zed_id:str|None=None,certificate_level:str|None=None,delayed:bool|None=None,overrun:bool|None=None,page:int=Query(default=1,ge=1),page_size:int=Query(default=25,ge=1,le=100)):
    query=select(JobCard).where(JobCard.deleted_at==None)
    if user.role==Role.END_USER: query=query.join(Assignment,Assignment.job_card_id==JobCard.id).where(Assignment.user_id==user.id,Assignment.active==True)
    elif consultant_id: query=query.join(Assignment,Assignment.job_card_id==JobCard.id).where(Assignment.user_id==consultant_id,Assignment.active==True)
    if status_filter: query=query.where(JobCard.status==status_filter)
    if msme_id: query=query.where(JobCard.msme_id==msme_id)
    if zed_id or certificate_level or search:
        query=query.join(MSME,MSME.id==JobCard.msme_id)
        if zed_id: query=query.where(MSME.zed_id.ilike(f"%{zed_id}%"))
        if certificate_level: query=query.where(MSME.certificate_level==certificate_level)
        if search: query=query.where((JobCard.number.ilike(f"%{search}%"))|(MSME.name.ilike(f"%{search}%"))|(MSME.zed_id.ilike(f"%{search}%")))
    cards=list(db.scalars(query.order_by(JobCard.created_at.desc())))
    if delayed is not None or overrun is not None:
        cards=[card for card in cards if (delayed is None or (card_dashboard_row(db,card)["status"]=="DELAYED")==delayed) and (overrun is None or card_dashboard_row(db,card)["overrun"]==overrun)]
    return cards[(page-1)*page_size:page*page_size]

@app.get("/api/job-cards/{card_id}",response_model=JobCardOut)
def get_job_card(card_id:int,db:DB,user:Annotated[User,Depends(current_user)]):
    card=db.get(JobCard,card_id)
    if not card or card.deleted_at: raise HTTPException(404,"Job Card not found.")
    if user.role==Role.END_USER and not db.scalar(select(Assignment).where(Assignment.job_card_id==card_id,Assignment.user_id==user.id,Assignment.active==True)): raise HTTPException(403,"You are not assigned to this Job Card.")
    return card

@app.patch("/api/job-cards/{card_id}",response_model=JobCardOut)
def update_job_card(card_id:int,data:JobCardUpdate,db:DB,admin:Annotated[User,Depends(require(Role.ADMIN))]):
    card=mutable_card(assigned_card(db,admin,card_id)); updates=data.model_dump(exclude_none=True)
    start=updates.get("project_start_date",card.project_start_date); end=updates.get("project_end_date",card.project_end_date)
    if end<start: raise HTTPException(422,"Project end date cannot be before the start date.")
    if "required_md" in updates: updates["required_hours"]=updates["required_md"]*Decimal(8)
    for field,value in updates.items(): setattr(card,field,value)
    audit(db,admin.id,"JOB_CARD_UPDATED","JOB_CARD",card.id,",".join(updates)); db.commit(); db.refresh(card); return card
@app.post("/api/job-cards/{card_id}/reassign",response_model=JobCardOut)
def reassign(card_id:int,data:ReassignmentIn,db:DB,admin:Annotated[User,Depends(require(Role.ADMIN))]):
    card=mutable_card(assigned_card(db,admin,card_id)); active_consultant(db,data.user_id)
    for assignment in db.scalars(select(Assignment).where(Assignment.job_card_id==card_id,Assignment.assignment_type==data.assignment_type,Assignment.active==True)):
        assignment.active=False; assignment.ended_at=datetime.now(timezone.utc)
    db.add(Assignment(job_card_id=card_id,user_id=data.user_id,assignment_type=data.assignment_type,assigned_by=admin.id)); notify(db,data.user_id,f"You were assigned as {data.assignment_type.replace('_',' ').title()} on Job Card {card.number}.",f"/job-cards/{card.id}"); audit(db,admin.id,"JOB_CARD_REASSIGNED","JOB_CARD",card.id,data.assignment_type); db.commit(); db.refresh(card); return card
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
    return card

def mutable_card(card:JobCard):
    if card.status=="COMPLETED": raise HTTPException(409,"Completed Job Cards are read-only. An Admin must reopen the Job Card before changes can be made.")
    return card

@app.post("/api/job-cards/{card_id}/milestones",response_model=MilestoneOut,status_code=201)
def create_milestone(card_id:int,data:MilestoneCreate,db:DB,admin:Annotated[User,Depends(require(Role.ADMIN))]):
    mutable_card(assigned_card(db,admin,card_id))
    if data.closing_date and data.start_date and data.closing_date<data.start_date: raise HTTPException(422,"Milestone closing date cannot be before its start date.")
    if data.payment_date and data.closing_date and data.payment_date<data.closing_date: raise HTTPException(422,"Milestone payment date cannot be before its closing date.")
    if data.portal_report_submission_date and data.start_date and data.portal_report_submission_date<data.start_date: raise HTTPException(422,"Portal report submission date cannot be before the milestone start date.")
    if db.scalar(select(Milestone).where(Milestone.job_card_id==card_id,Milestone.code==data.code)): raise HTTPException(409,"Milestone code already exists on this Job Card.")
    milestone=Milestone(job_card_id=card_id,**data.model_dump()); db.add(milestone); db.flush(); audit(db,admin.id,"MILESTONE_CREATED","MILESTONE",milestone.id,milestone.code); db.commit(); db.refresh(milestone); return MilestoneOut(**{**milestone.__dict__,**milestone_summary(db,milestone)})

@app.get("/api/job-cards/{card_id}/milestones",response_model=list[MilestoneOut])
def list_milestones(card_id:int,db:DB,user:Annotated[User,Depends(current_user)]):
    assigned_card(db,user,card_id); return [MilestoneOut(**{**m.__dict__,**milestone_summary(db,m)}) for m in db.scalars(select(Milestone).where(Milestone.job_card_id==card_id).order_by(Milestone.code))]

def visit_output(visit:Visit): return VisitOut.model_validate(visit)
RATINGS={"A":"Excellent","B":"Very Good","C":"Good","D":"Poor","E":"Not Satisfactory"}
@app.post("/api/job-cards/{card_id}/visits/draft",response_model=VisitOut,status_code=201)
def save_draft(card_id:int,data:VisitCreate,db:DB,user:Annotated[User,Depends(require(Role.END_USER))]):
    card=mutable_card(assigned_card(db,user,card_id)); milestone=db.get(Milestone,data.milestone_id)
    if not milestone or milestone.job_card_id!=card_id: raise HTTPException(422,"The selected milestone is not available for this Job Card.")
    if not card.project_start_date<=data.visit_date<=card.project_end_date: raise HTTPException(422,"Visit date must fall within the project dates.")
    attachment={}
    if any((data.attachment_name,data.attachment_mime_type,data.attachment_base64)):
        if not all((data.attachment_name,data.attachment_mime_type,data.attachment_base64)): raise HTTPException(422,"Visit attachment name, type, and content are all required.")
        try: attachment={"attachment_name":data.attachment_name,"attachment_mime_type":data.attachment_mime_type,"attachment_key":save_file(data.attachment_name,data.attachment_mime_type,base64.b64decode(data.attachment_base64,validate=True))}
        except Exception: raise HTTPException(422,"The visit attachment is invalid.")
    next_number=int(db.scalar(select(func.coalesce(func.max(Visit.visit_number),0)+1).where(Visit.job_card_id==card_id)) or 1)
    visit=Visit(job_card_id=card_id,milestone_id=milestone.id,visit_number=next_number,visit_date=data.visit_date,working_minutes=data.hours*60+data.minutes,work_details=data.work_details,rating_code=data.rating_code,rating_description=RATINGS[data.rating_code],remarks=data.remarks,created_by=user.id,status="DRAFT",**attachment); db.add(visit); db.flush(); audit(db,user.id,"VISIT_DRAFT_CREATED","VISIT",visit.id,f"Visit {next_number}"); db.commit(); db.refresh(visit); return visit

@app.patch("/api/visits/{visit_id}",response_model=VisitOut)
def edit_visit(visit_id:int,data:VisitUpdate,db:DB,user:Annotated[User,Depends(require(Role.END_USER))]):
    visit=db.get(Visit,visit_id)
    if not visit or visit.created_by!=user.id or visit.status!="DRAFT": raise HTTPException(409,"Only your draft visits can be edited.")
    card=mutable_card(assigned_card(db,user,visit.job_card_id)); milestone=db.get(Milestone,data.milestone_id)
    if not milestone or milestone.job_card_id!=card.id or not card.project_start_date<=data.visit_date<=card.project_end_date: raise HTTPException(422,"Invalid milestone or visit date.")
    visit.milestone_id=data.milestone_id; visit.visit_date=data.visit_date; visit.working_minutes=data.hours*60+data.minutes; visit.work_details=data.work_details; visit.rating_code=data.rating_code; visit.rating_description=RATINGS[data.rating_code]; visit.remarks=data.remarks
    audit(db,user.id,"VISIT_UPDATED","VISIT",visit.id,"Draft corrected")
    db.commit(); db.refresh(visit); return visit

@app.get("/api/visits/review-queue",response_model=list[VisitOut])
def visit_review_queue(db:DB,user:Annotated[User,Depends(require(Role.MANAGEMENT))]):
    return list(db.scalars(select(Visit).where(Visit.status=="SUBMITTED",Visit.deleted_at==None).order_by(Visit.created_at)))

@app.post("/api/visits/{visit_id}/submit",response_model=VisitOut)
def submit_visit(visit_id:int,db:DB,user:Annotated[User,Depends(require(Role.END_USER))]):
    visit=db.get(Visit,visit_id)
    if not visit or visit.created_by!=user.id: raise HTTPException(404,"Draft visit not found.")
    if visit.status!="DRAFT": raise HTTPException(409,"Only draft visits can be submitted.")
    card=mutable_card(assigned_card(db,user,visit.job_card_id))
    if not card.project_start_date<=visit.visit_date<=card.project_end_date: raise HTTPException(422,"Visit date must fall within the project dates.")
    if visit.working_minutes<=0: raise HTTPException(422,"A submitted visit must have a positive duration.")
    visit.status="SUBMITTED"; card.status="IN_PROGRESS"; audit(db,user.id,"VISIT_SUBMITTED","VISIT",visit.id,f"Visit {visit.visit_number}"); notify(db,card.created_by,f"Visit {visit.visit_number} was submitted for Job Card {card.number}.",f"/job-cards/{card.id}"); db.commit(); db.refresh(visit); return visit

@app.get("/api/job-cards/{card_id}/visits",response_model=list[VisitOut])
def list_visits(card_id:int,db:DB,user:Annotated[User,Depends(current_user)]):
    assigned_card(db,user,card_id); return list(db.scalars(select(Visit).where(Visit.job_card_id==card_id,Visit.deleted_at==None).order_by(Visit.visit_number.desc())))

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
    mutable_card(assigned_card(db,user,visit.job_card_id))
    if visit.status!="SUBMITTED": raise HTTPException(409,"Only submitted visits can be rejected.")
    visit.status="REJECTED"; visit.rejection_reason=data.reason; visit.rejected_by=user.id; visit.rejected_at=datetime.now(timezone.utc); audit(db,user.id,"VISIT_REJECTED","VISIT",visit.id,data.reason); notify(db,visit.created_by,f"Visit {visit.visit_number} was rejected: {data.reason}",f"/visits/{visit.id}"); db.commit(); db.refresh(visit); return visit
@app.post("/api/visits/{visit_id}/resubmit",response_model=VisitOut)
def resubmit_visit(visit_id:int,db:DB,user:Annotated[User,Depends(require(Role.END_USER))]):
    visit=db.get(Visit,visit_id)
    if not visit or visit.deleted_at or visit.created_by!=user.id: raise HTTPException(404,"Rejected visit not found.")
    mutable_card(assigned_card(db,user,visit.job_card_id))
    if visit.status!="REJECTED": raise HTTPException(409,"Only rejected visits can be resubmitted.")
    visit.status="DRAFT"; visit.rejection_reason=None; visit.rejected_by=None; visit.rejected_at=None; audit(db,user.id,"VISIT_RESUBMITTED","VISIT",visit.id); db.commit(); db.refresh(visit); return visit
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
    mutable_card(assigned_card(db,user,card_id))
    try: content=base64.b64decode(data.content_base64,validate=True); storage_key=save_file(data.file_name,data.mime_type,content)
    except ValueError as exc: raise HTTPException(422,str(exc))
    except Exception: raise HTTPException(422,"The uploaded file is invalid.")
    prior=int(db.scalar(select(func.coalesce(func.max(Document.version),0)).where(Document.job_card_id==card_id,Document.document_type==data.document_type)) or 0)
    document=Document(job_card_id=card_id,document_type=data.document_type,file_name=data.file_name,storage_key=storage_key,file_size=len(content),mime_type=data.mime_type,version=prior+1,status="RESUBMITTED" if prior else "SUBMITTED",remarks=data.remarks,uploaded_by=user.id)
    db.add(document); db.flush(); audit(db,user.id,"DOCUMENT_UPLOADED","DOCUMENT",document.id,f"{data.document_type} v{document.version}"); notify(db,card_created_by(db,card_id),f"{data.document_type} was submitted for review.",f"/job-cards/{card_id}"); db.commit(); db.refresh(document); return document

@app.get("/api/job-cards/{card_id}/documents",response_model=list[DocumentOut])
def list_documents(card_id:int,db:DB,user:Annotated[User,Depends(current_user)]):
    assigned_card(db,user,card_id); return list(db.scalars(select(Document).where(Document.job_card_id==card_id,Document.deleted_at==None).order_by(Document.document_type,Document.version.desc())))

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
    if not document: raise HTTPException(404,"Document not found.")
    mutable_card(assigned_card(db,user,document.job_card_id))
    document.status=data.decision; document.remarks=data.comment; audit(db,user.id,f"DOCUMENT_{data.decision}","DOCUMENT",document.id,data.comment); owner=document_owner(db,document.job_card_id); notify(db,owner,f"Your {document.document_type} was {data.decision.lower()}. {data.comment}",f"/job-cards/{document.job_card_id}"); db.commit(); db.refresh(document); return document

STAGES=("CONSULTANT_CONFIRMATION","UNIT_REPRESENTATIVE","DIRECTOR_REVIEW")
STAGE_ROLES={"CONSULTANT_CONFIRMATION":Role.END_USER,"UNIT_REPRESENTATIVE":Role.UNIT_REPRESENTATIVE,"DIRECTOR_REVIEW":Role.DIRECTOR}
def required_documents_ready(db:Session,card_id:int):
    types={x[0] for x in db.execute(select(Document.document_type).where(Document.job_card_id==card_id,Document.status=="APPROVED")).all()}
    return {"Case Study","Technology Recommendation"}.issubset(types)
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
        if not milestones_complete(db,card_id) or not required_documents_ready(db,card_id): raise HTTPException(422,"All milestones and required approved documents are needed before consultant confirmation.")
    current_cycle=int(db.scalar(select(func.coalesce(func.max(Approval.cycle),0)).where(Approval.job_card_id==card_id)) or 0) or 1
    if stage=="CONSULTANT_CONFIRMATION" and card.status=="REJECTED": current_cycle+=1
    previous=STAGES.index(stage)-1
    if previous>=0 and not db.scalar(select(Approval).where(Approval.job_card_id==card_id,Approval.cycle==current_cycle,Approval.stage==STAGES[previous],Approval.decision=="APPROVED")): raise HTTPException(422,"The previous approval stage must be approved in this review cycle.")
    if db.scalar(select(Approval).where(Approval.job_card_id==card_id,Approval.cycle==current_cycle,Approval.stage==stage)): raise HTTPException(409,"This approval stage has already been decided in the current review cycle.")
    if data.decision=="REJECTED" and not data.comment: raise HTTPException(422,"A rejection reason is required.")
    approval=Approval(job_card_id=card_id,cycle=current_cycle,stage=stage,decision=data.decision,comment=data.comment,decided_by=user.id); db.add(approval)
    card.status="REJECTED" if data.decision=="REJECTED" else ("COMPLETED" if stage=="DIRECTOR_REVIEW" else "PENDING_APPROVAL")
    db.flush(); audit(db,user.id,f"APPROVAL_{data.decision}","APPROVAL",approval.id,stage)
    if data.decision=="REJECTED":
        notify(db,document_owner(db,card_id),f"{stage.replace('_',' ').title()} rejected Job Card {card.number}. {data.comment}",f"/job-cards/{card_id}")
    elif stage=="CONSULTANT_CONFIRMATION":
        notify(db,approval_owner(db,card,"UNIT_REPRESENTATIVE"),f"Unit Representative approval is required for Job Card {card.number}.",f"/job-cards/{card_id}")
    elif stage=="UNIT_REPRESENTATIVE":
        notify(db,approval_owner(db,card,"DIRECTOR_REVIEW"),f"Director approval is required for Job Card {card.number}.",f"/job-cards/{card_id}")
    else:
        notify(db,document_owner(db,card_id),f"Job Card {card.number} is completed.",f"/job-cards/{card_id}")
        notify(db,card.created_by,f"Job Card {card.number} received final Director approval and is completed.",f"/job-cards/{card_id}")
    db.commit(); db.refresh(approval); return approval

@app.get("/api/job-cards/{card_id}/approvals",response_model=list[ApprovalOut])
def list_approvals(card_id:int,db:DB,user:Annotated[User,Depends(current_user)]):
    assigned_card(db,user,card_id); return list(db.scalars(select(Approval).where(Approval.job_card_id==card_id).order_by(Approval.id)))

def document_owner(db:Session,card_id:int):
    return db.scalar(select(Assignment.user_id).where(Assignment.job_card_id==card_id,Assignment.assignment_type=="LEAD_CONSULTANT",Assignment.active==True))
def card_created_by(db:Session,card_id:int): return db.get(JobCard,card_id).created_by
@app.get("/api/notifications",response_model=list[NotificationOut])
def list_notifications(db:DB,user:Annotated[User,Depends(current_user)]): return list(db.scalars(select(Notification).where(Notification.user_id==user.id).order_by(Notification.created_at.desc())))
@app.post("/api/notifications/{notification_id}/read",response_model=NotificationOut)
def mark_notification_read(notification_id:int,db:DB,user:Annotated[User,Depends(current_user)]):
    item=db.get(Notification,notification_id)
    if not item or item.user_id!=user.id: raise HTTPException(404,"Notification not found.")
    item.read_at=datetime.now(timezone.utc); db.commit(); db.refresh(item); return item
@app.get("/api/activity",response_model=list[AuditOut])
def my_activity(db:DB,user:Annotated[User,Depends(current_user)]): return list(db.scalars(select(AuditLog).where(AuditLog.actor_id==user.id).order_by(AuditLog.created_at.desc())))
@app.get("/api/audit-logs",response_model=list[AuditOut])
def audit_logs(db:DB,user:Annotated[User,Depends(require(Role.ADMIN,Role.MANAGEMENT))]): return list(db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(200)))

REPORTS={"projects":project_rows,"consultants":consultant_rows,"milestones":milestone_rows}
def report_data(kind:str,db:Session,status_filter:str|None=None,search:str|None=None,delayed:bool|None=None,overrun:bool|None=None):
    builder=REPORTS.get(kind)
    if not builder: raise HTTPException(404,"Report type not found.")
    rows=builder(db)
    if kind=="projects":
        rows=[row for row in rows if (not status_filter or row["Status"]==status_filter) and (not search or search.lower() in " ".join(map(str,row.values())).lower()) and (delayed is None or (int(row["Delay Days"])>0)==delayed) and (overrun is None or row["Overrun"]==overrun)]
    return rows
@app.get("/api/reports/{kind}")
def get_report(kind:str,db:DB,user:Annotated[User,Depends(require(Role.ADMIN,Role.MANAGEMENT))],status_filter:str|None=Query(default=None,alias="status"),search:str|None=None,delayed:bool|None=None,overrun:bool|None=None): return report_data(kind,db,status_filter,search,delayed,overrun)
@app.get("/api/reports/{kind}/export/{format}")
def export_report(kind:str,format:str,db:DB,user:Annotated[User,Depends(require(Role.ADMIN,Role.MANAGEMENT))],status_filter:str|None=Query(default=None,alias="status"),search:str|None=None,delayed:bool|None=None,overrun:bool|None=None):
    rows=report_data(kind,db,status_filter,search,delayed,overrun)
    if format=="csv": content=to_csv(rows); media="text/csv"; ext="csv"
    elif format=="xlsx": content=to_xlsx(rows); media="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"; ext="xlsx"
    elif format=="pdf": content=to_pdf(f"D'Mastru {kind.title()} Report",rows); media="application/pdf"; ext="pdf"
    else: raise HTTPException(404,"Supported formats are csv, xlsx, and pdf.")
    audit(db,user.id,"REPORT_EXPORTED","REPORT",None,f"{kind}.{ext}"); db.commit()
    return Response(content=content,media_type=media,headers={"Content-Disposition":f'attachment; filename="{kind}-report.{ext}"'})

def dashboard_cards(db:Session, user:User):
    query=select(JobCard)
    if user.role==Role.END_USER: query=query.join(Assignment,Assignment.job_card_id==JobCard.id).where(Assignment.user_id==user.id,Assignment.active==True)
    return [card_dashboard_row(db,c) for c in db.scalars(query)]
def dashboard_payload(db:Session,user:User):
    cards=dashboard_cards(db,user)
    completed=sum((c["completed_md"] for c in cards),Decimal(0)); required=sum((Decimal(str(db.get(JobCard,c["id"]).required_md)) for c in cards),Decimal(0))
    active=sum(c["status"] in ("ASSIGNED","IN_PROGRESS") for c in cards); delayed=[c for c in cards if c["status"]=="DELAYED"]
    return {"role":user.role,"kpis":{"total_projects":len(cards),"active_projects":active,"completed_projects":sum(c["status"]=="COMPLETED" for c in cards),"delayed_projects":len(delayed),"required_md":required,"completed_md":completed.quantize(MD_PRECISION),"remaining_md":(required-completed).quantize(MD_PRECISION)},"manday_chart":{"completed_md":completed.quantize(MD_PRECISION),"remaining_md":max(required-completed,Decimal(0)).quantize(MD_PRECISION)},"status_chart":{"active":active,"completed":sum(c["status"]=="COMPLETED" for c in cards),"delayed":len(delayed),"on_hold":sum(c["status"]=="ON_HOLD" for c in cards)},"attention":[c for c in cards if c["overrun"] or c["status"]=="DELAYED"],"projects":cards}

@app.get("/api/dashboard/management")
def management_dashboard(db:DB,user:Annotated[User,Depends(require(Role.MANAGEMENT))]): return dashboard_payload(db,user)
@app.get("/api/dashboard/admin")
def admin_dashboard(db:DB,user:Annotated[User,Depends(require(Role.ADMIN))]):
    payload=dashboard_payload(db,user); payload["kpis"]["total_users"]=int(db.scalar(select(func.count(User.id))) or 0); payload["kpis"]["total_consultants"]=int(db.scalar(select(func.count(User.id)).where(User.role==Role.END_USER)) or 0); return payload
@app.get("/api/dashboard/user")
def user_dashboard(db:DB,user:Annotated[User,Depends(require(Role.END_USER))]): return dashboard_payload(db,user)
