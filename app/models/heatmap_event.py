import uuid
from datetime import datetime, timezone

from sqlalchemy import Uuid

from app.core.extensions import db


def utcnow():
    return datetime.now(timezone.utc)


# Event types captured by the client-side tracker.
TYPE_PAGEVIEW = "pageview"
TYPE_CLICK = "click"
TYPE_RAGECLICK = "rageclick"
TYPE_SCROLL = "scroll"


class HeatmapEvent(db.Model):
    """A single client-side interaction (click, rage-click, scroll, page view)
    used to build the admin heatmap and per-visitor journey.

    Events are keyed by ``visitor_id`` (the long-lived ``lp_vid`` cookie shared
    with server-side page-view tracking) so a visitor is followed as ONE
    continuous journey across every session — never one heatmap per session.
    Coordinates are stored relative to the document (``x_ratio`` = fraction of
    document width, ``y_px`` = absolute pixel offset) so clicks can be replayed
    onto a heat cloud whatever the screen size. No PII is stored: only the
    clicked element's tag/id/class selector and a short trimmed label.
    """

    __tablename__ = "heatmap_events"

    id = db.Column(Uuid, primary_key=True, default=uuid.uuid4)
    visitor_id = db.Column(db.String(40), nullable=True, index=True)
    session_id = db.Column(db.String(40), nullable=True, index=True)
    event_type = db.Column(db.String(20), nullable=False, default=TYPE_CLICK, index=True)
    path = db.Column(db.String(500), nullable=True, index=True)

    # Click geometry (null for pageview/scroll rows).
    x_ratio = db.Column(db.Float, nullable=True)  # clientX+scrollX / document width
    y_px = db.Column(db.Integer, nullable=True)   # clientY+scrollY absolute
    vw = db.Column(db.Integer, nullable=True)     # viewport width
    vh = db.Column(db.Integer, nullable=True)     # viewport height
    doc_w = db.Column(db.Integer, nullable=True)  # full document width
    doc_h = db.Column(db.Integer, nullable=True)  # full document height
    scroll_depth = db.Column(db.Integer, nullable=True)  # max scroll % for the page

    el_selector = db.Column(db.String(300), nullable=True)  # tag#id.class
    el_text = db.Column(db.String(200), nullable=True)      # short visible label

    device = db.Column(db.String(20), nullable=True)  # mobile / desktop
    created_at = db.Column(
        db.DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )

    def to_dict(self):
        return {
            "id": str(self.id),
            "visitor_id": self.visitor_id,
            "session_id": self.session_id,
            "event_type": self.event_type,
            "path": self.path,
            "x_ratio": self.x_ratio,
            "y_px": self.y_px,
            "vw": self.vw,
            "vh": self.vh,
            "doc_w": self.doc_w,
            "doc_h": self.doc_h,
            "scroll_depth": self.scroll_depth,
            "el_selector": self.el_selector,
            "el_text": self.el_text,
            "device": self.device,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
