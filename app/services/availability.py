import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.core.extensions import db
from app.models.appointment import Appointment

PARIS_TZ = ZoneInfo("Europe/Paris")
SLOT_DURATION = timedelta(hours=1)
BUSINESS_START_HOUR = 9
BUSINESS_END_HOUR = 17
MAX_SEARCH_DAYS = 14

BUSY_STATUSES = ("scheduled", "confirmed")


def normalize_slot(dt: datetime) -> datetime:
    """Snap a datetime to the start of an hourly slot (stored as UTC)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    paris = dt.astimezone(PARIS_TZ)
    paris = paris.replace(minute=0, second=0, microsecond=0)
    return paris.astimezone(timezone.utc)


def _is_business_hour(paris_dt: datetime) -> bool:
    if paris_dt.weekday() == 6:
        return False
    return BUSINESS_START_HOUR <= paris_dt.hour < BUSINESS_END_HOUR


def _advance_to_business_hours(paris_dt: datetime) -> datetime:
    paris_dt = paris_dt.replace(minute=0, second=0, microsecond=0)

    while paris_dt.weekday() == 6:
        paris_dt = (paris_dt + timedelta(days=1)).replace(hour=BUSINESS_START_HOUR)

    if paris_dt.hour < BUSINESS_START_HOUR:
        return paris_dt.replace(hour=BUSINESS_START_HOUR)
    if paris_dt.hour >= BUSINESS_END_HOUR:
        next_day = paris_dt + timedelta(days=1)
        while next_day.weekday() == 6:
            next_day += timedelta(days=1)
        return next_day.replace(hour=BUSINESS_START_HOUR, minute=0, second=0, microsecond=0)
    return paris_dt


def get_busy_slots(tenant_id) -> set[datetime]:
    """Return normalized UTC datetimes already booked for this tenant."""
    tid = tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(str(tenant_id))
    now = datetime.now(timezone.utc)

    appointments = (
        Appointment.query.filter(
            Appointment.tenant_id == tid,
            Appointment.status.in_(BUSY_STATUSES),
            Appointment.date_time >= now - SLOT_DURATION,
        )
        .all()
    )
    return {normalize_slot(appt.date_time) for appt in appointments}


def is_slot_available(tenant_id, slot_dt: datetime) -> bool:
    slot = normalize_slot(slot_dt)
    paris = slot.astimezone(PARIS_TZ)
    now = datetime.now(timezone.utc)

    if slot <= now:
        return False
    if not _is_business_hour(paris):
        return False
    return slot not in get_busy_slots(tenant_id)


def find_next_available_slot(tenant_id, start_from: datetime | None = None) -> datetime | None:
    """Find the next free hourly slot within business hours."""
    busy = get_busy_slots(tenant_id)

    if start_from:
        paris_start = start_from.astimezone(PARIS_TZ)
    else:
        paris_start = datetime.now(PARIS_TZ) + timedelta(hours=1)

    candidate = _advance_to_business_hours(paris_start)
    deadline = datetime.now(PARIS_TZ) + timedelta(days=MAX_SEARCH_DAYS)

    while candidate <= deadline:
        slot_utc = candidate.astimezone(timezone.utc)
        if _is_business_hour(candidate) and slot_utc > datetime.now(timezone.utc):
            if slot_utc not in busy:
                return slot_utc
        candidate += SLOT_DURATION
        if not _is_business_hour(candidate):
            candidate = _advance_to_business_hours(candidate)

    return None


def book_appointment(tenant_id, lead_id, slot_dt: datetime) -> Appointment | None:
    """
    Book an appointment only if the slot is free.
    If the preferred slot is taken, tries the next available slot.
    """
    tid = tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(str(tenant_id))
    lid = lead_id if isinstance(lead_id, uuid.UUID) else uuid.UUID(str(lead_id))

    preferred = normalize_slot(slot_dt)
    slot = preferred if is_slot_available(tid, preferred) else find_next_available_slot(tid, preferred)

    if not slot:
        return None

    busy = get_busy_slots(tid)
    if slot in busy:
        slot = find_next_available_slot(tid, slot + SLOT_DURATION)
        if not slot or slot in get_busy_slots(tid):
            return None

    appointment = Appointment(
        tenant_id=tid,
        lead_id=lid,
        date_time=slot,
        status="scheduled",
    )
    db.session.add(appointment)

    # Booking an appointment means the lead is now a confirmed job: promote it
    # so the prospect card / dashboard reflect "réservé" without a manual step.
    from app.models.lead import Lead

    lead = db.session.get(Lead, lid)
    if lead and lead.status == "new" and lead.cancelled_at is None:
        lead.status = "booked"

    db.session.commit()
    return appointment
