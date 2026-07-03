"""Public demo simulation for landing page (no DB writes)."""

import uuid

from app.services.booking_engine import BookingEngine
from app.services.lead_extractor import LeadExtractor

_DEMO_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


class _DemoTenant:
    name = "Plomberie Demo"
    city = "Paris"
    latitude = 48.8566
    longitude = 2.3522
    service_radius_km = 30
    id = _DEMO_TENANT_ID


def simulate_inbound_demo(transcript: str, phone: str = "+33600000000") -> dict:
    extractor = LeadExtractor()
    extracted = extractor.extract(transcript=transcript.strip(), phone=phone.strip())

    booking_engine = BookingEngine()
    booking = booking_engine.process_lead(extracted, _DemoTenant())

    return {
        "success": True,
        "demo": True,
        "extracted_data": extracted,
        "booking": booking,
    }
