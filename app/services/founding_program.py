"""Les 50 premiers artisans — seats, activation, drip, admin KPIs.

A founding seat is a real Tenant + User created through /50-artisans.
Counters never invent volume: they read these tables and existing Leads.
"""
from __future__ import annotations

import logging
import re
import secrets
from datetime import datetime, timedelta, timezone

from app.core.errors import AppError, ConflictError
from app.core.extensions import db
from app.models.founding import (
    SOURCES,
    STATUSES,
    FoundingParticipant,
    FoundingStatusEvent,
    FoundingWaitlist,
)
from app.models.lead import Lead
from app.models.page_view import PageView
from app.models.tenant import Tenant, utcnow
from app.models.user import User
from app.services import content_studio
from app.services.events import CAT_AUTH, LEVEL_INFO, LEVEL_SUCCESS, log_event

logger = logging.getLogger(__name__)

SETTING_ENABLED = "founding_enabled"
SETTING_MAX = "founding_max"
SETTING_DURATION = "founding_duration_days"
SETTING_WAITLIST = "founding_waitlist_enabled"
SETTING_NUDGE_INACTIVE = "founding_nudge_inactive_days"
SETTING_NUDGE_NO_USAGE = "founding_nudge_no_usage_days"
SETTING_AT_RISK = "founding_at_risk_days"
SETTING_START = "founding_start_date"
SETTING_END = "founding_end_date"
SETTING_POST_OFFER = "founding_post_offer"

# Gifted Starter window for /50-artisans. Distinct from the 14-day full trial
# on /register: 30 days of the Starter feature set, no card, no dedicated line.
STARTER_GIFT_DAYS = 30
STARTER_GIFT_PLAN = "starter"

DEFAULTS = {
    SETTING_ENABLED: "1",
    SETTING_MAX: "50",
    SETTING_DURATION: str(STARTER_GIFT_DAYS),
    SETTING_WAITLIST: "1",
    SETTING_NUDGE_INACTIVE: "2",
    SETTING_NUDGE_NO_USAGE: "3",
    SETTING_AT_RISK: "5",
    SETTING_START: "",
    SETTING_END: "",
    SETTING_POST_OFFER: STARTER_GIFT_PLAN,
}

_gift_defaults_ready = False

ACTIVATION_STEPS = (
    ("account", "Compte créé", None),
    ("profile", "Fiche entreprise", "web.settings_page"),
    ("phone", "Ligne configurée", "web.settings_page"),
    ("first_usage", "Premier usage", "web.dashboard"),
    ("first_handled", "Première demande traitée", "web.leads_page"),
)


def _int_setting(key: str, default: int, *, minimum: int = 1, maximum: int = 500) -> int:
    raw = (content_studio.get_setting(key, DEFAULTS[key]) or DEFAULTS[key]).strip()
    try:
        value = int(raw)
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def _ensure_gift_defaults() -> None:
    """One-shot: old default was 14-day trial copy; the gift is 30-day Starter."""
    global _gift_defaults_ready
    if _gift_defaults_ready:
        return
    raw = (content_studio.get_setting(SETTING_DURATION, "") or "").strip()
    if raw in ("", "14"):
        content_studio.set_setting(SETTING_DURATION, str(STARTER_GIFT_DAYS))
    post = (content_studio.get_setting(SETTING_POST_OFFER, "") or "").strip()
    if not post:
        content_studio.set_setting(SETTING_POST_OFFER, STARTER_GIFT_PLAN)
    _gift_defaults_ready = True


def get_config() -> dict:
    _ensure_gift_defaults()
    enabled = (content_studio.get_setting(SETTING_ENABLED, "1") or "1") == "1"
    waitlist = (content_studio.get_setting(SETTING_WAITLIST, "1") or "1") == "1"
    return {
        "enabled": enabled,
        "max_participants": _int_setting(SETTING_MAX, 50, minimum=1, maximum=500),
        "duration_days": _int_setting(SETTING_DURATION, STARTER_GIFT_DAYS, minimum=1, maximum=365),
        "waitlist_enabled": waitlist,
        "nudge_inactive_days": _int_setting(SETTING_NUDGE_INACTIVE, 2, maximum=30),
        "nudge_no_usage_days": _int_setting(SETTING_NUDGE_NO_USAGE, 3, maximum=30),
        "at_risk_days": _int_setting(SETTING_AT_RISK, 5, maximum=60),
        "start_date": (content_studio.get_setting(SETTING_START, "") or "").strip(),
        "end_date": (content_studio.get_setting(SETTING_END, "") or "").strip(),
        "post_offer": (content_studio.get_setting(SETTING_POST_OFFER, STARTER_GIFT_PLAN) or STARTER_GIFT_PLAN).strip(),
        "gift_plan": STARTER_GIFT_PLAN,
    }


def save_config(values: dict) -> dict:
    mapping = {
        SETTING_ENABLED: "1" if values.get("enabled") else "0",
        SETTING_MAX: str(_int_setting(SETTING_MAX, int(values.get("max_participants") or 50))),
        SETTING_DURATION: str(_int_setting(SETTING_DURATION, int(values.get("duration_days") or STARTER_GIFT_DAYS))),
        SETTING_WAITLIST: "1" if values.get("waitlist_enabled") else "0",
        SETTING_NUDGE_INACTIVE: str(int(values.get("nudge_inactive_days") or 2)),
        SETTING_NUDGE_NO_USAGE: str(int(values.get("nudge_no_usage_days") or 3)),
        SETTING_AT_RISK: str(int(values.get("at_risk_days") or 5)),
        SETTING_START: (values.get("start_date") or "")[:32],
        SETTING_END: (values.get("end_date") or "")[:32],
        SETTING_POST_OFFER: (values.get("post_offer") or STARTER_GIFT_PLAN)[:40],
    }
    # Re-read through setters with clamping via _int_setting after write.
    content_studio.set_setting(SETTING_ENABLED, mapping[SETTING_ENABLED])
    content_studio.set_setting(SETTING_WAITLIST, mapping[SETTING_WAITLIST])
    content_studio.set_setting(SETTING_START, mapping[SETTING_START])
    content_studio.set_setting(SETTING_END, mapping[SETTING_END])
    content_studio.set_setting(SETTING_POST_OFFER, mapping[SETTING_POST_OFFER])
    try:
        content_studio.set_setting(SETTING_MAX, str(max(1, min(500, int(values.get("max_participants") or 50)))))
        content_studio.set_setting(
            SETTING_DURATION, str(max(1, min(365, int(values.get("duration_days") or STARTER_GIFT_DAYS))))
        )
        content_studio.set_setting(
            SETTING_NUDGE_INACTIVE, str(max(1, min(30, int(values.get("nudge_inactive_days") or 2))))
        )
        content_studio.set_setting(
            SETTING_NUDGE_NO_USAGE, str(max(1, min(30, int(values.get("nudge_no_usage_days") or 3))))
        )
        content_studio.set_setting(
            SETTING_AT_RISK, str(max(1, min(60, int(values.get("at_risk_days") or 5))))
        )
    except (TypeError, ValueError):
        pass
    return get_config()


def occupied_count() -> int:
    return FoundingParticipant.query.count()


def max_participants() -> int:
    return get_config()["max_participants"]


def remaining_seats() -> int:
    return max(0, max_participants() - occupied_count())


def is_full() -> bool:
    return occupied_count() >= max_participants()


def _parse_date(raw: str) -> datetime | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        if len(text) == 10:
            dt = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def program_open() -> bool:
    cfg = get_config()
    if not cfg["enabled"]:
        return False
    now = utcnow()
    start = _parse_date(cfg["start_date"])
    end = _parse_date(cfg["end_date"])
    if start and now < start:
        return False
    if end and now > end:
        return False
    return not is_full()


def accept_signups() -> bool:
    return program_open()


def normalize_phone(raw: str | None) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("33") and len(digits) >= 11:
        digits = "0" + digits[2:]
    return digits


def normalize_source(raw: str | None, *, has_ref: bool = False) -> str:
    key = (raw or "").strip().lower()
    if key in SOURCES:
        return key
    if has_ref:
        return "referral"
    return "direct"


def _referral_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    for _ in range(12):
        code = "".join(secrets.choice(alphabet) for _ in range(8))
        if not FoundingParticipant.query.filter_by(referral_code=code).first():
            return code
    return secrets.token_hex(4).upper()


def _record_status(participant: FoundingParticipant, new_status: str, actor: str = "system") -> None:
    if participant.status == new_status:
        return
    event = FoundingStatusEvent(
        participant_id=participant.id,
        old_status=participant.status,
        new_status=new_status,
        actor=actor,
    )
    participant.status = new_status
    db.session.add(event)


def participant_for_tenant(tenant_id) -> FoundingParticipant | None:
    if not tenant_id:
        return None
    return FoundingParticipant.query.filter_by(tenant_id=tenant_id).first()


def _lead_stats(tenant_id) -> tuple[int, int, datetime | None]:
    leads = Lead.query.filter_by(tenant_id=tenant_id).filter(Lead.archived_at.is_(None)).all()
    handled = sum(1 for lead in leads if lead.is_booked)
    last = max((lead.created_at for lead in leads if lead.created_at), default=None)
    return len(leads), handled, last


def activation_progress(participant: FoundingParticipant) -> dict:
    tenant = participant.tenant or db.session.get(Tenant, participant.tenant_id)
    lead_count, handled, last_lead = _lead_stats(participant.tenant_id)
    if last_lead:
        participant.last_usage_at = last_lead

    has_profile = bool(
        tenant
        and (tenant.name or "").strip()
        and (tenant.city or "").strip()
        and (tenant.phone_number or "").strip()
        and (tenant.trade_type or "").strip()
    )
    has_line = bool(tenant and (tenant.ai_phone_number or tenant.phone_number))
    steps = [
        {"key": "account", "label": "Compte créé", "done": True, "cta": None},
        {
            "key": "profile",
            "label": "Fiche entreprise",
            "done": has_profile,
            "cta": "settings" if not has_profile or not (tenant and (tenant.address or "").strip()) else None,
        },
        {
            "key": "phone",
            "label": "Ligne configurée",
            "done": has_line,
            "cta": "settings" if not has_line else None,
        },
        {
            "key": "first_usage",
            "label": "Premier usage",
            "done": lead_count >= 1,
            "cta": "dashboard" if lead_count < 1 else None,
        },
        {
            "key": "first_handled",
            "label": "Première demande traitée",
            "done": handled >= 1,
            "cta": "leads" if handled < 1 else None,
        },
    ]
    done = sum(1 for step in steps if step["done"])
    pct = int(round(100 * done / len(steps))) if steps else 0
    activated = steps[0]["done"] and steps[3]["done"]
    return {
        "steps": steps,
        "done": done,
        "total": len(steps),
        "percent": pct,
        "activated": activated,
        "lead_count": lead_count,
        "handled": handled,
        "needs_address": bool(tenant and not (tenant.address or "").strip()),
        "has_ai_number": bool(tenant and tenant.ai_phone_number),
    }


def refresh_participant(participant: FoundingParticipant, *, actor: str = "system") -> FoundingParticipant:
    tenant = participant.tenant or db.session.get(Tenant, participant.tenant_id)
    if tenant and tenant.is_paid:
        _record_status(participant, "converted", actor)
        db.session.commit()
        return participant
    if participant.status in ("cancelled", "converted"):
        return participant

    progress = activation_progress(participant)
    now = utcnow()
    cfg = get_config()
    ends = participant.ends_at
    if ends and ends.tzinfo is None:
        ends = ends.replace(tzinfo=timezone.utc)

    if ends and now > ends:
        _record_status(participant, "completed" if progress["activated"] else "expired", actor)
        db.session.commit()
        return participant

    if progress["activated"]:
        if participant.status in ("pending", "active", "at_risk"):
            _record_status(participant, "activated", actor)
    else:
        started = participant.started_at
        if started and started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        age_days = (now - started).days if started else 0
        if age_days >= cfg["at_risk_days"] and participant.status != "at_risk":
            _record_status(participant, "at_risk", actor)
        elif participant.status == "pending":
            _record_status(participant, "active", actor)
    db.session.commit()
    return participant


def mark_converted(tenant_id) -> None:
    participant = participant_for_tenant(tenant_id)
    if not participant:
        return
    if participant.status != "converted":
        _record_status(participant, "converted", "system")
        db.session.commit()
        log_event(
            CAT_AUTH,
            "founding_converted",
            summary=f"Participant #{participant.place_number} converti payant",
            tenant_id=tenant_id,
            meta={"participant_id": str(participant.id), "place": participant.place_number},
            level=LEVEL_SUCCESS,
        )


def gift_active_for_tenant(tenant) -> bool:
    """True while this artisan is on the unpaid Starter month from /50-artisans."""
    if not tenant or getattr(tenant, "is_paid", False):
        return False
    cached = getattr(tenant, "_founding_gift_active", None)
    if isinstance(cached, bool):
        return cached
    tid = getattr(tenant, "id", None)
    if tid is None:
        tenant._founding_gift_active = False
        return False
    row = FoundingParticipant.query.filter_by(tenant_id=tid).first()
    active = bool(row) and row.status not in ("cancelled", "converted")
    tenant._founding_gift_active = active
    return active


def _sync_gift_window(participant: FoundingParticipant, tenant: Tenant | None, days: int) -> None:
    """Extend (never shorten) an unpaid seat to the configured Starter month."""
    if participant.status in ("cancelled", "converted", "expired", "completed"):
        return
    started = participant.started_at
    if started and started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    if not started:
        return
    now = utcnow()
    ends = participant.ends_at
    if ends and ends.tzinfo is None:
        ends = ends.replace(tzinfo=timezone.utc)
    if ends and ends < now:
        return
    target = started + timedelta(days=days)
    if not ends or ends < target:
        participant.ends_at = target
    if tenant and not tenant.is_paid:
        current = tenant.trial_ends_at
        if current and current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        if not current or current < target:
            tenant.trial_ends_at = target


def enroll(
    *,
    email: str,
    password: str,
    first_name: str,
    last_name: str,
    phone: str,
    city: str,
    trade_type: str,
    company_name: str | None,
    source: str | None,
    utm: dict | None = None,
    referral_code: str | None = None,
) -> tuple[User, Tenant, FoundingParticipant]:
    if not accept_signups():
        raise AppError("Le programme des 50 premiers artisans est complet.", status_code=409)

    email = (email or "").strip().lower()
    phone_n = normalize_phone(phone)
    if not phone_n or len(phone_n) < 8:
        raise AppError("Téléphone requis", status_code=422)

    if User.query.filter_by(email=email).first():
        raise ConflictError("Email already registered")

    for other in FoundingParticipant.query.all():
        tenant = other.tenant or db.session.get(Tenant, other.tenant_id)
        if tenant and normalize_phone(tenant.phone_number) == phone_n:
            raise ConflictError("Phone already registered")

    referrer = None
    code = (referral_code or "").strip().upper()
    if code:
        referrer = FoundingParticipant.query.filter_by(referral_code=code).first()

    company = (company_name or "").strip()
    if not company:
        company = f"{(first_name or '').strip()} {(last_name or '').strip()}".strip() or f"Artisan {city}".strip()

    from app.services.signup_service import register_plumber

    user, tenant = register_plumber(
        email=email,
        password=password,
        company_name=company,
        phone=phone,
        city=city,
        first_name=first_name or None,
        last_name=last_name or None,
        trade_type=trade_type,
        send_welcome=False,
    )
    cfg = get_config()
    tenant.trial_ends_at = utcnow() + timedelta(days=cfg["duration_days"])
    db.session.commit()

    started = utcnow()
    utm = utm or {}
    last_place = db.session.query(db.func.max(FoundingParticipant.place_number)).scalar() or 0
    participant = FoundingParticipant(
        place_number=last_place + 1,
        tenant_id=tenant.id,
        user_id=user.id,
        status="active",
        source=normalize_source(source, has_ref=bool(referrer)),
        utm_source=(utm.get("utm_source") or "")[:80] or None,
        utm_medium=(utm.get("utm_medium") or "")[:80] or None,
        utm_campaign=(utm.get("utm_campaign") or "")[:120] or None,
        utm_content=(utm.get("utm_content") or "")[:120] or None,
        referral_code=_referral_code(),
        referred_by_id=referrer.id if referrer else None,
        started_at=started,
        ends_at=started + timedelta(days=cfg["duration_days"]),
    )
    db.session.add(participant)
    db.session.add(
        FoundingStatusEvent(
            participant=participant,
            old_status=None,
            new_status="active",
            actor="system",
        )
    )
    db.session.commit()

    try:
        from app.services.transactional_email import send_founding_welcome

        if send_founding_welcome(user, tenant, participant):
            participant.mark_email_sent("welcome")
            db.session.commit()
    except Exception:
        logger.exception("Founding welcome email failed for tenant=%s", tenant.id)

    log_event(
        CAT_AUTH,
        "founding_signup",
        summary=f"Artisan fondateur #{participant.place_number}/{max_participants()}",
        tenant_id=tenant.id,
        meta={
            "participant_id": str(participant.id),
            "place": participant.place_number,
            "source": participant.source,
        },
        level=LEVEL_SUCCESS,
    )
    return user, tenant, participant


def join_waitlist(
    *,
    name: str,
    email: str,
    phone: str | None,
    trade_type: str | None,
    city: str | None,
    source: str | None,
    utm_source: str | None = None,
) -> FoundingWaitlist:
    email = (email or "").strip().lower()
    if not email or not name.strip():
        raise AppError("Nom et e-mail requis", status_code=422)
    existing = FoundingWaitlist.query.filter_by(email=email).first()
    if existing:
        return existing
    if User.query.filter_by(email=email).first():
        raise ConflictError("Email already registered")
    row = FoundingWaitlist(
        name=name.strip()[:200],
        email=email,
        phone=(phone or "").strip()[:50] or None,
        trade_type=(trade_type or "").strip()[:30] or None,
        city=(city or "").strip()[:100] or None,
        source=normalize_source(source),
        utm_source=(utm_source or "")[:80] or None,
    )
    db.session.add(row)
    db.session.commit()
    try:
        from app.services.transactional_email import send_founding_waitlist

        send_founding_waitlist(row)
    except Exception:
        logger.exception("Waitlist email failed for %s", email)
    log_event(
        CAT_AUTH,
        "founding_waitlist",
        summary=f"Liste d'attente : {email}",
        meta={"email": email, "source": row.source},
    )
    return row


def set_status(participant: FoundingParticipant, new_status: str, actor: str) -> FoundingParticipant:
    if new_status not in STATUSES:
        raise AppError("Statut inconnu", status_code=422)
    _record_status(participant, new_status, actor)
    db.session.commit()
    return participant


def save_feedback(participant: FoundingParticipant, text: str, consent: bool) -> None:
    participant.feedback_text = (text or "").strip()[:4000] or None
    participant.testimonial_consent = bool(consent)
    db.session.commit()
    log_event(
        CAT_AUTH,
        "founding_feedback",
        summary=f"Avis participant #{participant.place_number}",
        tenant_id=participant.tenant_id,
        meta={"participant_id": str(participant.id), "consent": bool(consent)},
    )


def decline_continue(participant: FoundingParticipant) -> None:
    if participant.status not in ("converted", "cancelled"):
        _record_status(participant, "completed", "user")
        db.session.commit()


def _send_once(participant: FoundingParticipant, key: str, sender) -> None:
    if participant.has_email(key):
        return
    if sender():
        participant.mark_email_sent(key)
        db.session.commit()


def tick() -> dict:
    """Daily automation: statuses, expiry mails, activation nudges, conversions."""
    cfg = get_config()
    now = utcnow()
    updated = 0
    mailed = 0
    for participant in FoundingParticipant.query.all():
        tenant = participant.tenant or db.session.get(Tenant, participant.tenant_id)
        _sync_gift_window(participant, tenant, cfg["duration_days"])
        before = participant.status
        refresh_participant(participant)
        if participant.status != before:
            updated += 1
        if participant.status in ("cancelled", "converted"):
            continue
        tenant = participant.tenant or db.session.get(Tenant, participant.tenant_id)
        user = participant.user or db.session.get(User, participant.user_id)
        if not user or not tenant:
            continue
        progress = activation_progress(participant)
        started = participant.started_at
        if started and started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        ends = participant.ends_at
        if ends and ends.tzinfo is None:
            ends = ends.replace(tzinfo=timezone.utc)
        age = (now - started) if started else timedelta()
        try:
            from app.services import transactional_email as mail

            if not progress["activated"] and age.days >= cfg["nudge_inactive_days"]:
                if participant.mark_email_sent("nudge_inactive"):
                    mail.send_founding_nudge_inactive(user, tenant, participant)
                    mailed += 1
            elif progress["activated"] and progress["lead_count"] == 0 and age.days >= cfg["nudge_no_usage_days"]:
                if participant.mark_email_sent("nudge_no_usage"):
                    mail.send_founding_nudge_no_usage(user, tenant, participant)
                    mailed += 1
            if progress["lead_count"] >= 1 and participant.mark_email_sent("first_usage"):
                mail.send_founding_ask_feedback(user, tenant, participant)
                mailed += 1
            if progress["lead_count"] >= 2 and participant.mark_email_sent("ask_testimonial"):
                mail.send_founding_ask_testimonial(user, tenant, participant)
                mailed += 1
            if ends and participant.status not in ("expired", "completed", "converted"):
                days_left = (ends.date() - now.date()).days
                for marker, key, fn in (
                    (7, "expiry_7", mail.send_founding_expiry_7),
                    (3, "expiry_3", mail.send_founding_expiry_3),
                    (1, "expiry_1", mail.send_founding_expiry_1),
                    (0, "expiry_0", mail.send_founding_expiry_0),
                ):
                    if days_left == marker and participant.mark_email_sent(key):
                        fn(user, tenant, participant)
                        mailed += 1
            db.session.commit()
        except Exception:
            logger.exception("Founding tick mail failed for %s", participant.id)
            db.session.rollback()
    return {"updated": updated, "mailed": mailed, "occupied": occupied_count(), "full": is_full()}


def funnel() -> list[dict]:
    occupied = occupied_count()
    visitors = (
        db.session.query(PageView.visitor_id)
        .filter(PageView.path == "/50-artisans")
        .filter(PageView.visitor_id.isnot(None))
        .distinct()
        .count()
    )
    activated = 0
    first_usage = 0
    active_users = 0
    converted = 0
    for row in FoundingParticipant.query.all():
        progress = activation_progress(row)
        if progress["activated"] or row.status in ("activated", "converted", "completed"):
            activated += 1
        if progress["lead_count"] >= 1:
            first_usage += 1
        if progress["lead_count"] >= 2:
            active_users += 1
        if row.status == "converted":
            converted += 1
    return [
        {"key": "visitors", "label": "Visiteurs", "value": visitors},
        {"key": "signups", "label": "Inscriptions", "value": occupied},
        {"key": "activated", "label": "Comptes activés", "value": activated},
        {"key": "first_usage", "label": "Premier usage", "value": first_usage},
        {"key": "active_users", "label": "Utilisateurs actifs", "value": active_users},
        {"key": "converted", "label": "Conversions payantes", "value": converted},
    ]


def kpis() -> dict:
    rows = FoundingParticipant.query.all()
    counts = {status: 0 for status in STATUSES}
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
    occupied = len(rows)
    cap = max_participants()
    activated = counts["activated"] + counts["converted"] + counts["completed"]
    converted = counts["converted"]
    return {
        "occupied": occupied,
        "max": cap,
        "remaining": max(0, cap - occupied),
        "active": counts["active"] + counts["pending"],
        "activated": counts["activated"],
        "at_risk": counts["at_risk"],
        "expired": counts["expired"],
        "converted": converted,
        "cancelled": counts["cancelled"],
        "completed": counts["completed"],
        "activation_rate": round(100 * activated / occupied, 1) if occupied else 0,
        "conversion_rate": round(100 * converted / occupied, 1) if occupied else 0,
        "waitlist": FoundingWaitlist.query.count(),
        "full": occupied >= cap,
        "open": accept_signups(),
        "duration_days": get_config()["duration_days"],
    }


def sources_breakdown() -> list[dict]:
    buckets: dict[str, dict] = {}
    for row in FoundingParticipant.query.all():
        key = row.source or "direct"
        bucket = buckets.setdefault(key, {"source": key, "inscrits": 0, "actives": 0, "payants": 0})
        bucket["inscrits"] += 1
        progress = activation_progress(row)
        if progress["activated"] or row.status in ("activated", "converted", "completed"):
            bucket["actives"] += 1
        if row.status == "converted":
            bucket["payants"] += 1
    return sorted(buckets.values(), key=lambda item: item["inscrits"], reverse=True)


def referral_stats() -> list[dict]:
    out = []
    for row in FoundingParticipant.query.filter(FoundingParticipant.referred_by_id.isnot(None)).all():
        out.append(row)
    by_ref: dict[str, dict] = {}
    for row in FoundingParticipant.query.all():
        kids = FoundingParticipant.query.filter_by(referred_by_id=row.id).all()
        if not kids:
            continue
        activated = sum(1 for kid in kids if activation_progress(kid)["activated"] or kid.status in ("activated", "converted"))
        converted = sum(1 for kid in kids if kid.status == "converted")
        by_ref[str(row.id)] = {
            "place": row.place_number,
            "name": (row.tenant.name if row.tenant else "") or "",
            "code": row.referral_code,
            "invites": len(kids),
            "activated": activated,
            "converted": converted,
        }
    return list(by_ref.values())


def alerts() -> list[dict]:
    cfg = get_config()
    now = utcnow()
    items = []
    occupied = occupied_count()
    cap = max_participants()
    if occupied >= cap:
        items.append({"level": "info", "text": f"Programme complet ({occupied}/{cap})."})
    elif occupied >= int(cap * 0.9):
        items.append({"level": "warn", "text": f"Programme proche de la limite : {occupied}/{cap}."})
    day_ago = now - timedelta(days=1)
    recent = FoundingParticipant.query.filter(FoundingParticipant.created_at >= day_ago).count()
    if recent:
        items.append({"level": "info", "text": f"{recent} inscription(s) dans les dernières 24 h."})
    at_risk = FoundingParticipant.query.filter_by(status="at_risk").count()
    if at_risk:
        items.append({"level": "warn", "text": f"{at_risk} artisan(s) à risque (non activés)."})
    soon = now + timedelta(days=3)
    ending = FoundingParticipant.query.filter(
        FoundingParticipant.ends_at <= soon,
        FoundingParticipant.ends_at >= now,
        FoundingParticipant.status.notin_(("converted", "cancelled", "expired", "completed")),
    ).count()
    if ending:
        items.append({"level": "warn", "text": f"{ending} artisan(s) arrivent en fin de programme (≤ 3 jours)."})
    converted_today = FoundingParticipant.query.filter(
        FoundingParticipant.status == "converted",
        FoundingParticipant.created_at >= day_ago,
    ).count()
    # Conversion can be older accounts — count status events instead if present.
    conv_events = FoundingStatusEvent.query.filter(
        FoundingStatusEvent.new_status == "converted",
        FoundingStatusEvent.created_at >= day_ago,
    ).count()
    if conv_events:
        items.append({"level": "success", "text": f"{conv_events} conversion(s) payante(s) (24 h)."})
    elif converted_today:
        items.append({"level": "success", "text": f"{converted_today} nouveau(x) inscrit(s) déjà payant(s)."})
    _ = cfg
    return items


def landing_context() -> dict:
    cfg = get_config()
    occupied = occupied_count()
    cap = cfg["max_participants"]
    open_ = accept_signups()
    return {
        "occupied": occupied,
        "max": cap,
        "remaining": max(0, cap - occupied),
        "full": not open_,
        "waitlist_enabled": cfg["waitlist_enabled"] and not open_,
        "duration_days": cfg["duration_days"],
        "enabled": cfg["enabled"],
        "open": open_,
        "gift_plan": STARTER_GIFT_PLAN,
    }
