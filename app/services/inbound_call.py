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
from app.services.notifications import notify_high_urgency_lead

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
        else:
            booking["action"] = "CALL_BACK"
            booking["slot_unavailable"] = True
            lead.set_booking(booking)
            logger.warning("BOOK_NOW failed — no slot lead=%s", lead.id)

    db.session.commit()

    if extracted.get("urgency_level") == "high":
        notify_high_urgency_lead(lead, tenant)

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
        "extracted_data": extracted,
        "booking": booking,
    }
