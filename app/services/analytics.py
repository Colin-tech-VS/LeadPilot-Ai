"""Analytics aggregation for the admin console — GA4-style KPIs, time series
and a conversion funnel, all computed from the operational tables so nothing
extra needs to be tracked for the numbers to be real."""
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

from sqlalchemy import func

from app.core.extensions import db
from app.models.appointment import Appointment
from app.models.email_message import EmailMessage
from app.models.lead import Lead
from app.models.quote import Quote
from app.models.tenant import Tenant
from app.models.user import User


def _utcnow():
    return datetime.now(timezone.utc)


def _since(days):
    return _utcnow() - timedelta(days=days)


def _rate(part, whole):
    return round((part / whole) * 100, 1) if whole else 0.0


def kpis(days=30):
    """Headline numbers for the top of the dashboard."""
    since = _since(days)
    prev_since = _since(days * 2)

    leads = Lead.query.count()
    leads_period = Lead.query.filter(Lead.created_at >= since).count()
    leads_prev = Lead.query.filter(
        Lead.created_at >= prev_since, Lead.created_at < since
    ).count()

    booked = Lead.query.filter(Lead.status == "booked").count()
    appointments = Appointment.query.count()
    quotes_sent = Quote.query.filter(Quote.status.in_(["sent", "accepted", "paid"])).count()
    quotes_accepted = Quote.query.filter(Quote.status.in_(["accepted", "paid"])).count()
    quotes_paid = Quote.query.filter(Quote.status == "paid").count()

    tenants = Tenant.query.count()
    paid_tenants = Tenant.query.filter(Tenant.plan != "trial").count()

    return {
        "leads_total": leads,
        "leads_period": leads_period,
        "leads_trend": _rate(leads_period - leads_prev, leads_prev) if leads_prev else (100.0 if leads_period else 0.0),
        "conversion_rate": _rate(booked, leads),
        "booked_total": booked,
        "appointments_total": appointments,
        "quotes_sent": quotes_sent,
        "quotes_accepted": quotes_accepted,
        "quote_acceptance_rate": _rate(quotes_accepted, quotes_sent),
        "quotes_paid": quotes_paid,
        "tenants_total": tenants,
        "paid_tenants": paid_tenants,
        "users_total": User.query.count(),
        "emails_sent": EmailMessage.query.filter(EmailMessage.direction == "outbound").count(),
        "emails_received": EmailMessage.query.filter(EmailMessage.direction == "inbound").count(),
        "revenue_estimate_cents": _revenue_estimate_cents(),
    }


def _revenue_estimate_cents():
    """Rough MRR estimate from paid plans."""
    from app.services.billing import PLANS

    total = 0
    rows = (
        db.session.query(Tenant.plan, func.count(Tenant.id))
        .filter(Tenant.plan != "trial")
        .group_by(Tenant.plan)
        .all()
    )
    for plan, count in rows:
        plan_conf = PLANS.get(plan)
        if plan_conf:
            total += plan_conf["amount"] * count
    return total


def leads_timeseries(days=30):
    """Daily lead counts for the main chart (zero-filled)."""
    since = _since(days).replace(hour=0, minute=0, second=0, microsecond=0)
    buckets = OrderedDict()
    for i in range(days):
        day = (since + timedelta(days=i)).date()
        buckets[day.isoformat()] = 0

    rows = (
        db.session.query(func.date(Lead.created_at), func.count(Lead.id))
        .filter(Lead.created_at >= since)
        .group_by(func.date(Lead.created_at))
        .all()
    )
    for day, count in rows:
        key = day.isoformat() if hasattr(day, "isoformat") else str(day)
        if key in buckets:
            buckets[key] = count
    return [{"date": k, "count": v} for k, v in buckets.items()]


def funnel():
    """Conversion funnel from first contact to paid."""
    leads = Lead.query.count()
    booked = Lead.query.filter(Lead.status == "booked").count()
    quotes_sent = Quote.query.filter(Quote.status.in_(["sent", "accepted", "paid"])).count()
    quotes_accepted = Quote.query.filter(Quote.status.in_(["accepted", "paid"])).count()
    quotes_paid = Quote.query.filter(Quote.status == "paid").count()

    steps = [
        ("Prospects", leads),
        ("RDV pris", booked),
        ("Devis envoyés", quotes_sent),
        ("Devis acceptés", quotes_accepted),
        ("Payés", quotes_paid),
    ]
    top = leads or 1
    return [
        {"label": label, "count": count, "pct": _rate(count, top)}
        for label, count in steps
    ]


def urgency_breakdown():
    rows = (
        db.session.query(Lead.urgency_level, func.count(Lead.id))
        .group_by(Lead.urgency_level)
        .all()
    )
    return [{"label": (u or "inconnu"), "count": c} for u, c in rows]


def plan_breakdown():
    rows = (
        db.session.query(Tenant.plan, func.count(Tenant.id))
        .group_by(Tenant.plan)
        .all()
    )
    return [{"label": (p or "trial"), "count": c} for p, c in rows]


def dashboard_payload(days=30):
    return {
        "kpis": kpis(days),
        "leads_timeseries": leads_timeseries(days),
        "funnel": funnel(),
        "urgency": urgency_breakdown(),
        "plans": plan_breakdown(),
        "generated_at": _utcnow().isoformat(),
        "range_days": days,
    }
