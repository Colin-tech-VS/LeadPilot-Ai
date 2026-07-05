"""Segmentation marketing / SAV.

Once a job is finished the plumber marks the prospect as "Terminé" (archived).
Those completed clients form the after-sales base: this module groups them into
useful segments and sends a one-off campaign (SMS and/or e-mail) to a segment —
an entretien reminder, a satisfaction message, a seasonal promo, etc.

Deliberately light: no scheduling, no templates library. Best-effort sending
that reuses the existing SMS (Twilio) and e-mail (SMTP) helpers, both of which
degrade gracefully when not configured.
"""

import logging

from app.core.extensions import db
from app.models.lead import Lead
from app.models.quote import Quote

logger = logging.getLogger(__name__)

# Segment keys that are computed from the lead outcome rather than the issue.
SEG_ALL = "all"
SEG_WON = "won"
SEG_LOST = "lost"


def _issue_key(issue_type):
    return "issue:" + (issue_type or "unknown").strip().lower()


def _emails_by_lead(tenant_id, lead_ids):
    """Map lead_id -> client e-mail, sourced from the leads' own e-mail first
    then, as a fallback, from the most recent devis that carried one."""
    emails = {}
    if not lead_ids:
        return emails
    # Quotes are ordered oldest-first so a newer devis e-mail overwrites an older
    # one for the same lead.
    rows = (
        Quote.query.filter(
            Quote.tenant_id == tenant_id,
            Quote.lead_id.in_(lead_ids),
            Quote.client_email.isnot(None),
        )
        .order_by(Quote.created_at.asc())
        .all()
    )
    for q in rows:
        email = (q.client_email or "").strip()
        if email:
            emails[q.lead_id] = email
    return emails


def archived_leads(tenant_id):
    """All completed (archived) prospects for the tenant, newest first."""
    return (
        Lead.query.filter(Lead.tenant_id == tenant_id, Lead.archived_at.isnot(None))
        .order_by(Lead.archived_at.desc())
        .all()
    )


def build_segments(tenant_id):
    """Return the marketing segments for the tenant's completed clients.

    Each segment is a dict: {key, label, icon, leads, count, email_count,
    sms_count}. Only non-empty issue segments are returned, alongside the three
    outcome segments (all / won / lost).
    """
    leads = archived_leads(tenant_id)
    email_map = _emails_by_lead(tenant_id, [lead.id for lead in leads])

    # Attach the resolved e-mail so the template and the campaign share it.
    for lead in leads:
        lead.resolved_email = lead.email or email_map.get(lead.id)

    def make(key, label, icon, members):
        members = list(members)
        return {
            "key": key,
            "label": label,
            "icon": icon,
            "leads": members,
            "count": len(members),
            "email_count": sum(1 for m in members if m.resolved_email),
            "sms_count": sum(1 for m in members if (m.phone or "").strip()),
        }

    segments = [make(SEG_ALL, "Tous les clients terminés", "👥", leads)]

    won = [lead for lead in leads if lead.is_booked and not lead.is_cancelled]
    lost = [lead for lead in leads if lead.is_cancelled or lead.status == "lost"]
    if won:
        segments.append(make(SEG_WON, "Clients gagnés", "✅", won))
    if lost:
        segments.append(make(SEG_LOST, "Sans suite / annulés", "🕓", lost))

    # One segment per issue type present in the completed base.
    from app.utils.i18n import issue_label

    by_issue = {}
    for lead in leads:
        by_issue.setdefault(lead.issue_type or "", []).append(lead)
    for issue_type, members in sorted(by_issue.items(), key=lambda kv: -len(kv[1])):
        label = issue_label(issue_type) if issue_type else "Autres demandes"
        segments.append(make(_issue_key(issue_type), "🔧 " + label, "🔧", members))

    return segments


def _resolve_segment_leads(tenant_id, segment_key):
    for seg in build_segments(tenant_id):
        if seg["key"] == segment_key:
            return seg["leads"]
    return []


def _personalize(text, lead, company):
    """Fill {name} / {prenom} / {company} placeholders in the campaign body."""
    name = (lead.name or "").strip()
    first = name.split(" ")[0] if name else ""
    return (
        (text or "")
        .replace("{name}", name or "cher client")
        .replace("{prenom}", first or name or "")
        .replace("{company}", company or "")
    )


def send_campaign(tenant_id, segment_key, channel, subject, message):
    """Send a campaign to every reachable lead of a segment.

    channel: "sms", "email" or "both". Returns a summary dict with per-channel
    attempted / sent counts. Best-effort — a single failure never aborts the run.
    """
    from app.models.tenant import Tenant
    from app.services.admin_email import send_email
    from app.services.sms import send_sms

    message = (message or "").strip()
    result = {
        "segment": segment_key,
        "channel": channel,
        "recipients": 0,
        "sms_sent": 0,
        "sms_attempted": 0,
        "email_sent": 0,
        "email_attempted": 0,
    }
    if not message:
        return result

    leads = _resolve_segment_leads(tenant_id, segment_key)
    if not leads:
        return result

    tenant = db.session.get(Tenant, tenant_id)
    company = (tenant.name if tenant else "") or "votre artisan"
    want_sms = channel in ("sms", "both")
    want_email = channel in ("email", "both")
    subject = (subject or "").strip() or f"Message de {company}"

    reached = set()
    for lead in leads:
        body = _personalize(message, lead, company)

        if want_sms and (lead.phone or "").strip():
            result["sms_attempted"] += 1
            if send_sms(lead.phone, body):
                result["sms_sent"] += 1
            reached.add(lead.id)

        email = getattr(lead, "resolved_email", None) or lead.email
        if want_email and email:
            result["email_attempted"] += 1
            html = "<p>" + _personalize(message, lead, company).replace("\n", "<br>") + "</p>"
            row = send_email(
                email,
                _personalize(subject, lead, company),
                html,
                is_html=True,
                tenant_id=tenant_id,
            )
            if row and row.status in ("sent", "simulated"):
                result["email_sent"] += 1
            reached.add(lead.id)

    result["recipients"] = len(reached)
    logger.info("marketing campaign tenant=%s segment=%s result=%s", tenant_id, segment_key, result)
    return result
