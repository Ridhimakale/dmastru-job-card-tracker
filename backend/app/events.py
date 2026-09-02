"""Workflow event helpers: audit entries are append-only; notifications are per-user."""
from sqlalchemy.orm import Session
from .models import AuditLog, Notification

def audit(db:Session, actor_id:int|None, action:str, entity_type:str, entity_id:int|None=None, details:str|None=None):
    db.add(AuditLog(actor_id=actor_id,action=action,entity_type=entity_type,entity_id=entity_id,details=details))
def notify(db:Session, user_id:int, message:str, link:str|None=None):
    db.add(Notification(user_id=user_id,message=message,link=link))
