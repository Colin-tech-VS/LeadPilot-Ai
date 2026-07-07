"""GA4-style traffic analytics computed from the PageView table: real-time
active visitors, unique visitors, page views, sessions, bounce rate, signup
conversion rates, acquisition funnel, channel grouping, plus time series."""
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

from sqlalchemy import func

from app.core.extensions import db
from app.models.page_view import PageView
from app.models.user import User

REALTIME_WINDOW_MIN = 5
REGISTER_PATHS = ("/register", "/client/register")
ARTISAN_REGISTER_PATHS = ("/register",)
CUSTOMER_REGISTER_PATHS = ("/client/register",)

SOCIAL_SOURCES = frozenset(
    {"facebook", "fb", "instagram", "linkedin", "twitter", "x", "tiktok", "youtube"}
)
SEARCH_HOSTS = ("google.", "bing.", "duckduckgo.", "yahoo.", "ecosia.", "qwant.")


def _utcnow():
    return datetime.now(timezone.utc)


def _since(days):
    return _utcnow() - timedelta(days=days)


def _trend(cur, prev):
    if not prev:
        return 100.0 if cur else 0.0
    return round(((cur - prev) / prev) * 100, 1)


def _rate(num, denom):
    return round((num / denom) * 100, 2) if denom else 0.0


def _period_bounds(days):
    since = _since(days)
    prev_since = _since(days * 2)
    return since, prev_since


def _unique_visitors(since, until=None):
    q = db.session.query(func.count(func.distinct(PageView.visitor_id))).filter(
        PageView.created_at >= since
    )
    if until is not None:
        q = q.filter(PageView.created_at < until)
    return q.scalar() or 0


def _unique_on_paths(since, paths, until=None):
    q = (
        db.session.query(func.count(func.distinct(PageView.visitor_id)))
        .filter(PageView.created_at >= since, PageView.path.in_(paths))
    )
    if until is not None:
        q = q.filter(PageView.created_at < until)
    return q.scalar() or 0


def _count_signups(since, until=None, role=None):
    q = User.query.filter(User.created_at >= since)
    if until is not None:
        q = q.filter(User.created_at < until)
    if role == "customer":
        q = q.filter(User.role == "customer")
    elif role == "artisan":
        q = q.filter(User.role != "customer")
    return q.count()


def _classify_channel(referrer_host, utm_source, utm_medium):
    src = (utm_source or "").lower()
    med = (utm_medium or "").lower()
    host = (referrer_host or "").lower()

    if med in ("cpc", "ppc", "paid", "paid_social") or med.startswith("paid"):
        return "Paid"
    if med == "email" or src in ("newsletter", "mailchimp", "sendgrid"):
        return "Email"
    if med in ("social", "social-paid") or src in SOCIAL_SOURCES:
        return "Social"
    if utm_source:
        return "Campaign"
    if not host:
        return "Direct"
    if any(token in host for token in SEARCH_HOSTS):
        return "Organic Search"
    return "Referral"


def realtime():
    """Active visitors in the last few minutes + a per-minute sparkline of the
    last 30 minutes and the pages they're on right now."""
    now = _utcnow()
    window = now - timedelta(minutes=REALTIME_WINDOW_MIN)
    active = (
        db.session.query(func.count(func.distinct(PageView.visitor_id)))
        .filter(PageView.created_at >= window)
        .scalar()
    ) or 0

    # last 30 minutes, per-minute page-view counts
    start = now - timedelta(minutes=30)
    buckets = OrderedDict()
    for i in range(30):
        key = (start + timedelta(minutes=i)).strftime("%H:%M")
        buckets[key] = 0
    rows = (
        PageView.query.with_entities(PageView.created_at)
        .filter(PageView.created_at >= start)
        .all()
    )
    for (ts,) in rows:
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        key = ts.strftime("%H:%M")
        if key in buckets:
            buckets[key] += 1

    active_pages = (
        db.session.query(PageView.path, func.count(PageView.id))
        .filter(PageView.created_at >= window)
        .group_by(PageView.path)
        .order_by(func.count(PageView.id).desc())
        .limit(8)
        .all()
    )
    return {
        "active_visitors": active,
        "sparkline": [{"t": k, "v": v} for k, v in buckets.items()],
        "active_pages": [{"path": p, "views": c} for p, c in active_pages],
    }


def _bounce_and_sessions(since):
    """Sessions and bounce rate over the period. A bounce is a session with a
    single page view."""
    per_session = (
        db.session.query(PageView.session_id, func.count(PageView.id).label("n"))
        .filter(PageView.created_at >= since, PageView.session_id.isnot(None))
        .group_by(PageView.session_id)
        .all()
    )
    sessions = len(per_session)
    bounces = sum(1 for _, n in per_session if n == 1)
    views = sum(n for _, n in per_session)
    bounce_rate = round((bounces / sessions) * 100, 1) if sessions else 0.0
    pages_per_session = round(views / sessions, 2) if sessions else 0.0
    return sessions, bounce_rate, pages_per_session


def kpis(days=30):
    since = _since(days)
    prev_since = _since(days * 2)

    def _views(a, b=None):
        q = PageView.query.filter(PageView.created_at >= a)
        if b is not None:
            q = q.filter(PageView.created_at < b)
        return q.count()

    def _uniques(a, b=None):
        q = db.session.query(func.count(func.distinct(PageView.visitor_id))).filter(
            PageView.created_at >= a
        )
        if b is not None:
            q = q.filter(PageView.created_at < b)
        return q.scalar() or 0

    views = _views(since)
    views_prev = _views(prev_since, since)
    uniques = _uniques(since)
    uniques_prev = _uniques(prev_since, since)
    sessions, bounce_rate, pages_per_session = _bounce_and_sessions(since)

    new_sessions = (
        db.session.query(func.count(func.distinct(PageView.session_id)))
        .filter(
            PageView.created_at >= since,
            PageView.is_new_session.is_(True),
            PageView.session_id.isnot(None),
        )
        .scalar()
    ) or 0

    return {
        "pageviews": views,
        "pageviews_trend": _trend(views, views_prev),
        "unique_visitors": uniques,
        "visitors_trend": _trend(uniques, uniques_prev),
        "sessions": sessions,
        "new_sessions": new_sessions,
        "bounce_rate": bounce_rate,
        "pages_per_session": pages_per_session,
    }


def conversions(days=30):
    """Signup counts and conversion rates vs unique visitors."""
    since, prev_since = _period_bounds(days)
    uniques = _unique_visitors(since)
    uniques_prev = _unique_visitors(prev_since, since)

    artisan = _count_signups(since, role="artisan")
    customer = _count_signups(since, role="customer")
    total = artisan + customer

    artisan_prev = _count_signups(prev_since, since, role="artisan")
    customer_prev = _count_signups(prev_since, since, role="customer")
    total_prev = artisan_prev + customer_prev

    register_visitors = _unique_on_paths(since, REGISTER_PATHS)
    register_artisan = _unique_on_paths(since, ARTISAN_REGISTER_PATHS)
    register_customer = _unique_on_paths(since, CUSTOMER_REGISTER_PATHS)

    return {
        "signups_total": total,
        "signups_artisan": artisan,
        "signups_customer": customer,
        "signups_trend": _trend(total, total_prev),
        "signups_artisan_trend": _trend(artisan, artisan_prev),
        "signups_customer_trend": _trend(customer, customer_prev),
        "register_visitors": register_visitors,
        "register_artisan_visitors": register_artisan,
        "register_customer_visitors": register_customer,
        "visitor_to_signup_rate": _rate(total, uniques),
        "visitor_to_artisan_rate": _rate(artisan, uniques),
        "visitor_to_customer_rate": _rate(customer, uniques),
        "register_to_signup_rate": _rate(total, register_visitors),
        "register_to_artisan_rate": _rate(artisan, register_artisan),
        "register_to_customer_rate": _rate(customer, register_customer),
        "visitors_per_signup": round(uniques / total, 1) if total else None,
        "unique_visitors": uniques,
        "visitors_trend": _trend(uniques, uniques_prev),
    }


def acquisition_funnel(days=30):
    """Visitor → register page → signup funnel (GA4-style)."""
    since = _since(days)
    uniques = _unique_visitors(since)
    register_visitors = _unique_on_paths(since, REGISTER_PATHS)
    signups = _count_signups(since)

    base = max(uniques, 1)
    return [
        {
            "label": "Visiteurs uniques",
            "count": uniques,
            "pct": 100.0,
            "step_rate": None,
        },
        {
            "label": "Page inscription visitée",
            "count": register_visitors,
            "pct": round((register_visitors / base) * 100, 1),
            "step_rate": _rate(register_visitors, uniques),
        },
        {
            "label": "Inscriptions confirmées",
            "count": signups,
            "pct": round((signups / base) * 100, 1),
            "step_rate": _rate(signups, register_visitors),
        },
    ]


def channel_breakdown(days=30, limit=8):
    """Group traffic into GA4-like acquisition channels."""
    since = _since(days)
    rows = (
        PageView.query.with_entities(
            PageView.referrer_host,
            PageView.utm_source,
            PageView.utm_medium,
            PageView.visitor_id,
            PageView.path,
        )
        .filter(PageView.created_at >= since)
        .all()
    )
    buckets = OrderedDict()
    register_visitors_by_channel = {}

    for host, src, med, vid, path in rows:
        channel = _classify_channel(host, src, med)
        if channel not in buckets:
            buckets[channel] = {"views": 0, "visitors": set()}
        buckets[channel]["views"] += 1
        if vid:
            buckets[channel]["visitors"].add(vid)
            if path in REGISTER_PATHS:
                register_visitors_by_channel.setdefault(channel, set()).add(vid)

    order = ["Direct", "Organic Search", "Social", "Paid", "Email", "Campaign", "Referral"]
    result = []
    for channel in order:
        if channel not in buckets:
            continue
        data = buckets[channel]
        visitors = len(data["visitors"])
        reg_vis = len(register_visitors_by_channel.get(channel, set()))
        result.append(
            {
                "label": channel,
                "views": data["views"],
                "visitors": visitors,
                "register_visitors": reg_vis,
                "register_rate": _rate(reg_vis, visitors),
            }
        )
    result.sort(key=lambda r: r["visitors"], reverse=True)
    return result[:limit]


def timeseries(days=30):
    # Buckets span the last `days` days *including today* (so the most recent
    # day is never dropped).
    today = _utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    since = today - timedelta(days=days - 1)
    views_b = OrderedDict()
    for i in range(days):
        day = (since + timedelta(days=i)).date().isoformat()
        views_b[day] = {"date": day, "views": 0, "visitors": 0, "signups": 0}

    view_rows = (
        db.session.query(func.date(PageView.created_at), func.count(PageView.id))
        .filter(PageView.created_at >= since)
        .group_by(func.date(PageView.created_at))
        .all()
    )
    visitor_rows = (
        db.session.query(
            func.date(PageView.created_at),
            func.count(func.distinct(PageView.visitor_id)),
        )
        .filter(PageView.created_at >= since)
        .group_by(func.date(PageView.created_at))
        .all()
    )
    signup_rows = (
        db.session.query(func.date(User.created_at), func.count(User.id))
        .filter(User.created_at >= since)
        .group_by(func.date(User.created_at))
        .all()
    )
    for day, c in view_rows:
        key = day.isoformat() if hasattr(day, "isoformat") else str(day)
        if key in views_b:
            views_b[key]["views"] = c
    for day, c in visitor_rows:
        key = day.isoformat() if hasattr(day, "isoformat") else str(day)
        if key in views_b:
            views_b[key]["visitors"] = c
    for day, c in signup_rows:
        key = day.isoformat() if hasattr(day, "isoformat") else str(day)
        if key in views_b:
            views_b[key]["signups"] = c
    return list(views_b.values())


def top_pages(days=30, limit=10):
    since = _since(days)
    rows = (
        db.session.query(
            PageView.path,
            func.count(PageView.id),
            func.count(func.distinct(PageView.visitor_id)),
        )
        .filter(PageView.created_at >= since)
        .group_by(PageView.path)
        .order_by(func.count(PageView.id).desc())
        .limit(limit)
        .all()
    )
    return [{"path": p or "/", "views": views, "visitors": visitors} for p, views, visitors in rows]


def top_referrers(days=30, limit=8):
    since = _since(days)
    rows = (
        db.session.query(
            PageView.referrer_host,
            func.count(PageView.id),
            func.count(func.distinct(PageView.visitor_id)),
        )
        .filter(PageView.created_at >= since, PageView.referrer_host.isnot(None))
        .group_by(PageView.referrer_host)
        .order_by(func.count(PageView.id).desc())
        .limit(limit)
        .all()
    )
    result = [
        {"host": h, "views": views, "visitors": visitors} for h, views, visitors in rows
    ]
    direct_views = PageView.query.filter(
        PageView.created_at >= since, PageView.referrer_host.is_(None)
    ).count()
    direct_visitors = (
        db.session.query(func.count(func.distinct(PageView.visitor_id)))
        .filter(PageView.created_at >= since, PageView.referrer_host.is_(None))
        .scalar()
    ) or 0
    if direct_views:
        result.append({"host": "(direct)", "views": direct_views, "visitors": direct_visitors})
    return sorted(result, key=lambda r: r["views"], reverse=True)[:limit]


def device_breakdown(days=30):
    since = _since(days)
    rows = (
        db.session.query(PageView.device, func.count(PageView.id))
        .filter(PageView.created_at >= since)
        .group_by(PageView.device)
        .all()
    )
    return [{"label": d or "inconnu", "count": c} for d, c in rows]


def _location_label(city, postal, region, country_code):
    from app.services.geoip import format_location

    return format_location(
        {
            "city": city,
            "postal_code": postal,
            "region": region,
            "country_code": country_code,
        }
    )


def top_locations(days=30, limit=12):
    """Most precise visitor locations (city + postal when available)."""
    since = _since(days)
    rows = (
        db.session.query(
            PageView.geo_city,
            PageView.geo_postal_code,
            PageView.geo_region,
            PageView.geo_country_code,
            func.count(PageView.id),
            func.count(func.distinct(PageView.visitor_id)),
        )
        .filter(PageView.created_at >= since, PageView.geo_city.isnot(None))
        .group_by(
            PageView.geo_city,
            PageView.geo_postal_code,
            PageView.geo_region,
            PageView.geo_country_code,
        )
        .order_by(func.count(func.distinct(PageView.visitor_id)).desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "label": _location_label(city, postal, region, cc),
            "city": city,
            "postal_code": postal,
            "region": region,
            "country_code": cc,
            "views": views,
            "visitors": visitors,
        }
        for city, postal, region, cc, views, visitors in rows
    ]


def top_countries(days=30, limit=8):
    since = _since(days)
    rows = (
        db.session.query(
            PageView.geo_country_code,
            PageView.geo_country,
            func.count(func.distinct(PageView.visitor_id)),
            func.count(PageView.id),
        )
        .filter(PageView.created_at >= since, PageView.geo_country_code.isnot(None))
        .group_by(PageView.geo_country_code, PageView.geo_country)
        .order_by(func.count(func.distinct(PageView.visitor_id)).desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "label": (name or code or "?"),
            "country_code": code,
            "visitors": visitors,
            "views": views,
        }
        for code, name, visitors, views in rows
    ]


def utm_breakdown(days=30, limit=10):
    since = _since(days)
    rows = (
        db.session.query(
            PageView.utm_source,
            PageView.utm_medium,
            PageView.utm_campaign,
            func.count(PageView.id),
            func.count(func.distinct(PageView.visitor_id)),
        )
        .filter(PageView.created_at >= since, PageView.utm_source.isnot(None))
        .group_by(PageView.utm_source, PageView.utm_medium, PageView.utm_campaign)
        .order_by(func.count(PageView.id).desc())
        .limit(limit)
        .all()
    )
    reg_rows = (
        db.session.query(
            PageView.utm_source,
            PageView.utm_medium,
            PageView.utm_campaign,
            func.count(func.distinct(PageView.visitor_id)),
        )
        .filter(
            PageView.created_at >= since,
            PageView.utm_source.isnot(None),
            PageView.path.in_(REGISTER_PATHS),
        )
        .group_by(PageView.utm_source, PageView.utm_medium, PageView.utm_campaign)
        .all()
    )
    reg_map = {(src, med, camp): n for src, med, camp, n in reg_rows}
    return [
        {
            "label": " / ".join(p for p in (src, med, camp) if p),
            "utm_source": src,
            "utm_medium": med,
            "utm_campaign": camp,
            "views": views,
            "visitors": visitors,
            "register_visitors": reg_map.get((src, med, camp), 0),
            "register_rate": _rate(reg_map.get((src, med, camp), 0), visitors),
        }
        for src, med, camp, views, visitors in rows
    ]


def geo_map_points(days=30, limit=40):
    """Approximate map pins (city centroids from IP)."""
    since = _since(days)
    rows = (
        db.session.query(
            PageView.geo_city,
            PageView.geo_country_code,
            PageView.geo_latitude,
            PageView.geo_longitude,
            func.count(func.distinct(PageView.visitor_id)),
        )
        .filter(
            PageView.created_at >= since,
            PageView.geo_latitude.isnot(None),
            PageView.geo_longitude.isnot(None),
        )
        .group_by(
            PageView.geo_city,
            PageView.geo_country_code,
            PageView.geo_latitude,
            PageView.geo_longitude,
        )
        .order_by(func.count(func.distinct(PageView.visitor_id)).desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "city": city,
            "country_code": cc,
            "lat": lat,
            "lng": lng,
            "visitors": visitors,
        }
        for city, cc, lat, lng, visitors in rows
    ]


def payload(days=30):
    return {
        "realtime": realtime(),
        "kpis": kpis(days),
        "conversions": conversions(days),
        "funnel": acquisition_funnel(days),
        "channels": channel_breakdown(days),
        "timeseries": timeseries(days),
        "top_pages": top_pages(days),
        "top_referrers": top_referrers(days),
        "devices": device_breakdown(days),
        "top_locations": top_locations(days),
        "top_countries": top_countries(days),
        "utm_campaigns": utm_breakdown(days),
        "geo_map": geo_map_points(days),
        "range_days": days,
        "generated_at": _utcnow().isoformat(),
    }
