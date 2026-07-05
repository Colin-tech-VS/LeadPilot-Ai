import uuid
from datetime import datetime, timezone

from sqlalchemy import Uuid

from app.core.extensions import db


def utcnow():
    return datetime.now(timezone.utc)


# Event types surfaced to the plumber. Kept as plain strings so new events can
# be added without a migration.
TYPE_NEW_LEAD = "new_lead"
TYPE_URGENT_LEAD = "urgent_lead"
TYPE_APPOINTMENT = "appointment"
TYPE_QUOTE_ACCEPTED = "quote_accepted"
TYPE_QUOTE_REFUSED = "quote_refused"
TYPE_QUOTE_SENT = "quote_sent"
TYPE_LEAD_CANCELLED = "lead_cancelled"
TYPE_INVOICE_PAID = "invoice_paid"


class Notification(db.Model):
    """An important event for a plumber, surfaced live on web + mobile.

    Rows are polled by the front-end (see static/js/notifications.js) which
    renders an in-page toast and a native OS notification — so the plumber is
    alerted even when the tab is in the background, as long as a session is
    open on the web app.
    """

    __tablename__ = "notifications"

    id = db.Column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id = db.Column(Uuid, db.ForeignKey("tenants.id"), nullable=False, index=True)

    type = db.Column(db.String(40), nullable=False, default=TYPE_NEW_LEAD)
    title = db.Column(db.String(255), nullable=False)
    body = db.Column(db.String(500), nullable=True)
    icon = db.Column(db.String(16), nullable=True)
    url = db.Column(db.String(255), nullable=True)

    read_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )

    tenant = db.relationship("Tenant")

    def to_dict(self):
        return {
            "id": str(self.id),
            "type": self.type,
            "title": self.title,
            "body": self.body or "",
            "icon": self.icon or "🔔",
            "url": self.url or "/dashboard",
            "read": self.read_at is not None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
