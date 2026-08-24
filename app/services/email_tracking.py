"""Open / click tracking for outbound mail (Brevo-style).

Tracking pixels and wrapped links are injected only in the MIME that goes out
over SMTP. The copy stored in ``email_messages.html_body`` stays clean so the
admin iframe cannot inflate stats.

Hits from an admin session, an ``/admin`` referer, or mail sent only to internal
PilotCore addresses are ignored.
"""
from __future__ import annotations

import json
import re
import secrets
from email.utils import parseaddr
from html import unescape
from urllib.parse import quote, urlparse

from flask import current_app, request
from sqlalchemy import func

from app.core.extensions import db
from app.models.email_message import (
    DIRECTION_OUTBOUND,
    STATUS_SENT,
    STATUS_SIMULATED,
    EmailMessage,
    utcnow,
)

INTERNAL_DOMAINS = ("pilotcore.fr",)
PIXEL_GIF = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00"
    b"!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01"
    b"\x00\x00\x02\x02D\x01\x00;"
)

_HREF_RE = re.compile(
    r'(<a\b[^>]*?\bhref\s*=\s*)(["\'])([^"\']+)\2',
    re.I | re.DOTALL,
)
_PLAIN_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)
_TRAIL_PUNCT = ".,);]>\"'"


def new_track_token() -> str:
    return secrets.token_urlsafe(24)


def _extract_addresses(*blobs: str | None) -> list[str]:
    found: list[str] = []
    for blob in blobs:
        if not blob:
            continue
        for part in str(blob).split(","):
            _, addr = parseaddr(part)
            addr = (addr or "").strip().lower()
            if addr and "@" in addr:
                found.append(addr)
    return found


def _internal_allowlist() -> set[str]:
    addrs = {"contact@pilotcore.fr"}
    for key in ("EMAIL_FROM", "SMTP_USER"):
        _, addr = parseaddr(current_app.config.get(key) or "")
        if addr and "@" in addr:
            addrs.add(addr.strip().lower())
    extra = current_app.config.get("EMAIL_TRACKING_EXCLUDE") or ""
    addrs.update(_extract_addresses(extra))
    return addrs


def is_internal_address(addr: str) -> bool:
    addr = (addr or "").strip().lower()
    if not addr or "@" not in addr:
        return False
    if addr in _internal_allowlist():
        return True
    domain = addr.rsplit("@", 1)[-1]
    return domain in INTERNAL_DOMAINS or domain.endswith(".pilotcore.fr")


def should_track_recipients(to_addr: str | None, cc_addrs: str | None = None) -> bool:
    """True when at least one recipient is a real (non-internal) mailbox."""
    addrs = _extract_addresses(to_addr, cc_addrs)
    if not addrs:
        return False
    return any(not is_internal_address(a) for a in addrs)


def tracking_base_url() -> str:
    from app.utils.seo import site_base_url

    return site_base_url()


def should_wrap_url(url: str) -> bool:
    raw = unescape((url or "").strip())
    if not raw:
        return False
    lower = raw.lower()
    if lower.startswith(("mailto:", "tel:", "sms:", "javascript:", "data:", "cid:")):
        return False
    if lower.startswith("#"):
        return False
    if "/t/c/" in lower or "/t/o/" in lower:
        return False
    parsed = urlparse(raw)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def wrap_click_url(url: str, token: str, base: str | None = None) -> str:
    dest = unescape((url or "").strip())
    if not token or not should_wrap_url(dest):
        return url
    root = (base or tracking_base_url()).rstrip("/")
    return f"{root}/t/c/{token}?u={quote(dest, safe='')}"


def instrument_html(html: str, token: str, base: str | None = None) -> str:
    if not html or not token:
        return html or ""
    root = (base or tracking_base_url()).rstrip("/")

    def _sub(match: re.Match) -> str:
        prefix, quote_chr, href = match.group(1), match.group(2), match.group(3)
        wrapped = wrap_click_url(href, token, root)
        return f"{prefix}{quote_chr}{wrapped}{quote_chr}"

    out = _HREF_RE.sub(_sub, html)
    pixel = (
        f'<img src="{root}/t/o/{token}.gif" width="1" height="1" alt="" '
        f'style="display:block;width:1px;height:1px;border:0;overflow:hidden" />'
    )
    if f"/t/o/{token}" in out:
        return out
    lower = out.lower()
    idx = lower.rfind("</body>")
    if idx != -1:
        return out[:idx] + pixel + out[idx:]
    return out + pixel


def instrument_plain(text: str, token: str, base: str | None = None) -> str:
    if not text or not token:
        return text or ""
    root = (base or tracking_base_url()).rstrip("/")

    def _sub(match: re.Match) -> str:
        raw = match.group(0)
        trailing = ""
        while raw and raw[-1] in _TRAIL_PUNCT:
            trailing = raw[-1] + trailing
            raw = raw[:-1]
        if not should_wrap_url(raw):
            return match.group(0)
        return wrap_click_url(raw, token, root) + trailing

    return _PLAIN_URL_RE.sub(_sub, text)


def instrument_bodies(token: str | None, html_body: str | None, plain_body: str | None, is_html: bool):
    """Return (html, plain) copies for the outbound MIME only."""
    if not token:
        return html_body, plain_body
    html = html_body or (plain_body if is_html else None)
    if html:
        return instrument_html(html, token), plain_body
    return html_body, instrument_plain(plain_body or "", token)


def should_ignore_hit() -> bool:
    """Admin console / operator browsing must not inflate engagement."""
    from flask import has_request_context

    if not has_request_context():
        return False
    try:
        from app.core.admin_auth import is_admin_logged_in

        if is_admin_logged_in():
            return True
    except RuntimeError:
        pass
    referer = (request.headers.get("Referer") or request.headers.get("Referrer") or "")
    if "/admin" in referer.lower():
        return True
    return False


def _is_click_prefetch() -> bool:
    from flask import has_request_context

    if not has_request_context():
        return False
    purpose = (
        (request.headers.get("Sec-Purpose") or "")
        + " "
        + (request.headers.get("Purpose") or "")
    ).lower()
    return "prefetch" in purpose or "prerender" in purpose


def find_by_token(token: str) -> EmailMessage | None:
    token = (token or "").strip().removesuffix(".gif")
    if not token or len(token) < 8:
        return None
    return EmailMessage.query.filter_by(track_token=token).first()


def record_open(token: str) -> bool:
    if should_ignore_hit():
        return False
    row = find_by_token(token)
    if not row or row.direction != DIRECTION_OUTBOUND:
        return False
    now = utcnow()
    row.open_count = (row.open_count or 0) + 1
    if row.first_opened_at is None:
        row.first_opened_at = now
    row.last_opened_at = now
    db.session.commit()
    return True


def _safe_dest_url(url: str) -> str | None:
    dest = (url or "").strip()
    if not dest:
        return None
    parsed = urlparse(dest)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    if "/t/c/" in dest or "/t/o/" in dest:
        return None
    return dest


def record_click(token: str, url: str) -> str | None:
    dest = _safe_dest_url(url)
    if not dest:
        return None
    if should_ignore_hit() or _is_click_prefetch():
        return dest
    row = find_by_token(token)
    if not row or row.direction != DIRECTION_OUTBOUND:
        return dest
    now = utcnow()
    row.click_count = (row.click_count or 0) + 1
    if row.first_clicked_at is None:
        row.first_clicked_at = now
    row.last_clicked_at = now
    if row.first_opened_at is None:
        row.first_opened_at = now
        row.open_count = max(row.open_count or 0, 1)
    links = {}
    if row.click_urls_json:
        try:
            parsed = json.loads(row.click_urls_json)
            if isinstance(parsed, dict):
                links = parsed
        except json.JSONDecodeError:
            links = {}
    meta = links.get(dest) if isinstance(links.get(dest), dict) else {}
    if not meta:
        meta = {"count": 0, "first_at": now.isoformat()}
    meta["count"] = int(meta.get("count") or 0) + 1
    if not meta.get("first_at"):
        meta["first_at"] = now.isoformat()
    links[dest] = meta
    row.click_urls_json = json.dumps(links, ensure_ascii=False)
    db.session.commit()
    return dest


def format_rate(n: int, d: int) -> str:
    if not d:
        return "—"
    value = 100.0 * n / d
    if abs(value - round(value)) < 0.05:
        return f"{int(round(value))} %"
    return f"{value:.1f} %".replace(".", ",")


def outbound_stats() -> dict:
    """Unique open / click rates on trackable outbound mail (Brevo-style)."""
    filters = (
        EmailMessage.direction == DIRECTION_OUTBOUND,
        EmailMessage.status.in_((STATUS_SENT, STATUS_SIMULATED)),
        EmailMessage.track_token.isnot(None),
    )
    sent = EmailMessage.query.filter(*filters).count()
    opened = EmailMessage.query.filter(*filters, EmailMessage.first_opened_at.isnot(None)).count()
    clicked = EmailMessage.query.filter(*filters, EmailMessage.first_clicked_at.isnot(None)).count()
    total_opens = (
        db.session.query(func.coalesce(func.sum(EmailMessage.open_count), 0))
        .filter(*filters)
        .scalar()
        or 0
    )
    total_clicks = (
        db.session.query(func.coalesce(func.sum(EmailMessage.click_count), 0))
        .filter(*filters)
        .scalar()
        or 0
    )
    return {
        "sent": sent,
        "unique_opens": opened,
        "unique_clicks": clicked,
        "total_opens": int(total_opens),
        "total_clicks": int(total_clicks),
        "open_rate": format_rate(opened, sent),
        "click_rate": format_rate(clicked, sent),
        "ctor": format_rate(clicked, opened),
    }
