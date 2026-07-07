"""GA4-style traffic analytics computed from the PageView table: real-time
active visitors, unique visitors, page views, sessions, bounce rate, plus time
series, top pages and top referrers."""
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

from sqlalchemy import func

from app.core.extensions import db
from app.models.page_view import PageView

REALTIME_WINDOW_MIN = 5


def _utcnow():
    return datetime.now(timezone.utc)


def _since(days):
    return _utcnow() - timedelta(days=days)


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

    def _trend(cur, prev):
        if not prev:
            return 100.0 if cur else 0.0
        return round(((cur - prev) / prev) * 100, 1)

    return {
        "pageviews": views,
        "pageviews_trend": _trend(views, views_prev),
        "unique_visitors": uniques,
        "visitors_trend": _trend(uniques, uniques_prev),
        "sessions": sessions,
        "bounce_rate": bounce_rate,
        "pages_per_session": pages_per_session,
    }


def timeseries(days=30):
    # Buckets span the last `days` days *including today* (so the most recent
    # day is never dropped).
    today = _utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    since = today - timedelta(days=days - 1)
    views_b = OrderedDict()
    for i in range(days):
        day = (since + timedelta(days=i)).date().isoformat()
        views_b[day] = {"date": day, "views": 0, "visitors": 0}

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
    for day, c in view_rows:
        key = day.isoformat() if hasattr(day, "isoformat") else str(day)
        if key in views_b:
            views_b[key]["views"] = c
    for day, c in visitor_rows:
        key = day.isoformat() if hasattr(day, "isoformat") else str(day)
        if key in views_b:
            views_b[key]["visitors"] = c
    return list(views_b.values())


def top_pages(days=30, limit=10):
    since = _since(days)
    rows = (
        db.session.query(PageView.path, func.count(PageView.id))
        .filter(PageView.created_at >= since)
        .group_by(PageView.path)
        .order_by(func.count(PageView.id).desc())
        .limit(limit)
        .all()
    )
    return [{"path": p or "/", "views": c} for p, c in rows]


def top_referrers(days=30, limit=8):
    since = _since(days)
    rows = (
        db.session.query(PageView.referrer_host, func.count(PageView.id))
        .filter(PageView.created_at >= since, PageView.referrer_host.isnot(None))
        .group_by(PageView.referrer_host)
        .order_by(func.count(PageView.id).desc())
        .limit(limit)
        .all()
    )
    result = [{"host": h, "views": c} for h, c in rows]
    direct = (
        PageView.query.filter(
            PageView.created_at >= since, PageView.referrer_host.is_(None)
        ).count()
    )
    if direct:
        result.append({"host": "(direct)", "views": direct})
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
    return [
        {
            "label": " / ".join(p for p in (src, med, camp) if p),
            "utm_source": src,
            "utm_medium": med,
            "utm_campaign": camp,
            "views": views,
            "visitors": visitors,
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
