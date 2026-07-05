"""Email center for the admin console.

Outbound: sent over SMTP when configured (SMTP_HOST…). Without SMTP the message
is still recorded with status "simulated" so the console works end-to-end in
dev and nothing breaks in prod before email is wired up.

Inbound: a provider (Mailgun / SendGrid inbound parse, etc.) POSTs to
/admin/email/inbound; store_inbound() persists it into the shared inbox.
"""
import logging
import smtplib
from email.mime.text import MIMEText
from email.utils import formataddr

from flask import current_app

from app.core.extensions import db
from app.models.email_message import (
    DIRECTION_INBOUND,
    DIRECTION_OUTBOUND,
    STATUS_FAILED,
    STATUS_RECEIVED,
    STATUS_SENT,
    STATUS_SIMULATED,
    EmailMessage,
)

logger = logging.getLogger(__name__)


def is_configured():
    return bool(current_app.config.get("SMTP_HOST"))


def send_email(to_addr, subject, body, is_html=False, tenant_id=None, from_addr=None):
    """Send (or simulate) an email and record it. Returns the EmailMessage."""
    from_addr = from_addr or current_app.config.get("EMAIL_FROM", "no-reply@leadpilot.ai")
    msg_row = EmailMessage(
        direction=DIRECTION_OUTBOUND,
        status="queued",
        from_addr=from_addr,
        to_addr=to_addr,
        subject=subject,
        body=body,
        is_html=is_html,
        tenant_id=tenant_id,
    )
    db.session.add(msg_row)
    db.session.commit()

    if not is_configured():
        msg_row.status = STATUS_SIMULATED
        db.session.commit()
        logger.info("Email simulated (SMTP not configured) to=%s subject=%s", to_addr, subject)
        _log(msg_row, simulated=True)
        return msg_row

    try:
        mime = MIMEText(body, "html" if is_html else "plain", "utf-8")
        mime["Subject"] = subject
        mime["From"] = formataddr(("LeadPilot AI", from_addr))
        mime["To"] = to_addr

        host = current_app.config["SMTP_HOST"]
        port = current_app.config.get("SMTP_PORT", 587)
        with smtplib.SMTP(host, port, timeout=15) as server:
            if current_app.config.get("SMTP_USE_TLS", True):
                server.starttls()
            user = current_app.config.get("SMTP_USER")
            pwd = current_app.config.get("SMTP_PASSWORD")
            if user and pwd:
                server.login(user, pwd)
            server.sendmail(from_addr, [to_addr], mime.as_string())
        msg_row.status = STATUS_SENT
        db.session.commit()
        _log(msg_row)
    except Exception as exc:  # pragma: no cover - depends on live SMTP
        msg_row.status = STATUS_FAILED
        msg_row.error = str(exc)[:500]
        db.session.commit()
        logger.exception("Email send failed to=%s", to_addr)
        _log(msg_row, error=str(exc))
    return msg_row


def store_inbound(from_addr, to_addr, subject, body, provider_id=None):
    """Persist an inbound email delivered by a provider webhook."""
    row = EmailMessage(
        direction=DIRECTION_INBOUND,
        status=STATUS_RECEIVED,
        from_addr=from_addr,
        to_addr=to_addr,
        subject=subject,
        body=body,
        provider_id=provider_id,
    )
    db.session.add(row)
    db.session.commit()
    from app.services.events import CAT_EMAIL, log_event

    log_event(
        CAT_EMAIL,
        "email_received",
        summary=f"Reçu de {from_addr} — {subject or '(sans objet)'}",
        actor=from_addr,
    )
    return row


def _log(msg_row, simulated=False, error=None):
    from app.services.events import CAT_EMAIL, LEVEL_ERROR, LEVEL_INFO, log_event

    if error:
        log_event(
            CAT_EMAIL,
            "email_failed",
            summary=f"Échec envoi à {msg_row.to_addr} — {error}",
            level=LEVEL_ERROR,
        )
    else:
        log_event(
            CAT_EMAIL,
            "email_sent",
            summary=f"Envoyé à {msg_row.to_addr} — {msg_row.subject or '(sans objet)'}"
            + (" (simulé)" if simulated else ""),
            level=LEVEL_INFO,
        )
