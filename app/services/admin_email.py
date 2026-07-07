"""Email center for the admin console.

Outbound: sent over SMTP when configured (SMTP_HOST…). Supports SSL (465) and
STARTTLS (587). Without SMTP the message is still recorded with status
"simulated" so the console works end-to-end in dev.

Inbound: IMAP sync (LWS mailbox) and/or provider webhook at /admin/email/inbound.
"""
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, make_msgid

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


def default_from_addr() -> str:
    return (
        current_app.config.get("EMAIL_FROM")
        or current_app.config.get("SMTP_USER")
        or "contact@pilotcore.fr"
    )


def send_email(
    to_addr,
    subject,
    body,
    is_html=False,
    tenant_id=None,
    from_addr=None,
    cc_addrs=None,
    in_reply_to_row=None,
    html_body=None,
    reply_to=None,
):
    """Send (or simulate) an email and record it. Returns the EmailMessage."""
    from_addr = from_addr or default_from_addr()
    msg_row = EmailMessage(
        direction=DIRECTION_OUTBOUND,
        status="queued",
        from_addr=from_addr,
        to_addr=to_addr,
        cc_addrs=cc_addrs,
        subject=subject,
        body=body if not is_html else (body or ""),
        html_body=html_body if is_html else (html_body or None),
        is_html=is_html or bool(html_body),
        tenant_id=tenant_id,
    )
    if in_reply_to_row:
        msg_row.in_reply_to_id = in_reply_to_row.id
        msg_row.rfc_in_reply_to = in_reply_to_row.provider_id or make_msgid()
        refs = (in_reply_to_row.references_header or "").strip()
        parent_id = in_reply_to_row.provider_id or ""
        msg_row.references_header = f"{refs} {parent_id}".strip() if parent_id else refs

    db.session.add(msg_row)
    db.session.commit()

    if not is_configured():
        msg_row.status = STATUS_SIMULATED
        db.session.commit()
        logger.info("Email simulated (SMTP not configured) to=%s subject=%s", to_addr, subject)
        _log(msg_row, simulated=True)
        return msg_row

    try:
        mime = _build_mime(
            from_addr=from_addr,
            to_addr=to_addr,
            subject=subject,
            body=body,
            is_html=is_html,
            html_body=html_body,
            cc_addrs=cc_addrs,
            in_reply_to=msg_row.rfc_in_reply_to,
            references=msg_row.references_header,
            reply_to=reply_to,
        )
        if not msg_row.provider_id:
            msg_row.provider_id = mime.get("Message-ID")

        recipients = [a.strip() for a in to_addr.split(",") if a.strip()]
        if cc_addrs:
            recipients.extend(a.strip() for a in cc_addrs.split(",") if a.strip())

        _smtp_send(from_addr, recipients, mime.as_string())
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


def _build_mime(
    from_addr,
    to_addr,
    subject,
    body,
    is_html=False,
    html_body=None,
    cc_addrs=None,
    in_reply_to=None,
    references=None,
    reply_to=None,
):
    if is_html or html_body:
        mime = MIMEMultipart("alternative")
        plain = body or ""
        mime.attach(MIMEText(plain, "plain", "utf-8"))
        mime.attach(MIMEText(html_body or body or "", "html", "utf-8"))
    else:
        mime = MIMEText(body or "", "plain", "utf-8")

    mime["Subject"] = subject
    mime["From"] = formataddr(("PilotCore", from_addr))
    mime["To"] = to_addr
    if cc_addrs:
        mime["Cc"] = cc_addrs
    mime["Message-ID"] = make_msgid(domain=from_addr.split("@")[-1] if "@" in from_addr else "pilotcore.fr")
    if in_reply_to:
        mime["In-Reply-To"] = in_reply_to
    if references:
        mime["References"] = references
    if reply_to:
        mime["Reply-To"] = reply_to
    return mime


def smtp_test():
    """Live connectivity + auth probe against the configured SMTP server.

    Opens the connection (SSL or STARTTLS) and, when credentials are present,
    performs a LOGIN — the exact same steps a real send does, minus the message.
    Returns ``{"ok": bool, "detail": str}`` and never raises.
    """
    cfg = current_app.config
    host = cfg.get("SMTP_HOST")
    if not host:
        return {"ok": False, "detail": "SMTP_HOST non configuré — les envois sont simulés."}
    port = int(cfg.get("SMTP_PORT", 587))
    use_ssl = cfg.get("SMTP_USE_SSL", False)
    use_tls = cfg.get("SMTP_USE_TLS", True)
    user = cfg.get("SMTP_USER")
    pwd = cfg.get("SMTP_PASSWORD")
    try:
        if use_ssl:
            server = smtplib.SMTP_SSL(host, port, timeout=15)
        else:
            server = smtplib.SMTP(host, port, timeout=15)
        try:
            if not use_ssl and use_tls:
                server.starttls()
            if user and pwd:
                server.login(user, pwd)
                return {"ok": True, "detail": f"Connexion et authentification OK ({host}:{port})."}
            if user and not pwd:
                return {"ok": False,
                        "detail": f"Connexion OK ({host}:{port}) mais SMTP_PASSWORD manquant."}
            return {"ok": True, "detail": f"Connexion OK ({host}:{port}) — sans authentification."}
        finally:
            try:
                server.quit()
            except Exception:
                pass
    except Exception as exc:  # pragma: no cover - depends on live SMTP
        logger.warning("SMTP test failed host=%s port=%s: %s", host, port, exc)
        return {"ok": False, "detail": f"{type(exc).__name__}: {str(exc)[:250]}"}


def _smtp_send(from_addr, recipients, raw_message):
    cfg = current_app.config
    host = cfg["SMTP_HOST"]
    port = int(cfg.get("SMTP_PORT", 587))
    use_ssl = cfg.get("SMTP_USE_SSL", False)
    use_tls = cfg.get("SMTP_USE_TLS", True)

    if use_ssl:
        server = smtplib.SMTP_SSL(host, port, timeout=20)
    else:
        server = smtplib.SMTP(host, port, timeout=20)
    try:
        if not use_ssl and use_tls:
            server.starttls()
        user = cfg.get("SMTP_USER")
        pwd = cfg.get("SMTP_PASSWORD")
        if user and pwd:
            server.login(user, pwd)
        server.sendmail(from_addr, recipients, raw_message)
    finally:
        server.quit()


def store_inbound(
    from_addr,
    to_addr,
    subject,
    body,
    provider_id=None,
    html_body=None,
    is_html=False,
    cc_addrs=None,
    imap_uid=None,
    imap_folder=None,
    attachments=None,
):
    """Persist an inbound email (IMAP or webhook)."""
    if provider_id:
        existing = EmailMessage.query.filter_by(provider_id=provider_id).first()
        if existing:
            return existing
    if imap_uid and imap_folder:
        existing = EmailMessage.query.filter_by(imap_uid=imap_uid, imap_folder=imap_folder).first()
        if existing:
            return existing

    import json

    row = EmailMessage(
        direction=DIRECTION_INBOUND,
        status=STATUS_RECEIVED,
        from_addr=from_addr,
        to_addr=to_addr,
        cc_addrs=cc_addrs,
        subject=subject,
        body=body,
        html_body=html_body,
        is_html=is_html or bool(html_body),
        provider_id=provider_id,
        imap_uid=imap_uid,
        imap_folder=imap_folder,
        attachments_json=json.dumps(attachments) if attachments else None,
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
