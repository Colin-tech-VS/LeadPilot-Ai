"""Central event log. Any part of the app can record a meaningful event that
then shows up in the admin log and feeds the analytics funnels.

Logging must never break the calling flow — every failure is swallowed and the
session rolled back, so a broken log write can't take down a booking.
"""
import json
import logging

from flask import g, has_request_context, request

from app.core.extensions import db
from app.models.event import (  # noqa: F401 (re-exported for callers)
    CAT_ADMIN,
    CAT_APPOINTMENT,
    CAT_AUTH,
    CAT_BILLING,
    CAT_EMAIL,
    CAT_LEAD,
    CAT_QUOTE,
    CAT_SYSTEM,
    LEVEL_ERROR,
    LEVEL_INFO,
    LEVEL_SUCCESS,
    LEVEL_WARNING,
    Event,
)

logger = logging.getLogger(__name__)


def _client_ip():
    if not has_request_context():
        return None
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote_addr


def log_event(
    category,
    action,
    summary=None,
    level=LEVEL_INFO,
    actor=None,
    tenant_id=None,
    meta=None,
):
    """Persist an event. Best-effort: never raises."""
    try:
        if actor is None and has_request_context():
            actor = getattr(g, "admin_username", None) or (
                str(getattr(g, "current_user").email)
                if getattr(g, "current_user", None)
                else None
            )
        if tenant_id is None and has_request_context():
            tenant_id = getattr(g, "tenant_id", None)

        event = Event(
            category=category,
            action=action,
            level=level,
            actor=actor or "system",
            tenant_id=tenant_id,
            summary=(summary or "")[:500],
            meta=json.dumps(meta) if meta else None,
            ip=_client_ip(),
        )
        db.session.add(event)
        db.session.commit()
        return event
    except Exception:  # pragma: no cover - logging must never break a request
        logger.exception("Failed to record event %s/%s", category, action)
        try:
            db.session.rollback()
        except Exception:
            pass
        return None


def recent_events(limit=100, category=None, level=None):
    query = Event.query
    if category:
        query = query.filter(Event.category == category)
    if level:
        query = query.filter(Event.level == level)
    return query.order_by(Event.created_at.desc()).limit(limit).all()
