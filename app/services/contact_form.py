"""Public contact form — messages appear in the admin email inbox."""
from flask import current_app

from app.services import admin_email

CONTACT_SUBJECT_PREFIX = "[Contact] "


def inbox_addr() -> str:
    return (
        current_app.config.get("EMAIL_FROM")
        or current_app.config.get("SMTP_USER")
        or "contact@pilotcore.fr"
    )


def submit_contact(name, email, subject, message):
    """Store inbound message for admin console and notify the mailbox."""
    clean_name = (name or "").strip()
    clean_email = (email or "").strip().lower()
    clean_subject = (subject or "").strip() or "Message depuis le site"
    clean_message = (message or "").strip()
    full_subject = CONTACT_SUBJECT_PREFIX + clean_subject

    from_display = f"{clean_name} <{clean_email}>" if clean_name else clean_email
    body = (
        "Nouveau message depuis pilotcore.fr/contact\n\n"
        f"De : {clean_name or clean_email}\n"
        f"E-mail : {clean_email}\n\n"
        f"{clean_message}\n"
    )

    row = admin_email.store_inbound(
        from_addr=from_display,
        to_addr=inbox_addr(),
        subject=full_subject,
        body=body,
    )
    admin_email.send_email(
        to_addr=inbox_addr(),
        subject=full_subject,
        body=body,
        reply_to=clean_email,
    )
    return row
