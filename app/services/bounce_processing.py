"""Learn from bounces so a dead address is never mailed twice.

The mail server reports an undeliverable message by e-mailing a report back to
the sender. Those reports land in the inbox with everything else and, until now,
stayed there: nothing read them, so a prospect whose address does not exist kept
its "ready" status and was picked up by the next campaign. Twenty-nine of them
had accumulated in production.

That is not a cosmetic problem. Mailbox providers score a sending domain on its
bounce rate, and a domain they distrust gets *all* its mail junked — including
the password-reset e-mail a paying customer is waiting for. Reading bounces is
how the transactional mail stays deliverable.

What this does:

* Reads inbound reports from the usual daemons (``MAILER-DAEMON``,
  ``postmaster``…), extracts the address that failed and why.
* Marks only **permanent** failures (SMTP 5.x.x — no such mailbox, relay
  refused). A 4.x.x is a full mailbox or a server having a bad day, and the
  address may well work tomorrow, so it is left alone.
* Flips the matching prospect to ``skipped`` and writes the reason into its
  notes, which is what the console shows and what the audience query excludes.
* Is idempotent: a report already processed is recognised and skipped, so it can
  run on every inbox sync and on a schedule without double-counting.
"""
from __future__ import annotations

import logging
import re

from app.core.extensions import db
from app.models.email_message import DIRECTION_INBOUND, EmailMessage
from app.models.outreach_prospect import OutreachProspect, utcnow

logger = logging.getLogger(__name__)

# Senders that deliver delivery-status reports.
_DAEMON_RE = re.compile(
    r"(mailer-daemon|postmaster|mail delivery|delivery (subsystem|status)|"
    r"no-?reply.*(bounce|delivery))",
    re.IGNORECASE,
)
_SUBJECT_RE = re.compile(
    r"(undelivered|undeliverable|delivery status notification|returned to sender|"
    r"delivery failure|failure notice|mail delivery failed|échec de (la )?remise)",
    re.IGNORECASE,
)

# RFC 3464 machine-readable fields, and the plain-text shape Postfix also emits.
_FINAL_RECIPIENT_RE = re.compile(r"^Final-Recipient:\s*rfc822;\s*(\S+)", re.IGNORECASE | re.MULTILINE)
_ORIGINAL_RECIPIENT_RE = re.compile(r"^Original-Recipient:\s*rfc822;\s*(\S+)", re.IGNORECASE | re.MULTILINE)
_STATUS_RE = re.compile(r"^Status:\s*([245])\.\d+\.\d+", re.IGNORECASE | re.MULTILINE)
_DIAGNOSTIC_RE = re.compile(r"^Diagnostic-Code:\s*smtp;\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_INLINE_RE = re.compile(r"<([^<>@\s]+@[^<>\s]+)>:\s*([45]\d\d[^\n]*)")

_MARKER = "[Rebond]"


def looks_like_bounce(row: EmailMessage) -> bool:
    if row.direction != DIRECTION_INBOUND:
        return False
    return bool(
        _DAEMON_RE.search(row.from_addr or "") or _SUBJECT_RE.search(row.subject or "")
    )


def _body_of(row: EmailMessage) -> str:
    text = row.body or ""
    if not text and row.html_body:
        text = re.sub(r"<[^>]+>", "\n", row.html_body)
    return text


def _is_permanent(code_text: str) -> bool:
    """True for a 5.x.x failure. A 4.x.x is temporary and must not be acted on."""
    return bool(re.match(r"\s*5\d\d", code_text or ""))


def parse_bounce(row: EmailMessage) -> list[dict]:
    """Return ``[{"email", "reason", "permanent"}]`` for each failed recipient."""
    body = _body_of(row)
    if not body:
        return []

    found: dict[str, dict] = {}

    # 1. The machine-readable report, when the server sends one.
    status = _STATUS_RE.search(body)
    diagnostic = _DIAGNOSTIC_RE.search(body)
    for pattern in (_FINAL_RECIPIENT_RE, _ORIGINAL_RECIPIENT_RE):
        for addr in pattern.findall(body):
            addr = addr.strip().strip("<>").lower()
            if "@" not in addr:
                continue
            reason = (diagnostic.group(1).strip() if diagnostic else "").strip()
            permanent = (status.group(1) == "5") if status else _is_permanent(reason)
            found.setdefault(addr, {"email": addr, "reason": reason or "échec permanent",
                                    "permanent": permanent})

    # 2. The human-readable line Postfix and friends also include.
    for addr, detail in _INLINE_RE.findall(body):
        addr = addr.strip().lower()
        detail = " ".join(detail.split())[:300]
        entry = found.get(addr)
        if entry is None:
            found[addr] = {"email": addr, "reason": detail, "permanent": _is_permanent(detail)}
        elif not entry["reason"]:
            entry["reason"] = detail

    return list(found.values())


def _already_processed(prospect: OutreachProspect, email: str) -> bool:
    return _MARKER in (prospect.notes or "") and email.lower() in (prospect.notes or "").lower()


def process_bounces(*, limit: int = 500) -> dict:
    """Scan recent inbound mail for delivery reports and quarantine dead addresses."""
    candidates = (
        EmailMessage.query.filter(EmailMessage.direction == DIRECTION_INBOUND)
        .order_by(EmailMessage.created_at.desc())
        .limit(limit)
        .all()
    )
    reports = [r for r in candidates if looks_like_bounce(r)]

    marked, already, temporary, unknown = 0, 0, 0, 0
    addresses: list[str] = []

    for report in reports:
        for failure in parse_bounce(report):
            email = failure["email"]
            if not failure["permanent"]:
                temporary += 1
                continue
            prospect = (
                OutreachProspect.query.filter(
                    db.func.lower(OutreachProspect.email) == email
                ).first()
            )
            if prospect is None:
                unknown += 1
                continue
            if _already_processed(prospect, email):
                already += 1
                continue

            note = f"{_MARKER} {email} — {failure['reason'][:200]}"
            prospect.notes = (f"{prospect.notes}\n{note}" if prospect.notes else note).strip()
            prospect.status = "skipped"
            prospect.email_confidence = "low"
            prospect.updated_at = utcnow()
            marked += 1
            addresses.append(email)

    if marked:
        db.session.commit()
        logger.info("Bounce processing quarantined %s address(es)", marked)

    return {
        "reports": len(reports),
        "marked": marked,
        "already_marked": already,
        "temporary_ignored": temporary,
        "unknown_recipient": unknown,
        "addresses": addresses[:50],
    }
