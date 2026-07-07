"""Devis / facture engine.

Turns a lead (or a blank form) into a structured quote, pre-fills line items
from a plumbing catalogue keyed on the detected issue type, numbers documents
sequentially per tenant and year, and surfaces quotes that need a follow-up
(relance) — the step artisans most often forget.
"""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func

from app.core.extensions import db
from app.models.quote import (
    DOC_DEVIS,
    DOC_FACTURE,
    STATUS_ACCEPTED,
    STATUS_DRAFT,
    STATUS_SENT,
    Quote,
)

# Default TVA for renovation work on housing older than 2 years (France).
DEFAULT_TVA = 10.0

# Days after which a sent, undecided devis should be relaunched.
FOLLOWUP_AFTER_DAYS = 3
# Default validity window printed on a devis.
DEVIS_VALIDITY_DAYS = 30


def utcnow():
    return datetime.now(timezone.utc)


# Catalogue of common plumbing interventions. Prices are HT (indicative
# starting points the plumber adjusts before sending). Each issue type maps to
# a ready-made set of line items so the AI can pre-draft a devis from a call.
SERVICE_CATALOG = {
    "leak": [
        {"label": "Déplacement et diagnostic fuite", "quantity": 1, "unit_price": 60, "tva_rate": DEFAULT_TVA},
        {"label": "Réparation de fuite (raccord / joint)", "quantity": 1, "unit_price": 110, "tva_rate": DEFAULT_TVA},
        {"label": "Fournitures (joints, raccords)", "quantity": 1, "unit_price": 25, "tva_rate": 20},
    ],
    "burst_pipe": [
        {"label": "Intervention urgente canalisation", "quantity": 1, "unit_price": 120, "tva_rate": DEFAULT_TVA},
        {"label": "Remplacement section de tuyau", "quantity": 1, "unit_price": 180, "tva_rate": DEFAULT_TVA},
        {"label": "Fournitures plomberie", "quantity": 1, "unit_price": 60, "tva_rate": 20},
    ],
    "clogged_drain": [
        {"label": "Débouchage canalisation / évier", "quantity": 1, "unit_price": 90, "tva_rate": DEFAULT_TVA},
        {"label": "Déplacement", "quantity": 1, "unit_price": 50, "tva_rate": DEFAULT_TVA},
    ],
    "clogged_toilet": [
        {"label": "Débouchage WC", "quantity": 1, "unit_price": 90, "tva_rate": DEFAULT_TVA},
        {"label": "Déplacement", "quantity": 1, "unit_price": 50, "tva_rate": DEFAULT_TVA},
    ],
    "water_heater": [
        {"label": "Dépose ancien chauffe-eau", "quantity": 1, "unit_price": 90, "tva_rate": DEFAULT_TVA},
        {"label": "Fourniture chauffe-eau électrique 200L", "quantity": 1, "unit_price": 450, "tva_rate": DEFAULT_TVA},
        {"label": "Pose et raccordement chauffe-eau", "quantity": 1, "unit_price": 260, "tva_rate": DEFAULT_TVA},
    ],
    "toilet": [
        {"label": "Dépose ancien WC", "quantity": 1, "unit_price": 70, "tva_rate": DEFAULT_TVA},
        {"label": "Fourniture WC", "quantity": 1, "unit_price": 180, "tva_rate": DEFAULT_TVA},
        {"label": "Pose et raccordement WC", "quantity": 1, "unit_price": 150, "tva_rate": DEFAULT_TVA},
    ],
    "pipe_issue": [
        {"label": "Diagnostic canalisation", "quantity": 1, "unit_price": 60, "tva_rate": DEFAULT_TVA},
        {"label": "Réparation canalisation", "quantity": 1, "unit_price": 150, "tva_rate": DEFAULT_TVA},
    ],
    "flooding": [
        {"label": "Intervention urgente dégât des eaux", "quantity": 1, "unit_price": 150, "tva_rate": DEFAULT_TVA},
        {"label": "Recherche et arrêt de fuite", "quantity": 1, "unit_price": 120, "tva_rate": DEFAULT_TVA},
    ],
    "general_inquiry": [
        {"label": "Déplacement et main d'œuvre", "quantity": 1, "unit_price": 60, "tva_rate": DEFAULT_TVA},
    ],
}

DEFAULT_ITEMS = [
    {"label": "Déplacement et main d'œuvre", "quantity": 1, "unit_price": 60, "tva_rate": DEFAULT_TVA},
]


def suggest_items_for_issue(issue_type):
    """Return a fresh copy of catalogue line items for an issue type."""
    items = SERVICE_CATALOG.get((issue_type or "").lower())
    source = items if items else DEFAULT_ITEMS
    return [dict(item) for item in source]


def suggest_title_for_issue(issue_type):
    from app.utils.i18n import issue_label

    label = issue_label(issue_type) if issue_type else None
    if not label or label == "—":
        return "Intervention plomberie"
    return f"Intervention plomberie — {label}"


def generate_number(tenant_id, doc_type):
    """Sequential per-tenant, per-year, per-type number (e.g. DEV-2026-0007)."""
    tid = tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(str(tenant_id))
    year = utcnow().year
    prefix = "FAC" if doc_type == DOC_FACTURE else "DEV"

    count = (
        db.session.query(func.count(Quote.id))
        .filter(
            Quote.tenant_id == tid,
            Quote.doc_type == doc_type,
            func.extract("year", Quote.created_at) == year,
        )
        .scalar()
        or 0
    )
    return f"{prefix}-{year}-{count + 1:04d}"


def build_draft_from_lead(lead, tenant):
    """Create (unsaved) a draft devis pre-filled from a lead."""
    quote = Quote(
        tenant_id=tenant.id,
        lead_id=lead.id if lead else None,
        doc_type=DOC_DEVIS,
        status=STATUS_DRAFT,
        client_name=(lead.name if lead else None),
        client_phone=(lead.phone if lead else None),
        client_email=(lead.email if lead else None),
        client_address=(lead.address if lead else None),
        title=suggest_title_for_issue(lead.issue_type if lead else None),
        deposit_percent=30,
        valid_until=utcnow() + timedelta(days=DEVIS_VALIDITY_DAYS),
    )
    quote.set_items(suggest_items_for_issue(lead.issue_type if lead else None))
    quote.ensure_token()
    return quote


def mark_sent(quote):
    quote.status = STATUS_SENT
    quote.sent_at = utcnow()
    quote.ensure_token()


def create_signed_devis_for_lead(lead, tenant):
    """Build, number, mark-sent and persist a devis for a lead.

    Used by the voice AI: when it agrees to schedule an appointment it first
    sends a devis already signed by the plumber (the artisan's signature is
    rendered on every devis from ``tenant.signature``). The devis is pre-filled
    from the detected issue and lands in the plumber's list as "sent", with a
    client-facing accept/refuse link ready to share.

    The caller is responsible for committing the session.
    """
    quote = build_draft_from_lead(lead, tenant)
    quote.number = generate_number(tenant.id, DOC_DEVIS)
    mark_sent(quote)
    db.session.add(quote)
    db.session.flush()
    return quote


def create_online_booking_quote(lead, tenant, slot_dt, issue: str | None = None):
    """Create and send a pre-filled devis for an online booking request.

    The appointment stays ``tentative`` until the client accepts the devis.
    Caller must already have created the lead and tentative appointment.
    """
    from zoneinfo import ZoneInfo

    paris = ZoneInfo("Europe/Paris")
    when_label = slot_dt.astimezone(paris).strftime("%A %d/%m/%Y à %H:%M")

    quote = build_draft_from_lead(lead, tenant)
    quote.number = generate_number(tenant.id, DOC_DEVIS)
    quote.title = f"{quote.title or 'Intervention'} — RDV {when_label}"
    if issue:
        quote.notes = (
            f"Créneau demandé : {when_label}\n"
            f"Besoin : {issue}\n\n"
            "Le rendez-vous sera confirmé après signature de ce devis."
        )
    else:
        quote.notes = (
            f"Créneau demandé : {when_label}\n\n"
            "Le rendez-vous sera confirmé après signature de ce devis."
        )
    mark_sent(quote)
    db.session.add(quote)
    db.session.flush()
    return quote


def mark_reminded(quote):
    quote.last_reminded_at = utcnow()
    quote.reminder_count = (quote.reminder_count or 0) + 1


def convert_to_invoice(quote, tenant_id):
    """Create a facture (unsaved) mirroring an accepted devis."""
    invoice = Quote(
        tenant_id=tenant_id,
        lead_id=quote.lead_id,
        doc_type=DOC_FACTURE,
        status=STATUS_DRAFT,
        number=generate_number(tenant_id, DOC_FACTURE),
        client_name=quote.client_name,
        client_phone=quote.client_phone,
        client_address=quote.client_address,
        title=quote.title,
        deposit_percent=quote.deposit_percent,
        notes=quote.notes,
    )
    invoice.items_json = quote.items_json
    invoice.ensure_token()
    return invoice


def accept_quote(quote):
    """Accept a devis and run the post-acceptance automation.

    On the *first* acceptance this:
      1. marks the devis accepted,
      2. auto-generates the matching facture (invoice),
      3. auto-schedules the next available appointment.

    Idempotent: the automation runs only when the devis was not already
    accepted, so re-submitting the client link never duplicates anything.
    Returns ``{"invoice", "appointment", "already"}``. The caller commits.
    """
    already = quote.status == STATUS_ACCEPTED or quote.accepted_at is not None

    quote.status = STATUS_ACCEPTED
    if quote.accepted_at is None:
        quote.accepted_at = utcnow()

    if already or quote.doc_type != DOC_DEVIS:
        return {"invoice": None, "appointment": None, "already": True}

    invoice = convert_to_invoice(quote, quote.tenant_id)
    db.session.add(invoice)

    appointment = _auto_schedule_from_quote(quote)
    return {"invoice": invoice, "appointment": appointment, "already": False}


def _auto_schedule_from_quote(quote):
    """Confirm a held slot or book the next free one when a devis is accepted."""
    from app.models.appointment import TENTATIVE_STATUS
    from app.models.lead import Lead
    from app.services.availability import (
        book_appointment,
        confirm_tentative_appointment,
        find_next_available_slot,
    )

    lead_id = quote.lead_id
    if not lead_id:
        lead = Lead(
            tenant_id=quote.tenant_id,
            name=(quote.client_name or "Client").strip() or "Client",
            phone=(quote.client_phone or "").strip(),
            email=quote.client_email,
            address=quote.client_address,
            issue_type="general_inquiry",
            urgency_level="medium",
            summary=(quote.title or "Devis accepté"),
            status="booked",
        )
        db.session.add(lead)
        db.session.flush()
        quote.lead_id = lead.id
        lead_id = lead.id
    else:
        lead = db.session.get(Lead, lead_id)
        tentative = (
            lead.appointments.filter_by(status=TENTATIVE_STATUS).first()
            if lead
            else None
        )
        if tentative:
            return confirm_tentative_appointment(tentative)
        if lead and lead.status == "new" and lead.cancelled_at is None:
            lead.status = "booked"

    slot = find_next_available_slot(quote.tenant_id)
    if not slot:
        return None
    return book_appointment(quote.tenant_id, lead_id, slot)


def quotes_needing_followup(tenant_id):
    """Sent devis with no client decision, past the follow-up window."""
    tid = tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(str(tenant_id))
    cutoff = utcnow() - timedelta(days=FOLLOWUP_AFTER_DAYS)

    quotes = (
        Quote.query.filter(
            Quote.tenant_id == tid,
            Quote.doc_type == DOC_DEVIS,
            Quote.status == STATUS_SENT,
        )
        .order_by(Quote.sent_at.asc())
        .all()
    )
    due = []
    for q in quotes:
        anchor = q.last_reminded_at or q.sent_at
        if anchor is None:
            continue
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=timezone.utc)
        if anchor <= cutoff:
            due.append(q)
    return due


def followup_count(tenant_id):
    return len(quotes_needing_followup(tenant_id))


def pending_quote_count(tenant_id):
    """Devis still in play (draft or sent) — shown on the dashboard KPI."""
    tid = tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(str(tenant_id))
    return (
        Quote.query.filter(
            Quote.tenant_id == tid,
            Quote.doc_type == DOC_DEVIS,
            Quote.status.in_((STATUS_DRAFT, STATUS_SENT)),
        ).count()
    )
