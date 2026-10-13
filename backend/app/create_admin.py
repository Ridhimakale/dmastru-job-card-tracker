"""Create the first production Admin interactively from a trusted shell."""
import argparse
from getpass import getpass

from sqlalchemy import select

from .core import AccountStatus, ENV, Role, check_password_rules, passwords
from .database import SessionLocal
from .events import audit
from .models import User


def main():
    parser = argparse.ArgumentParser(description="Provision the initial D'Mastru administrator")
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    email = args.email.strip().lower()
    if "@" not in email or len(email) > 320:
        parser.error("Provide a valid administrator email address.")
    if ENV in ("development", "test"):
        parser.error("Use the documented development seed accounts in development/test environments.")

    password = getpass("Initial Admin password: ")
    confirmation = getpass("Confirm password: ")
    if password != confirmation:
        parser.error("Passwords do not match.")
    try:
        check_password_rules(password)
    except Exception as exc:
        parser.error(str(getattr(exc, "detail", exc)))

    db = SessionLocal()
    try:
        if db.scalar(select(User.id).where(User.role == Role.ADMIN, User.status == AccountStatus.ACTIVE)):
            parser.error("An active Admin already exists. Use Admin user management to add another.")
        if db.scalar(select(User.id).where(User.email == email)):
            parser.error("A user with that email already exists.")
        user = User(full_name=args.name.strip(), email=email, role=Role.ADMIN, status=AccountStatus.ACTIVE, password_hash=passwords.hash(password))
        db.add(user)
        db.flush()
        audit(db, None, "INITIAL_ADMIN_CREATED", "USER", user.id, "Provisioned through the trusted deployment shell")
        db.commit()
        print(f"Initial Admin created: {email}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
