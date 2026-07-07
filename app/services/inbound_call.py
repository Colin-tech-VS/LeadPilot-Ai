import logging
import uuid
from datetime import datetime

from app.core.errors import NotFoundError
from app.core.extensions import db
from app.models.lead import Lead
from app.models.tenant import Tenant
from app.services.booking_engine import ACTION_BOOK_NOW, BookingEngine
from app.services.availability import hold_tentative_appointment
from app.services.lead_extractor import LeadExtractor
from app.services.notifications import notify_inbound_call

logger = logging.getLogger(__name__)


def process_inbound_call(
    tenant_id: uuid.UUID,
    phone: str,
    transcript: str,
    *,
    lead_override: dict | None = None,
    request_booking: bool = False,
    send_devis_if_email: bool = False,
) -> dict:
    """Core pipeline: extract → create lead → score → tentative slot + devis email."""
    tenant = db.session.get(Tenant, tenant_id)
    if not tenant:
        raise NotFoundError("Tenant not found")

    from app.services.plan_features import inbound_allowed

    allowed, block_reason = inbound_allowed(tenant)
    if not allowed:
        raise NotFoundError(
            "Subscription inactive" if block_reason == "expired" else "Monthly call quota reached"
        )

    extractor = LeadExtractor()
    extracted = extractor.extract(transcript=transcript, phone=phone)
    extracted = _merge_lead_override(extracted, lead_override)

    booking_engine = BookingEngine()
    booking = booking_engine.process_lead(extracted, tenant)
    from app.services.plan_features import apply_booking_plan_limits, has_feature

    booking = apply_booking_plan_limits(tenant, booking)
    booking = _maybe_boost_booking(tenant, extracted, booking, request_booking)

    client_email = (extracted.get("email") or "").strip().lower() or None

    lead = Lead(
        tenant_id=tenant_id,
        name=extracted.get("name") or "Unknown Caller",
        phone=extracted["phone"],
        email=client_email,
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
        if not client_email:
            booking["action"] = "CALL_BACK"
            booking["email_missing"] = True
            lead.set_booking(booking)
            logger.warning("BOOK_NOW deferred — no email lead=%s", lead.id)
        elif not has_feature(tenant, "auto_booking"):
            booking["action"] = "CALL_BACK"
            booking["plan_limited"] = True
            lead.set_booking(booking)
        else:
            slot = datetime.fromisoformat(booking["suggested_slot"].replace("Z", "+00:00"))
            appointment = hold_tentative_appointment(tenant_id, lead.id, slot)
            if appointment:
                appointment_id = str(appointment.id)
                booking["suggested_slot"] = appointment.date_time.isoformat()
                booking["awaiting_signature"] = True
                booking["quote_pending"] = True
                lead.set_booking(booking)
                quote_id = _send_devis_for_signature(lead, tenant, appointment)
                logger.info(
                    "BOOK_NOW tentative appointment=%s lead=%s slot=%s quote=%s",
                    appointment.id,
                    lead.id,
                    appointment.date_time.isoformat(),
                    quote_id,
                )
            else:
                booking["action"] = "CALL_BACK"
                booking["slot_unavailable"] = True
                lead.set_booking(booking)
                logger.warning("BOOK_NOW failed — no slot lead=%s", lead.id)

    if not quote_id and send_devis_if_email and client_email:
        quote_id = _send_devis_for_signature(
            lead, tenant, appointment=None, require_auto_booking=False
        )

    db.session.commit()

    notify_inbound_call(lead, tenant, booked=False)

    try:
        from app.services.events import CAT_LEAD, LEVEL_SUCCESS, log_event

        has_quote = quote_id is not None
        log_event(
            CAT_LEAD,
            "lead_quote_sent" if has_quote else "lead_created",
            summary=f"{lead.name} — {lead.issue_type or 'demande'}"
            + (" (devis envoyé)" if has_quote else ""),
            level=LEVEL_SUCCESS if has_quote else "info",
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


def _send_devis_for_signature(lead, tenant, appointment=None, *, require_auto_booking: bool = True) -> str | None:
    """Generate and email a pre-signed devis. Email is mandatory."""
    from app.services import quote_engine
    from app.services.plan_features import has_feature
    from app.services.quote_delivery import send_quote

    if require_auto_booking and not has_feature(tenant, "auto_booking"):
        return None
    if not has_feature(tenant, "sms_email_notifications"):
        return None

    if not (lead.email or "").strip():
        logger.warning("Cannot send devis without email lead=%s", lead.id)
        return None

    try:
        if appointment:
            quote = quote_engine.create_voice_booking_quote(lead, tenant, appointment.date_time)
        else:
            quote = quote_engine.create_signed_devis_for_lead(lead, tenant)

        result = send_quote(quote, tenant, channels={"email"})
        if result.get("email"):
            quote.sent_channel = "email"
        elif result.get("sms"):
            quote.sent_channel = "sms"
        _text_devis_link(quote, tenant, lead)
        logger.info("Devis %s sent for lead=%s channel=%s", quote.number, lead.id, quote.sent_channel)
        return str(quote.id)
    except Exception:
        logger.exception("Failed to send devis for lead=%s", lead.id)
        return None


def _text_devis_link(quote, tenant, lead) -> bool:
    """SMS the client the link to their pre-signed devis (best-effort)."""
    from app.services.plan_features import has_feature

    if not has_feature(tenant, "sms_email_notifications"):
        return False
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
        f"Bonjour, votre devis {quote.number} de {company} est prêt. "
        f"Signez-le et réglez l'acompte en ligne : {link}"
    )
    return send_sms(phone, body)


def _merge_lead_override(extracted: dict, override: dict | None) -> dict:
    """Prefer explicit fields collected in chat/voice state over re-extraction."""
    merged = dict(extracted or {})
    if not isinstance(override, dict):
        return merged
    for key in ("name", "phone", "email", "address", "issue_type", "urgency_level", "summary"):
        value = override.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            value = value.strip()
            if key == "email":
                value = value.lower()
        if value:
            merged[key] = value
    if not merged.get("phone") and override.get("phone"):
        merged["phone"] = override["phone"]
    return merged


def _maybe_boost_booking(tenant, extracted: dict, booking: dict, request_booking: bool) -> dict:
    """When the visitor explicitly asks for a RDV, try to book if we have enough data."""
    from app.services.availability import find_next_available_slot
    from app.services.plan_features import has_feature

    if not request_booking or booking.get("action") == ACTION_BOOK_NOW:
        return booking
    if not has_feature(tenant, "auto_booking"):
        return booking
    if not (extracted.get("email") and extracted.get("address") and extracted.get("phone")):
        return booking

    slot = find_next_available_slot(tenant.id)
    if not slot:
        return booking

    boosted = dict(booking)
    boosted["action"] = ACTION_BOOK_NOW
    boosted["suggested_slot"] = slot.isoformat()
    boosted["reason"] = "Visitor requested an appointment — next available slot proposed."
    return boosted


def follow_up_chat_lead(
    tenant_id: uuid.UUID,
    lead_id: uuid.UUID,
    lead_override: dict | None = None,
    *,
    request_booking: bool = False,
    request_devis: bool = False,
) -> dict:
    """Update an existing chat lead when the visitor asks for RDV or devis."""
    from app.models.quote import DOC_DEVIS, Quote

    tenant = db.session.get(Tenant, tenant_id)
    lead = db.session.get(Lead, lead_id)
    if not tenant or not lead or lead.tenant_id != tenant_id:
        return {"quote_id": None, "appointment_id": None, "booking": None}

    extracted = {
        "name": lead.name,
        "phone": lead.phone,
        "email": (lead.email or "").strip().lower() or None,
        "address": lead.address,
        "issue_type": lead.issue_type,
        "urgency_level": lead.urgency_level,
        "summary": lead.summary,
    }
    extracted = _merge_lead_override(extracted, lead_override)

    if extracted.get("name"):
        lead.name = extracted["name"]
    if extracted.get("email"):
        lead.email = extracted["email"]
    if extracted.get("address"):
        lead.address = extracted["address"]
    if extracted.get("issue_type"):
        lead.issue_type = extracted["issue_type"]
    if extracted.get("urgency_level"):
        lead.urgency_level = extracted["urgency_level"]
    if extracted.get("summary"):
        lead.summary = extracted["summary"]

    booking_engine = BookingEngine()
    booking = booking_engine.process_lead(extracted, tenant)
    from app.services.plan_features import apply_booking_plan_limits, has_feature

    booking = apply_booking_plan_limits(tenant, booking)
    booking = _maybe_boost_booking(tenant, extracted, booking, request_booking)
    lead.set_booking(booking)

    appointment_id = None
    quote_id = None
    existing_quote = (
        Quote.query.filter_by(lead_id=lead.id, doc_type=DOC_DEVIS)
        .order_by(Quote.created_at.desc())
        .first()
    )

    if booking.get("action") == ACTION_BOOK_NOW and booking.get("suggested_slot"):
        client_email = (lead.email or "").strip().lower() or None
        if client_email and has_feature(tenant, "auto_booking"):
            slot = datetime.fromisoformat(booking["suggested_slot"].replace("Z", "+00:00"))
            appointment = hold_tentative_appointment(tenant_id, lead.id, slot)
            if appointment:
                appointment_id = str(appointment.id)
                if not existing_quote:
                    quote_id = _send_devis_for_signature(lead, tenant, appointment)

    if not quote_id and request_devis and (lead.email or "").strip() and not existing_quote:
        quote_id = _send_devis_for_signature(
            lead, tenant, appointment=None, require_auto_booking=False
        )

    db.session.commit()
    return {
        "quote_id": quote_id,
        "appointment_id": appointment_id,
        "booking": booking,
    }
