import logging
import uuid
from datetime import datetime

from app.core.errors import NotFoundError
from app.core.extensions import db
from app.models.lead import Lead
from app.models.tenant import Tenant
from app.services.booking_engine import ACTION_BOOK_NOW, BookingEngine
from app.services.availability import book_appointment
from app.services.lead_extractor import LeadExtractor
from app.services.notifications import notify_inbound_call

logger = logging.getLogger(__name__)


def process_inbound_call(tenant_id: uuid.UUID, phone: str, transcript: str) -> dict:
    """Core pipeline: extract → create lead → score → suggest booking."""
    tenant = db.session.get(Tenant, tenant_id)
    if not tenant:
        raise NotFoundError("Tenant not found")

    extractor = LeadExtractor()
    extracted = extractor.extract(transcript=transcript, phone=phone)

    booking_engine = BookingEngine()
    booking = booking_engine.process_lead(extracted, tenant)

    lead = Lead(
        tenant_id=tenant_id,
        name=extracted.get("name") or "Unknown Caller",
        phone=extracted["phone"],
        address=extracted.get("address"),
        latitude=extracted.get("latitude"),
        longitude=extracted.get("longitude"),
        issue_type=extracted.get("issue_type"),
        urgency_level=extracted.get("urgency_level"),
        summary=extracted.get("summary"),
        status="new",
    )
    lead.set_booking(booking)
    db.session.add(lead)
    db.session.flush()

    appointment_id = None
    quote_id = None
    if booking.get("action") == ACTION_BOOK_NOW and booking.get("suggested_slot"):
        slot = datetime.fromisoformat(booking["suggested_slot"].replace("Z", "+00:00"))
        appointment = book_appointment(tenant_id, lead.id, slot)
        if appointment:
            appointment_id = str(appointment.id)
            booking["suggested_slot"] = appointment.date_time.isoformat()
            lead.set_booking(booking)
            lead.status = "booked"
            logger.info(
                "BOOK_NOW appointment=%s lead=%s slot=%s",
                appointment.id,
                lead.id,
                appointment.date_time.isoformat(),
            )
            # Before confirming the RDV, send a devis already signed by the
            # plumber so the client receives a ready-to-accept quote.
            quote_id = _send_signed_devis(lead, tenant)
        else:
            booking["action"] = "CALL_BACK"
            booking["slot_unavailable"] = True
            lead.set_booking(booking)
            logger.warning("BOOK_NOW failed — no slot lead=%s", lead.id)

    db.session.commit()

    # Alert the plumber live (web + mobile) about this call — a booked RDV, an
    # urgent request, or simply a new lead.
    notify_inbound_call(lead, tenant, booked=appointment_id is not None)

    # Record the event for the admin log / analytics funnel.
    try:
        from app.services.events import CAT_LEAD, LEVEL_SUCCESS, log_event

        booked = appointment_id is not None
        log_event(
            CAT_LEAD,
            "lead_booked" if booked else "lead_created",
            summary=f"{lead.name} — {lead.issue_type or 'demande'}"
            + (" (RDV pris)" if booked else ""),
            level=LEVEL_SUCCESS if booked else "info",
            tenant_id=tenant_id,
            actor="voix IA",
            meta={"urgency": lead.urgency_level, "action": booking.get("action")},
        )
    except Exception:  # pragma: no cover
        logger.exception("event log failed for lead %s", lead.id)

    logger.info(
        "Inbound call processed tenant=%s lead=%s action=%s score=%s",
        tenant_id,
        lead.id,
        booking.get("action"),
        booking.get("priority_score"),
    )
    return {
        "success": True,
        "lead_id": str(lead.id),
        "appointment_id": appointment_id,
        "quote_id": quote_id,
        "extracted_data": extracted,
        "booking": booking,
    }


def _send_signed_devis(lead, tenant) -> str | None:
    """Generate and send a pre-signed devis for a just-booked lead.

    Best-effort: a failure here must never break the call flow, so any error is
    logged and the booking still succeeds.
    """
    from app.services import quote_engine
    from app.services.notifications import notify_quote_sent

    try:
        quote = quote_engine.create_signed_devis_for_lead(lead, tenant)
        notify_quote_sent(quote, commit=False)
        _text_devis_link(quote, tenant, lead)
        logger.info("Signed devis %s sent for lead=%s", quote.number, lead.id)
        return str(quote.id)
    except Exception:
        logger.exception("Failed to send signed devis for lead=%s", lead.id)
        return None


def _text_devis_link(quote, tenant, lead) -> bool:
    """SMS the client the link to their pre-signed devis (best-effort)."""
    from flask import url_for

    from app.services.sms import send_sms

    phone = (getattr(lead, "phone", None) or quote.client_phone or "").strip()
    if not phone:
        return False

    try:
        link = url_for(
            "quotes.public_quote",
            quote_id=quote.id,
            token=quote.public_token,
            _external=True,
        )
    except Exception:
        logger.exception("Could not build devis link for SMS (quote=%s)", quote.id)
        return False

    company = (tenant.name or "votre plombier").strip()
    body = (
        f"Bonjour, voici votre devis {quote.number} de {company}, "
        f"déjà signé. Consultez-le et validez-le en ligne : {link}"
    )
    return send_sms(phone, body)
