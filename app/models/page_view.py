import uuid
from datetime import datetime, timezone

from sqlalchemy import Uuid

from app.core.extensions import db


def utcnow():
    return datetime.now(timezone.utc)


class PageView(db.Model):
    """One page view of the public site / app, recorded server-side for the
    GA4-style traffic dashboard. Visitor and session ids come from cookies so
    we can count unique visitors, sessions and bounce rate without any external
    analytics provider. IPs are stored hashed for privacy."""

    __tablename__ = "page_views"

    id = db.Column(Uuid, primary_key=True, default=uuid.uuid4)
    visitor_id = db.Column(db.String(40), nullable=True, index=True)
    session_id = db.Column(db.String(40), nullable=True, index=True)
    path = db.Column(db.String(500), nullable=True, index=True)
    referrer = db.Column(db.String(500), nullable=True)
    referrer_host = db.Column(db.String(200), nullable=True, index=True)
    user_agent = db.Column(db.String(300), nullable=True)
    device = db.Column(db.String(20), nullable=True)  # mobile / desktop / bot
    lang = db.Column(db.String(10), nullable=True)
    ip_hash = db.Column(db.String(64), nullable=True)
    is_new_session = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(
        db.DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )

    def to_dict(self):
        return {
            "id": str(self.id),
            "path": self.path,
            "referrer_host": self.referrer_host,
            "device": self.device,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
