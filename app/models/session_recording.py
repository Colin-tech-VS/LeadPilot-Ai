import uuid
from datetime import datetime, timezone

from sqlalchemy import Uuid

from app.core.extensions import db


def utcnow():
    return datetime.now(timezone.utc)


class SessionRecording(db.Model):
    """A lightweight *session replay* — the recorded track of one visitor's
    page visit, replayed in the admin console as a "film" of their cursor.

    Unlike :class:`HeatmapEvent` (discrete aggregated clicks), a recording is a
    compact **time-series** of pointer movements, clicks and scroll positions so
    the admin can literally watch the mouse move, click and scroll on a replay
    of the real page.

    One row = one page visit, keyed by a client-generated ``rec_id`` (unique per
    page load). The client re-sends the full, growing track periodically and on
    unload; the server upserts by ``rec_id`` so the row always holds the latest
    complete track. No PII is stored: only pointer coordinates (relative to the
    document), timestamps and scroll offsets — never keystrokes or field values.

    Coordinates mirror the heatmap convention so a recording can be replayed at
    any screen size: ``x`` is a 0-1 fraction of the document width, ``y`` is an
    absolute pixel offset in the full document.
    """

    __tablename__ = "session_recordings"

    # Client-generated id (one per page load) so re-sent batches upsert in place.
    rec_id = db.Column(db.String(40), primary_key=True)
    visitor_id = db.Column(db.String(40), nullable=True, index=True)
    session_id = db.Column(db.String(40), nullable=True, index=True)
    path = db.Column(db.String(500), nullable=True, index=True)
    device = db.Column(db.String(20), nullable=True)

    vw = db.Column(db.Integer, nullable=True)      # viewport width
    vh = db.Column(db.Integer, nullable=True)      # viewport height
    doc_w = db.Column(db.Integer, nullable=True)   # full document width
    doc_h = db.Column(db.Integer, nullable=True)   # full document height

    duration_ms = db.Column(db.Integer, nullable=True)   # length of the recording
    samples = db.Column(db.Integer, nullable=True)       # total tracked points
    click_count = db.Column(db.Integer, nullable=True)

    # Compact track as JSON text: {"m": [[t,x,y],...], "c": [[t,x,y],...],
    # "s": [[t,y],...]} where t is ms since page arrival, x a 0-1 ratio, y a
    # pixel offset (scroll "y" is the pixel scroll position).
    track = db.Column(db.Text, nullable=True)

    created_at = db.Column(
        db.DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )
    updated_at = db.Column(
        db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    def to_dict(self, include_track=False):
        d = {
            "rec_id": self.rec_id,
            "visitor_id": self.visitor_id,
            "short_id": (self.visitor_id or "")[:8],
            "session_id": self.session_id,
            "path": self.path,
            "device": self.device,
            "vw": self.vw,
            "vh": self.vh,
            "doc_w": self.doc_w,
            "doc_h": self.doc_h,
            "duration_ms": self.duration_ms or 0,
            "samples": self.samples or 0,
            "click_count": self.click_count or 0,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_track:
            import json

            try:
                d["track"] = json.loads(self.track) if self.track else {"m": [], "c": [], "s": []}
            except (ValueError, TypeError):
                d["track"] = {"m": [], "c": [], "s": []}
        return d
