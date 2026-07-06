"""IMAP sync for the admin mailbox (LWS / OVH-style hosting).

Pulls messages from the configured inbox and stores them via admin_email.store_inbound.
Uses stdlib imaplib + email — no extra dependencies.
"""
import email
import imaplib
import json
import logging
import re
from email import policy
from email.header import decode_header
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path

from flask import current_app

from app.core.extensions import db
from app.models.email_message import EmailMessage

logger = logging.getLogger(__name__)


def is_configured() -> bool:
    cfg = current_app.config
    return bool(cfg.get("IMAP_HOST") and cfg.get("IMAP_USER") and cfg.get("IMAP_PASSWORD"))


def _decode_header_value(value: str | None) -> str:
    if not value:
        return ""
    parts = []
    for chunk, charset in decode_header(value):
        if isinstance(chunk, bytes):
            parts.append(chunk.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(str(chunk))
    return "".join(parts).strip()


def _extract_body(msg: email.message.Message) -> tuple[str, str | None, bool]:
    """Return (plain_text, html_body, is_html_primary)."""
    plain_parts: list[str] = []
    html_parts: list[str] = []

    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            disposition = (part.get("Content-Disposition") or "").lower()
            if "attachment" in disposition:
                continue
            ctype = part.get_content_type()
            try:
                payload = part.get_content()
            except Exception:
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    charset = part.get_content_charset() or "utf-8"
                    payload = payload.decode(charset, errors="replace")
            if not payload:
                continue
            if ctype == "text/plain":
                plain_parts.append(str(payload).strip())
            elif ctype == "text/html":
                html_parts.append(str(payload).strip())
    else:
        try:
            payload = msg.get_content()
        except Exception:
            raw = msg.get_payload(decode=True)
            charset = msg.get_content_charset() or "utf-8"
            payload = raw.decode(charset, errors="replace") if isinstance(raw, bytes) else raw
        if msg.get_content_type() == "text/html":
            html_parts.append(str(payload or "").strip())
        else:
            plain_parts.append(str(payload or "").strip())

    plain = "\n\n".join(p for p in plain_parts if p)
    html = "\n\n".join(h for h in html_parts if h)
    if plain:
        return plain, html or None, False
    if html:
        return _html_to_plain(html), html, True
    return "", None, False


def _html_to_plain(html: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.I | re.S)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p>", "\n\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _extract_attachments(msg: email.message.Message, message_id: str) -> list[dict]:
    attachments: list[dict] = []
    if not msg.is_multipart():
        return attachments

    storage_root = _attachment_dir()
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "_", message_id or "msg")[:80]

    for idx, part in enumerate(msg.walk()):
        disposition = (part.get("Content-Disposition") or "").lower()
        filename = part.get_filename()
        if not filename and "attachment" not in disposition:
            continue
        if filename:
            filename = _decode_header_value(filename)
        else:
            filename = f"piece-jointe-{idx + 1}"
        try:
            data = part.get_payload(decode=True) or b""
        except Exception:
            continue
        if not data:
            continue
        key = f"{safe_id}_{idx}_{filename}"
        path = storage_root / key
        path.write_bytes(data)
        attachments.append(
            {
                "filename": filename,
                "content_type": part.get_content_type(),
                "size": len(data),
                "storage_key": key,
            }
        )
    return attachments


def _attachment_dir() -> Path:
    base = Path(current_app.instance_path) / "email_attachments"
    base.mkdir(parents=True, exist_ok=True)
    return base


def get_attachment_path(storage_key: str) -> Path | None:
    if not storage_key or ".." in storage_key or "/" in storage_key or "\\" in storage_key:
        return None
    path = _attachment_dir() / storage_key
    return path if path.is_file() else None


def _connect_imap() -> imaplib.IMAP4_SSL | imaplib.IMAP4:
    cfg = current_app.config
    host = cfg["IMAP_HOST"]
    port = int(cfg.get("IMAP_PORT", 993))
    use_ssl = cfg.get("IMAP_USE_SSL", True)
    if use_ssl:
        client = imaplib.IMAP4_SSL(host, port)
    else:
        client = imaplib.IMAP4(host, port)
    client.login(cfg["IMAP_USER"], cfg["IMAP_PASSWORD"])
    return client


def sync_inbox(limit: int = 50) -> dict:
    """Fetch recent inbox messages from IMAP. Returns stats dict."""
    if not is_configured():
        return {"ok": False, "error": "IMAP non configuré", "synced": 0, "skipped": 0}

    from app.services.admin_email import store_inbound

    folder = current_app.config.get("IMAP_FOLDER", "INBOX")
    synced = 0
    skipped = 0
    errors: list[str] = []

    try:
        client = _connect_imap()
        status, _ = client.select(folder, readonly=True)
        if status != "OK":
            return {"ok": False, "error": f"Impossible d'ouvrir {folder}", "synced": 0, "skipped": 0}

        status, data = client.uid("search", None, "ALL")
        if status != "OK" or not data or not data[0]:
            client.logout()
            return {"ok": True, "synced": 0, "skipped": 0, "message": "Boîte vide"}

        uids = data[0].split()
        for uid in reversed(uids[-limit:]):
            uid_str = uid.decode() if isinstance(uid, bytes) else str(uid)
            if EmailMessage.query.filter_by(imap_uid=uid_str, imap_folder=folder).first():
                skipped += 1
                continue

            status, msg_data = client.uid("fetch", uid, "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue

            raw = msg_data[0][1]
            if not isinstance(raw, bytes):
                continue

            parsed = email.message_from_bytes(raw, policy=policy.default)
            message_id = (parsed.get("Message-ID") or "").strip()
            if message_id and EmailMessage.query.filter_by(provider_id=message_id).first():
                skipped += 1
                continue

            from_name, from_addr = parseaddr(_decode_header_value(parsed.get("From")))
            to_name, to_addr = parseaddr(_decode_header_value(parsed.get("To")))
            subject = _decode_header_value(parsed.get("Subject"))
            plain, html_body, is_html = _extract_body(parsed)
            attachments = _extract_attachments(parsed, message_id or uid_str)

            row = store_inbound(
                from_addr=from_addr or parsed.get("From", ""),
                to_addr=to_addr or parsed.get("To", ""),
                subject=subject,
                body=plain,
                provider_id=message_id or None,
                html_body=html_body,
                is_html=is_html,
                cc_addrs=_decode_header_value(parsed.get("Cc")),
                imap_uid=uid_str,
                imap_folder=folder,
                attachments=attachments,
            )
            if parsed.get("Date"):
                try:
                    row.created_at = parsedate_to_datetime(parsed.get("Date"))
                    db.session.commit()
                except Exception:
                    pass
            synced += 1

        client.logout()
    except Exception as exc:
        logger.exception("IMAP sync failed")
        return {"ok": False, "error": str(exc), "synced": synced, "skipped": skipped}

    return {"ok": True, "synced": synced, "skipped": skipped, "errors": errors}


def list_attachments(row: EmailMessage) -> list[dict]:
    if not row.attachments_json:
        return []
    try:
        return json.loads(row.attachments_json)
    except json.JSONDecodeError:
        return []
