import os
import secrets
from datetime import datetime, timedelta, timezone
from enum import StrEnum

import jwt
from argon2 import PasswordHasher
from fastapi import HTTPException, status

ENV = os.getenv("APP_ENV", "development")

def normalize_database_url(value: str) -> str:
    """Use psycopg 3 for both Render and local PostgreSQL connection strings."""
    if value.startswith("postgres://"):
        return "postgresql+psycopg://" + value.removeprefix("postgres://")
    if value.startswith("postgresql://"):
        return "postgresql+psycopg://" + value.removeprefix("postgresql://")
    return value

DATABASE_URL = normalize_database_url(os.getenv("DATABASE_URL", "sqlite:///./job_card.db"))
JWT_SECRET = os.getenv("JWT_SECRET") or (secrets.token_urlsafe(48) if ENV == "development" else "")
JWT_EXPIRY_MINUTES = int(os.getenv("JWT_EXPIRY_MINUTES", "30"))
REFRESH_EXPIRY_DAYS = int(os.getenv("REFRESH_EXPIRY_DAYS", "7"))
if not JWT_SECRET: raise RuntimeError("JWT_SECRET must be configured outside development.")

passwords = PasswordHasher()

class Role(StrEnum): ADMIN="ADMIN"; MARKETING="MARKETING"; FINANCE="FINANCE"; MANAGEMENT="MANAGEMENT"; END_USER="END_USER"; UNIT_REPRESENTATIVE="UNIT_REPRESENTATIVE"
class AccountStatus(StrEnum): PENDING_ACTIVATION="PENDING_ACTIVATION"; ACTIVE="ACTIVE"; INACTIVE="INACTIVE"; LOCKED="LOCKED"

def check_password_rules(value: str):
    if len(value)<8 or not any(c.isupper() for c in value) or not any(c.islower() for c in value) or not any(c.isdigit() for c in value) or not any(not c.isalnum() for c in value):
        raise HTTPException(422,"Password must contain 8+ characters with uppercase, lowercase, number, and special character.")

def issue_token(user_id: int, session_id: str):
    now=datetime.now(timezone.utc); return jwt.encode({"sub":str(user_id),"sid":session_id,"iat":now,"exp":now+timedelta(minutes=JWT_EXPIRY_MINUTES)},JWT_SECRET,algorithm="HS256")
def decode_token(token: str):
    try: return jwt.decode(token,JWT_SECRET,algorithms=["HS256"])
    except jwt.PyJWTError: raise HTTPException(status.HTTP_401_UNAUTHORIZED,"Your session has expired. Please sign in again.")
