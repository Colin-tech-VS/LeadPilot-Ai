"""Heatmap & visitor-journey analytics computed from the HeatmapEvent table.

Two views feed the admin console:

* **Journeys** — every unique visitor is followed as ONE continuous timeline
  (all their clicks / page views / scrolls, across every session), so we track
  the whole path of each visitor rather than one heatmap per session.
* **Heatmap** — aggregated click coordinates per page plus the most-clicked
  elements, to see where people actually click.
"""
from collections import OrderedDict, defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import func

from app.core.extensions import db
from app.models.heatmap_event import (
    TYPE_CLICK,
    TYPE_PAGEVIEW,
    TYPE_RAGECLICK,
    TYPE_SCROLL,
    HeatmapEvent,
)

# Cap how much we ever pull into memory so the admin page stays responsive
# regardless of traffic volume.
MAX_POINTS = 4000
MAX_JOURNEYS = 200
MAX_JOURNEY_EVENTS = 800
CLICK_TYPES = (TYPE_CLICK, TYPE_RAGECLICK)


def _utcnow():
    return datetime.now(timezone.utc)


def _since(days):
    return _utcnow() - timedelta(days=max(1, days))


def _base(days):
    return HeatmapEvent.query.filter(HeatmapEvent.created_at >= _since(days))


# --------------------------------------------------------------------------- #
# Overview / KPIs                                                              #
# --------------------------------------------------------------------------- #
def overview(days=30):
    since = _since(days)
    rows = (
        db.session.query(HeatmapEvent.event_type, func.count(HeatmapEvent.id))
        .filter(HeatmapEvent.created_at >= since)
        .group_by(HeatmapEvent.event_type)
        .all()
    )
    counts = {t: n for t, n in rows}

    tracked_visitors = (
        db.session.query(func.count(func.distinct(HeatmapEvent.visitor_id)))
        .filter(HeatmapEvent.created_at >= since)
        .scalar()
        or 0
    )

    # Hottest pages by number of clicks.
    page_rows = (
        db.session.query(
            HeatmapEvent.path,
            func.count(HeatmapEvent.id),
            func.count(func.distinct(HeatmapEvent.visitor_id)),
        )
        .filter(
            HeatmapEvent.created_at >= since,
            HeatmapEvent.event_type.in_(CLICK_TYPES),
            HeatmapEvent.path.isnot(None),
        )
        .group_by(HeatmapEvent.path)
        .order_by(func.count(HeatmapEvent.id).desc())
        .limit(30)
        .all()
    )
    top_pages = [
        {"path": p, "clicks": c, "visitors": v} for p, c, v in page_rows
    ]

    return {
        "total_events": sum(counts.values()),
        "clicks": counts.get(TYPE_CLICK, 0) + counts.get(TYPE_RAGECLICK, 0),
        "rage_clicks": counts.get(TYPE_RAGECLICK, 0),
        "pageviews": counts.get(TYPE_PAGEVIEW, 0),
        "scrolls": counts.get(TYPE_SCROLL, 0),
        "tracked_visitors": tracked_visitors,
        "top_pages": top_pages,
        "top_elements": top_elements(days),
        "pages": available_pages(days),
    }


def available_pages(days=30):
    """Distinct paths that have click data, most-clicked first — feeds the
    heatmap page selector."""
    rows = (
        db.session.query(HeatmapEvent.path, func.count(HeatmapEvent.id))
        .filter(
            HeatmapEvent.created_at >= _since(days),
            HeatmapEvent.event_type.in_(CLICK_TYPES),
            HeatmapEvent.path.isnot(None),
        )
        .group_by(HeatmapEvent.path)
        .order_by(func.count(HeatmapEvent.id).desc())
        .limit(60)
        .all()
    )
    return [{"path": p, "clicks": c} for p, c in rows]


def top_elements(days=30, path=None, limit=20):
    """Most-clicked elements (by selector), optionally scoped to one page."""
    q = db.session.query(
        HeatmapEvent.el_selector,
        func.max(HeatmapEvent.el_text),
        func.count(HeatmapEvent.id),
    ).filter(
        HeatmapEvent.created_at >= _since(days),
        HeatmapEvent.event_type.in_(CLICK_TYPES),
        HeatmapEvent.el_selector.isnot(None),
    )
    if path:
        q = q.filter(HeatmapEvent.path == path)
    rows = (
        q.group_by(HeatmapEvent.el_selector)
        .order_by(func.count(HeatmapEvent.id).desc())
        .limit(limit)
        .all()
    )
    return [
        {"selector": sel, "text": txt, "clicks": n} for sel, txt, n in rows
    ]


# --------------------------------------------------------------------------- #
# Heatmap points                                                              #
# --------------------------------------------------------------------------- #
def clicks_for_path(path, days=30):
    """Return click points for one page as {x_ratio, y_px, rage} plus a
    reference document size to scale the canvas."""
    rows = (
        _base(days)
        .filter(
            HeatmapEvent.path == path,
            HeatmapEvent.event_type.in_(CLICK_TYPES),
            HeatmapEvent.x_ratio.isnot(None),
            HeatmapEvent.y_px.isnot(None),
        )
        .order_by(HeatmapEvent.created_at.desc())
        .limit(MAX_POINTS)
        .all()
    )
    points = [
        {
            "x": round(r.x_ratio, 4),
            "y": r.y_px,
            "rage": r.event_type == TYPE_RAGECLICK,
        }
        for r in rows
    ]
    # Reference doc size = median so a few outliers don't stretch the canvas.
    heights = sorted(r.doc_h for r in rows if r.doc_h)
    widths = sorted(r.doc_w for r in rows if r.doc_w)
    doc_h = heights[len(heights) // 2] if heights else 1200
    doc_w = widths[len(widths) // 2] if widths else 1440
    return {
        "path": path,
        "points": points,
        "doc_h": doc_h,
        "doc_w": doc_w,
        "count": len(points),
        "elements": top_elements(days, path=path),
    }


# --------------------------------------------------------------------------- #
# Visitor journeys                                                            #
# --------------------------------------------------------------------------- #
def journeys(days=30):
    """One row per unique visitor with a summary of their whole journey."""
    since = _since(days)
    agg = (
        db.session.query(
            HeatmapEvent.visitor_id,
            func.min(HeatmapEvent.created_at),
            func.max(HeatmapEvent.created_at),
            func.count(HeatmapEvent.id),
            func.count(func.distinct(HeatmapEvent.session_id)),
            func.count(func.distinct(HeatmapEvent.path)),
            func.max(HeatmapEvent.device),
        )
        .filter(HeatmapEvent.created_at >= since, HeatmapEvent.visitor_id.isnot(None))
        .group_by(HeatmapEvent.visitor_id)
        .order_by(func.max(HeatmapEvent.created_at).desc())
        .limit(MAX_JOURNEYS)
        .all()
    )

    # Per-visitor click totals so the list can highlight the most engaged.
    click_map = dict(
        db.session.query(HeatmapEvent.visitor_id, func.count(HeatmapEvent.id))
        .filter(
            HeatmapEvent.created_at >= since,
            HeatmapEvent.event_type.in_(CLICK_TYPES),
            HeatmapEvent.visitor_id.isnot(None),
        )
        .group_by(HeatmapEvent.visitor_id)
        .all()
    )

    out = []
    for vid, first, last, events, sessions, pages, device in agg:
        out.append(
            {
                "visitor_id": vid,
                "short_id": (vid or "")[:8],
                "first_seen": first.isoformat() if first else None,
                "last_seen": last.isoformat() if last else None,
                "events": events,
                "clicks": click_map.get(vid, 0),
                "sessions": sessions,
                "pages": pages,
                "device": device,
            }
        )
    return out


def journey_detail(visitor_id, days=90):
    """The full chronological timeline for one visitor across every session."""
    rows = (
        HeatmapEvent.query.filter(
            HeatmapEvent.visitor_id == visitor_id,
            HeatmapEvent.created_at >= _since(days),
        )
        .order_by(HeatmapEvent.created_at.asc())
        .limit(MAX_JOURNEY_EVENTS)
        .all()
    )

    timeline = []
    session_order = OrderedDict()
    for r in rows:
        sid = r.session_id or "—"
        session_order.setdefault(sid, len(session_order) + 1)
        timeline.append(
            {
                "type": r.event_type,
                "path": r.path,
                "el_text": r.el_text,
                "el_selector": r.el_selector,
                "scroll_depth": r.scroll_depth,
                "session_no": session_order[sid],
                "at": r.created_at.isoformat() if r.created_at else None,
            }
        )

    # Pages visited, in order of first visit.
    pages = list(OrderedDict.fromkeys(r.path for r in rows if r.path))
    device = next((r.device for r in reversed(rows) if r.device), None)

    return {
        "visitor_id": visitor_id,
        "short_id": (visitor_id or "")[:8],
        "device": device,
        "sessions": len(session_order),
        "events": len(rows),
        "pages": pages,
        "first_seen": timeline[0]["at"] if timeline else None,
        "last_seen": timeline[-1]["at"] if timeline else None,
        "timeline": timeline,
    }


# --------------------------------------------------------------------------- #
# Ingestion                                                                   #
# --------------------------------------------------------------------------- #
_VALID_TYPES = {TYPE_PAGEVIEW, TYPE_CLICK, TYPE_RAGECLICK, TYPE_SCROLL}


def _int(v, lo=0, hi=100000):
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return max(lo, min(hi, n))


def _float01(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, f))


def record_events(visitor_id, session_id, device, events):
    """Persist a batch of client events. Server owns visitor/session ids (read
    from the httpOnly cookies) — the client can't spoof another visitor."""
    if not isinstance(events, list):
        return 0
    saved = 0
    for ev in events[:50]:  # hard cap per beacon
        if not isinstance(ev, dict):
            continue
        etype = ev.get("t")
        if etype not in _VALID_TYPES:
            continue
        db.session.add(
            HeatmapEvent(
                visitor_id=visitor_id,
                session_id=session_id,
                event_type=etype,
                path=(str(ev.get("p") or ""))[:500] or None,
                x_ratio=_float01(ev.get("x")),
                y_px=_int(ev.get("y")),
                vw=_int(ev.get("vw")),
                vh=_int(ev.get("vh")),
                doc_w=_int(ev.get("dw")),
                doc_h=_int(ev.get("dh")),
                scroll_depth=_int(ev.get("sd"), hi=100),
                el_selector=(str(ev.get("s") or ""))[:300] or None,
                el_text=(str(ev.get("txt") or "").strip())[:200] or None,
                device=device,
            )
        )
        saved += 1
    if saved:
        db.session.commit()
    return saved
