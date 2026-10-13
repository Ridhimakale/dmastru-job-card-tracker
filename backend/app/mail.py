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
