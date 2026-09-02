"""Idempotent development seed data. Run after `python -m alembic upgrade head`."""
from sqlalchemy import select
from .core import AccountStatus, Role, passwords
from .database import SessionLocal
from .models import User

DEFAULTS=(
    ("System Admin","admin@dmastru.local",Role.ADMIN,"Admin@123"),
    ("Management Demo","management@dmastru.local",Role.MANAGEMENT,"Manage@123"),
    ("Consultant Demo","consultant@dmastru.local",Role.END_USER,"Consult@123"),
    ("Unit Representative Demo","unit.rep@dmastru.local",Role.UNIT_REPRESENTATIVE,"UnitRep@123"),
    ("Director Demo","director@dmastru.local",Role.DIRECTOR,"Director@123"),
)
def seed():
    db=SessionLocal()
    try:
        for full_name,email,role,password in DEFAULTS:
            if not db.scalar(select(User).where(User.email==email)):
                db.add(User(full_name=full_name,email=email,role=role,status=AccountStatus.ACTIVE,password_hash=passwords.hash(password)))
        db.commit()
    finally: db.close()
if __name__=="__main__": seed()
