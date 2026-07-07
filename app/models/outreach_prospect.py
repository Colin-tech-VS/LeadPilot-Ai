import uuid
from datetime import datetime, timezone

from sqlalchemy import Uuid

from app.core.extensions import db


def utcnow():
    return datetime.now(timezone.utc)


OUTREACH_STATUSES = (
    "new",
    "ready",
    "contacted",
    "replied",
    "converted",
    "unsubscribed",
    "skipped",
)


class OutreachProspect(db.Model):
    """B2B artisan acquisition prospect — distinct from customer ``Lead`` rows."""

    __tablename__ = "outreach_prospects"

    id = db.Column(Uuid, primary_key=True, default=uuid.uuid4)
    first_name = db.Column(db.String(100), nullable=True)
    last_name = db.Column(db.String(100), nullable=True)
    company_name = db.Column(db.String(255), nullable=True)
    email = db.Column(db.String(255), nullable=True, index=True)
    phone = db.Column(db.String(50), nullable=True)
    trade_type = db.Column(db.String(30), nullable=False, default="plombier", index=True)
    city = db.Column(db.String(100), nullable=True, index=True)
    postal_code = db.Column(db.String(10), nullable=True)
    # URLs can legitimately exceed 500 chars (search-engine redirect/tracking
    # links), so store them unbounded rather than truncating.
    website_url = db.Column(db.Text, nullable=True)
    source_url = db.Column(db.Text, nullable=True)
    source = db.Column(db.String(50), nullable=False, default="web_search")
    status = db.Column(db.String(30), nullable=False, default="new", index=True)
    email_confidence = db.Column(db.String(20), nullable=True)
    search_query = db.Column(db.String(500), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    outreach_subject = db.Column(db.String(255), nullable=True)
    outreach_body = db.Column(db.Text, nullable=True)
    last_contacted_at = db.Column(db.DateTime(timezone=True), nullable=True)
    opted_out_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    def display_name(self) -> str:
        parts = [p for p in (self.first_name, self.last_name) if p]
        if parts:
            return " ".join(parts)
        return self.company_name or "Artisan"

    def to_dict(self):
        return {
            "id": str(self.id),
            "first_name": self.first_name,
            "last_name": self.last_name,
            "company_name": self.company_name,
            "email": self.email,
            "phone": self.phone,
            "trade_type": self.trade_type,
            "city": self.city,
            "postal_code": self.postal_code,
            "website_url": self.website_url,
            "source_url": self.source_url,
            "source": self.source,
            "status": self.status,
            "email_confidence": self.email_confidence,
            "search_query": self.search_query,
            "notes": self.notes,
            "outreach_subject": self.outreach_subject,
            "outreach_body": self.outreach_body,
            "last_contacted_at": (
                self.last_contacted_at.isoformat() if self.last_contacted_at else None
            ),
            "opted_out_at": self.opted_out_at.isoformat() if self.opted_out_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
