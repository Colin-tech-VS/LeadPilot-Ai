import uuid
import json
from datetime import datetime, timezone

from sqlalchemy import Float, Uuid

from app.core.extensions import db

def utcnow():
    return datetime.now(timezone.utc)


LEAD_STATUSES = ("new", "booked", "lost")


class Lead(db.Model):
    __tablename__ = "leads"

    id = db.Column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id = db.Column(Uuid, db.ForeignKey("tenants.id"), nullable=False, index=True)
    name = db.Column(db.String(255), nullable=False)
    phone = db.Column(db.String(50), nullable=False)
    address = db.Column(db.String(500), nullable=True)
    latitude = db.Column(Float, nullable=True)
    longitude = db.Column(Float, nullable=True)
    issue_type = db.Column(db.String(100), nullable=True)
    urgency_level = db.Column(db.String(50), nullable=True)
    status = db.Column(db.String(20), nullable=False, default="new")
    summary = db.Column(db.Text, nullable=True)
    booking_metadata = db.Column(db.Text, nullable=True)
    # Set when the plumber cancels a booked job from the prospect card. The
    # reason is sent to the client (SMS/email) and kept for the history.
    cancelled_at = db.Column(db.DateTime(timezone=True), nullable=True)
    cancel_reason = db.Column(db.Text, nullable=True)
    archived_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    tenant = db.relationship("Tenant", back_populates="leads")
    appointments = db.relationship("Appointment", back_populates="lead", lazy="dynamic")

    def get_booking(self):
        if not self.booking_metadata:
            return None
        try:
            return json.loads(self.booking_metadata)
        except (json.JSONDecodeError, TypeError):
            return None

    def set_booking(self, booking: dict):
        self.booking_metadata = json.dumps(booking)

    @property
    def acceptance_status(self):
        """IA decision for display: accepted, refused, or pending."""
        booking = self.get_booking()
        if not booking:
            return None

        action = (booking.get("action") or "").upper()
        if action == "OUT_OF_ZONE" or booking.get("out_of_zone"):
            return "refused"
        if action == "BOOK_NOW" or self.status == "booked":
            return "accepted"
        return "pending"

    @property
    def is_cancelled(self):
        return self.cancelled_at is not None

    @property
    def is_booked(self):
        """A confirmed job: the client accepted / the RDV is booked."""
        return not self.is_cancelled and (
            self.status == "booked" or self.acceptance_status == "accepted"
        )

    @property
    def maps_url(self):
        """Directions link to the client's address (opens the native maps app)."""
        if not (self.address or "").strip():
            return None
        from urllib.parse import quote_plus

        return "https://www.google.com/maps/dir/?api=1&destination=" + quote_plus(
            self.address.strip()
        )

    @property
    def is_archived(self):
        return self.archived_at is not None

    def to_dict(self):
        data = {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "name": self.name,
            "phone": self.phone,
            "address": self.address,
            "issue_type": self.issue_type,
            "urgency_level": self.urgency_level,
            "status": self.status,
            "summary": self.summary,
            "cancelled_at": self.cancelled_at.isoformat() if self.cancelled_at else None,
            "cancel_reason": self.cancel_reason,
            "archived_at": self.archived_at.isoformat() if self.archived_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        booking = self.get_booking()
        if booking:
            data["booking"] = booking
        return data