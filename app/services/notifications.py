"""Notification service.

Records important events for a plumber as ``Notification`` rows. The front-end
polls these (see static/js/notifications.js) and raises an in-page toast plus a
native OS notification — so the plumber is alerted on PC and mobile even when
the tab is in the background, as long as a session is open on the web app.
"""

import logging
import uuid

from app.core.extensions import db
from app.models.notification import (
    TYPE_APPOINTMENT,
    TYPE_NEW_LEAD,
    TYPE_QUOTE_ACCEPTED,
    TYPE_QUOTE_REFUSED,
    TYPE_URGENT_LEAD,
    Notification,
)

logger = logging.getLogger(__name__)

# French labels for issue slugs, reused across notification copy.
ISSUE_LABELS_FR = {
    "leak": "fuite d'eau",
    "clogged_drain": "canalisation bouchée",
    "clogged_toilet": "WC bouché",
    "water_heater": "chauffe-eau",
    "toilet": "WC",
    "pipe_issue": "canalisation",
    "burst_pipe": "canalisation percée",
    "flooding": "dégât des eaux",
    "no_water": "coupure d'eau",
    "general_inquiry": "demande",
}


def _issue_fr(issue_type):
    return ISSUE_LABELS_FR.get((issue_type or "").lower(), "intervention")


def push_notification(tenant_id, event_type, title, body="", icon="🔔", url="/dashboard", commit=True):
    """Persist a notification for a tenant. Best-effort: never raises."""
    try:
        tid = tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(str(tenant_id))
        notif = Notification(
            tenant_id=tid,
            type=event_type,
            title=(title or "").strip()[:255] or "Notification",
            body=(body or "").strip()[:500] or None,
            icon=icon or "🔔",
            url=url or "/dashboard",
        )
        db.session.add(notif)
        if commit:
            db.session.commit()
        else:
            db.session.flush()
        return notif
    except Exception:
        logger.exception("push_notification failed tenant=%s type=%s", tenant_id, event_type)
        if commit:
            db.session.rollback()
        return None


# --------------------------------------------------------------------------
# High-level helpers used across the app
# --------------------------------------------------------------------------
def notify_inbound_call(lead, tenant, booked=False, commit=True):
    """Emit the single most relevant notification for a handled call."""
    name = (getattr(lead, "name", None) or "Client").strip() or "Client"
    issue = _issue_fr(getattr(lead, "issue_type", None))
    address = (getattr(lead, "address", None) or "").strip()
    urgent = (getattr(lead, "urgency_level", None) or "").lower() == "high"

    if booked:
        title = f"📅 Nouveau rendez-vous — {name}"
        parts = [issue]
        if address:
            parts.append(address)
        return push_notification(
            tenant.id, TYPE_APPOINTMENT, title, " · ".join(parts),
            icon="📅", url="/appointments", commit=commit,
        )
    if urgent:
        title = f"🚨 Appel urgent — {name}"
        parts = [issue]
        if address:
            parts.append(address)
        return push_notification(
            tenant.id, TYPE_URGENT_LEAD, title, " · ".join(parts),
            icon="🚨", url="/leads", commit=commit,
        )
    title = f"📞 Nouveau lead — {name}"
    return push_notification(
        tenant.id, TYPE_NEW_LEAD, title, issue, icon="📞", url="/leads", commit=commit,
    )


def notify_quote_accepted(quote, appointment=None, invoice=None, commit=True):
    """Devis accepted by a client: alert the plumber (+ invoice / RDV info)."""
    client = (quote.client_name or "Client").strip() or "Client"
    bits = [f"{quote.total_ttc:.2f} € TTC"]
    if invoice is not None and invoice.number:
        bits.append(f"facture {invoice.number}")
    if appointment is not None and appointment.date_time:
        bits.append("RDV planifié le " + appointment.date_time.strftime("%d/%m à %H:%M"))
    return push_notification(
        quote.tenant_id, TYPE_QUOTE_ACCEPTED,
        f"✅ Devis accepté — {client}", " · ".join(bits),
        icon="✅", url=f"/quotes/{quote.id}", commit=commit,
    )


def notify_quote_refused(quote, commit=True):
    client = (quote.client_name or "Client").strip() or "Client"
    return push_notification(
        quote.tenant_id, TYPE_QUOTE_REFUSED,
        f"❌ Devis refusé — {client}", quote.number or "",
        icon="❌", url=f"/quotes/{quote.id}", commit=commit,
    )


def notify_high_urgency_lead(lead, tenant):
    """Backwards-compatible entrypoint for the urgent-lead alert."""
    message = (
        f"[ALERT] High urgency lead — tenant={tenant.name} lead={lead.id} "
        f"phone={lead.phone} issue={lead.issue_type}"
    )
    logger.warning(message)
