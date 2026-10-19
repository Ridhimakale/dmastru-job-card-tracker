"""Small SMTP helper for account recovery mail."""
import os
import smtplib
import ssl
from email.message import EmailMessage


def send_password_reset(recipient: str, reset_url: str) -> None:
    host = os.getenv("SMTP_HOST", "").strip()
    sender = os.getenv("SMTP_FROM", "").strip()
    if not host or not sender:
        raise RuntimeError("SMTP_HOST and SMTP_FROM must be configured to send password reset mail.")
    port = int(os.getenv("SMTP_PORT", "587"))
    username = os.getenv("SMTP_USERNAME", "")
    password = os.getenv("SMTP_PASSWORD", "")
    use_ssl = os.getenv("SMTP_USE_SSL", "false").lower() == "true"
    message = EmailMessage()
    message["Subject"] = "Reset your D'Mastru Job Card Tracker password"
    message["From"] = sender
    message["To"] = recipient
    message.set_content(f"Use this secure link to reset your password. It expires in 30 minutes.\n\n{reset_url}\n\nIf you did not request this, ignore this message.")
    context = ssl.create_default_context()
    client = smtplib.SMTP_SSL(host, port, timeout=20, context=context) if use_ssl else smtplib.SMTP(host, port, timeout=20)
    with client as smtp:
        if not use_ssl:
            smtp.starttls(context=context)
        if username:
            smtp.login(username, password)
        smtp.send_message(message)


def send_email(recipient: str, subject: str, body: str) -> None:
    """Send an operational notification through the configured SMTP service."""
    host = os.getenv("SMTP_HOST", "").strip()
    sender = os.getenv("SMTP_FROM", "").strip()
    if not host or not sender:
        raise RuntimeError("SMTP_HOST and SMTP_FROM must be configured to send operational email.")
    port = int(os.getenv("SMTP_PORT", "587"))
    username = os.getenv("SMTP_USERNAME", "")
    password = os.getenv("SMTP_PASSWORD", "")
    use_ssl = os.getenv("SMTP_USE_SSL", "false").lower() == "true"
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = recipient
    message.set_content(body)
    context = ssl.create_default_context()
    client = smtplib.SMTP_SSL(host, port, timeout=20, context=context) if use_ssl else smtplib.SMTP(host, port, timeout=20)
    with client:
        if not use_ssl:
            client.starttls(context=context)
        if username:
            client.login(username, password)
        client.send_message(message)


def send_initial_password(recipient: str, initial_password: str, password_setup_url: str, sign_in_url: str, activation_pending: bool) -> None:
    activation_note = "Your account is awaiting Admin activation. You can sign in after it is activated.\n\n" if activation_pending else ""
    body = (
        "An Admin created your D'Mastru account.\n\n"
        f"Sign-in page: {sign_in_url}\n"
        f"Registered email: {recipient}\n"
        f"Initial password: {initial_password}\n\n"
        f"Set your new permanent password using this one-time link (expires in 30 minutes):\n{password_setup_url}\n\n"
        "You must replace this initial password with a permanent password before accessing your workspace. "
        "The new password must have at least 8 characters and include uppercase and lowercase letters, a number, "
        "and a special character. You will be asked to confirm it.\n\n"
        f"{activation_note}After setting your permanent password, complete authenticator-app MFA setup.\n\n"
        "If you were not expecting this account, contact your site administrator."
    )
    send_email(recipient, "Your D'Mastru account initial password", body)
