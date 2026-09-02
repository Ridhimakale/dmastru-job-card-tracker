from datetime import datetime, timezone
from datetime import date
from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .core import AccountStatus, Role
from .database import Base

def now(): return datetime.now(timezone.utc)
class User(Base):
    __tablename__="users"
    id: Mapped[int]=mapped_column(primary_key=True)
    full_name: Mapped[str]=mapped_column(String(160))
    email: Mapped[str]=mapped_column(String(320),unique=True,index=True)
    mobile: Mapped[str|None]=mapped_column(String(32),nullable=True)
    consultant_id: Mapped[str|None]=mapped_column(String(64),nullable=True)
    role: Mapped[Role]=mapped_column(Enum(Role),index=True)
    status: Mapped[AccountStatus]=mapped_column(Enum(AccountStatus),default=AccountStatus.PENDING_ACTIVATION,index=True)
    password_hash: Mapped[str]=mapped_column(String(512))
    failed_attempts: Mapped[int]=mapped_column(Integer,default=0)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
    last_login_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    reset_token_hash: Mapped[str|None]=mapped_column(String(128),nullable=True,index=True)
    reset_expires_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
class AuthSession(Base):
    __tablename__="auth_sessions"
    id: Mapped[str]=mapped_column(String(64),primary_key=True)
    user_id: Mapped[int]=mapped_column(ForeignKey("users.id"),index=True)
    expires_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),index=True)
    revoked: Mapped[bool]=mapped_column(Boolean,default=False)
    refresh_token_hash: Mapped[str|None]=mapped_column(String(128),nullable=True,index=True)
    refresh_expires_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
class MSME(Base):
    __tablename__="msmes"
    id: Mapped[int]=mapped_column(primary_key=True)
    name: Mapped[str]=mapped_column(String(200),unique=True,index=True)
    msme_code: Mapped[str]=mapped_column(String(64),unique=True,index=True)
    zed_id: Mapped[str|None]=mapped_column(String(64),nullable=True)
    certificate_level: Mapped[str]=mapped_column(String(32),default="Bronze")
    address: Mapped[str|None]=mapped_column(Text,nullable=True)
    city: Mapped[str|None]=mapped_column(String(80),nullable=True)
    state: Mapped[str|None]=mapped_column(String(80),nullable=True)
    contact_person: Mapped[str|None]=mapped_column(String(120),nullable=True)
    status: Mapped[str]=mapped_column(String(24),default="ACTIVE",index=True)
    deleted_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    deleted_by: Mapped[int|None]=mapped_column(ForeignKey("users.id"),nullable=True)
class JobCard(Base):
    __tablename__="job_cards"
    id: Mapped[int]=mapped_column(primary_key=True)
    number: Mapped[str]=mapped_column(String(64),unique=True,index=True)
    msme_id: Mapped[int]=mapped_column(ForeignKey("msmes.id"),index=True)
    location: Mapped[str]=mapped_column(String(160))
    project_start_date: Mapped[date]=mapped_column(Date)
    project_end_date: Mapped[date]=mapped_column(Date,index=True)
    required_md: Mapped[float]=mapped_column(Numeric(10,2))
    required_hours: Mapped[float]=mapped_column(Numeric(10,2))
    unit_representative_id: Mapped[int|None]=mapped_column(ForeignKey("users.id"),nullable=True)
    director_id: Mapped[int|None]=mapped_column(ForeignKey("users.id"),nullable=True)
    status: Mapped[str]=mapped_column(String(32),default="ASSIGNED",index=True)
    created_by: Mapped[int]=mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
    deleted_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    deleted_by: Mapped[int|None]=mapped_column(ForeignKey("users.id"),nullable=True)
class Assignment(Base):
    __tablename__="job_card_assignments"
    id: Mapped[int]=mapped_column(primary_key=True)
    job_card_id: Mapped[int]=mapped_column(ForeignKey("job_cards.id"),index=True)
    user_id: Mapped[int]=mapped_column(ForeignKey("users.id"),index=True)
    assignment_type: Mapped[str]=mapped_column(String(32))
    assigned_by: Mapped[int]=mapped_column(ForeignKey("users.id"))
    assigned_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
    ended_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    active: Mapped[bool]=mapped_column(Boolean,default=True,index=True)
class Milestone(Base):
    __tablename__="milestones"
    id: Mapped[int]=mapped_column(primary_key=True)
    job_card_id: Mapped[int]=mapped_column(ForeignKey("job_cards.id"),index=True)
    code: Mapped[str]=mapped_column(String(24))
    name: Mapped[str]=mapped_column(String(120))
    description: Mapped[str|None]=mapped_column(Text,nullable=True)
    planned_visits: Mapped[int]=mapped_column(Integer)
    start_date: Mapped[date|None]=mapped_column(Date,nullable=True)
    closing_date: Mapped[date|None]=mapped_column(Date,nullable=True)
    payment_date: Mapped[date|None]=mapped_column(Date,nullable=True)
    portal_report_submission_date: Mapped[date|None]=mapped_column(Date,nullable=True)
    status: Mapped[str]=mapped_column(String(32),default="NOT_STARTED")
class Visit(Base):
    __tablename__="visits"
    __table_args__=(UniqueConstraint("job_card_id","visit_number",name="uq_visit_number_per_job_card"),)
    id: Mapped[int]=mapped_column(primary_key=True)
    job_card_id: Mapped[int]=mapped_column(ForeignKey("job_cards.id"),index=True)
    milestone_id: Mapped[int]=mapped_column(ForeignKey("milestones.id"),index=True)
    visit_number: Mapped[int]=mapped_column(Integer)
    visit_date: Mapped[date]=mapped_column(Date,index=True)
    working_minutes: Mapped[int]=mapped_column(Integer)
    work_details: Mapped[str]=mapped_column(Text)
    rating_code: Mapped[str]=mapped_column(String(1))
    rating_description: Mapped[str]=mapped_column(String(32))
    remarks: Mapped[str|None]=mapped_column(Text,nullable=True)
    attachment_name: Mapped[str|None]=mapped_column(String(255),nullable=True)
    attachment_key: Mapped[str|None]=mapped_column(String(255),nullable=True)
    attachment_mime_type: Mapped[str|None]=mapped_column(String(120),nullable=True)
    status: Mapped[str]=mapped_column(String(24),default="DRAFT",index=True)
    created_by: Mapped[int]=mapped_column(ForeignKey("users.id"),index=True)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
    rejection_reason: Mapped[str|None]=mapped_column(Text,nullable=True)
    rejected_by: Mapped[int|None]=mapped_column(ForeignKey("users.id"),nullable=True)
    rejected_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    deleted_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    deleted_by: Mapped[int|None]=mapped_column(ForeignKey("users.id"),nullable=True)
class Document(Base):
    __tablename__="documents"
    id: Mapped[int]=mapped_column(primary_key=True)
    job_card_id: Mapped[int]=mapped_column(ForeignKey("job_cards.id"),index=True)
    document_type: Mapped[str]=mapped_column(String(80),index=True)
    file_name: Mapped[str]=mapped_column(String(255))
    storage_key: Mapped[str]=mapped_column(String(255),unique=True)
    file_size: Mapped[int]=mapped_column(Integer)
    mime_type: Mapped[str]=mapped_column(String(120))
    version: Mapped[int]=mapped_column(Integer,default=1)
    status: Mapped[str]=mapped_column(String(32),default="SUBMITTED",index=True)
    remarks: Mapped[str|None]=mapped_column(Text,nullable=True)
    uploaded_by: Mapped[int]=mapped_column(ForeignKey("users.id"))
    uploaded_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
    deleted_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    deleted_by: Mapped[int|None]=mapped_column(ForeignKey("users.id"),nullable=True)
class Approval(Base):
    __tablename__="approvals"
    id: Mapped[int]=mapped_column(primary_key=True)
    job_card_id: Mapped[int]=mapped_column(ForeignKey("job_cards.id"),index=True)
    stage: Mapped[str]=mapped_column(String(32),index=True)
    cycle: Mapped[int]=mapped_column(Integer,default=1,index=True)
    decision: Mapped[str]=mapped_column(String(32))
    comment: Mapped[str|None]=mapped_column(Text,nullable=True)
    decided_by: Mapped[int]=mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
class Notification(Base):
    __tablename__="notifications"
    id: Mapped[int]=mapped_column(primary_key=True)
    user_id: Mapped[int]=mapped_column(ForeignKey("users.id"),index=True)
    message: Mapped[str]=mapped_column(String(500))
    link: Mapped[str|None]=mapped_column(String(255),nullable=True)
    read_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now,index=True)
class AuditLog(Base):
    __tablename__="audit_logs"
    id: Mapped[int]=mapped_column(primary_key=True)
    actor_id: Mapped[int|None]=mapped_column(ForeignKey("users.id"),nullable=True,index=True)
    action: Mapped[str]=mapped_column(String(80),index=True)
    entity_type: Mapped[str]=mapped_column(String(80),index=True)
    entity_id: Mapped[int|None]=mapped_column(Integer,nullable=True,index=True)
    details: Mapped[str|None]=mapped_column(Text,nullable=True)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now,index=True)
class MasterData(Base):
    __tablename__="master_data"
    id: Mapped[int]=mapped_column(primary_key=True)
    category: Mapped[str]=mapped_column(String(64),index=True)
    code: Mapped[str]=mapped_column(String(64))
    label: Mapped[str]=mapped_column(String(160))
    active: Mapped[bool]=mapped_column(Boolean,default=True,index=True)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
class SystemSetting(Base):
    __tablename__="system_settings"
    key: Mapped[str]=mapped_column(String(100),primary_key=True)
    value: Mapped[str]=mapped_column(Text)
    updated_by: Mapped[int|None]=mapped_column(ForeignKey("users.id"),nullable=True)
    updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
