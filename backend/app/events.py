"""Workflow event helpers: audit entries are append-only; notifications are per-user."""
from sqlalchemy.orm import Session
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from .models import AuditLog, Notification

def audit(db:Session, actor_id:int|None, action:str, entity_type:str, entity_id:int|None=None, details:str|None=None, entity_ref:str|None=None):
    db.add(AuditLog(actor_id=actor_id,action=action,entity_type=entity_type,entity_id=entity_id,entity_ref=entity_ref,details=details))
def notify(db:Session, user_id:int, message:str, link:str|None=None):
    db.add(Notification(user_id=user_id,message=message,link=link))
def notify_once(db:Session,user_id:int,message:str,link:str,dedupe_key:str):
    values={"user_id":user_id,"message":message,"link":link,"dedupe_key":dedupe_key}
    dialect=db.get_bind().dialect.name
    if dialect=="postgresql":
        db.execute(pg_insert(Notification).values(**values).on_conflict_do_nothing(index_elements=[Notification.dedupe_key]))
    elif dialect=="sqlite":
        db.execute(sqlite_insert(Notification).values(**values).on_conflict_do_nothing(index_elements=[Notification.dedupe_key]))
    elif not db.scalar(select(Notification.id).where(Notification.dedupe_key==dedupe_key)):
        db.add(Notification(**values))
