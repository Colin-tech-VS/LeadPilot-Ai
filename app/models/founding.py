"""Founding cohort: the first N artisans recruited through /50-artisans."""
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import Uuid

from app.core.extensions import db


def utcnow():
    return datetime.now(timezone.utc)


STATUSES = (
    "pending",
    "active",
    "activated",
    "at_risk",
    "completed",
    "converted",
    "cancelled",
    "expired",
)

STATUS_LABELS = {
    "pending": "En attente",
    "active": "Actif",
    "activated": "Activé",
    "at_risk": "À risque",
    "completed": "Terminé",
    "converted": "Converti",
    "cancelled": "Annulé",
    "expired": "Expiré",
}

# A validated seat is never freed — cancelled still occupies a numbered place.
SEAT_STATUSES = STATUSES

SOURCES = (
    "facebook",
    "reddit",
    "google",
    "organic",
    "referral",
    "direct",
    "partner",
    "other",
)

SOURCE_LABELS = {
    "facebook": "Facebook",
    "reddit": "Reddit",
    "google": "Google",
    "organic": "Organique",
    "referral": "Parrainage",
    "direct": "Direct",
    "partner": "Partenaire",
    "other": "Autre",
}


class FoundingParticipant(db.Model):
    __tablename__ = "founding_participants"

    id = db.Column(Uuid, primary_key=True, default=uuid.uuid4)
    place_number = db.Column(db.Integer, nullable=False, unique=True, index=True)
    tenant_id = db.Column(Uuid, db.ForeignKey("tenants.id"), nullable=False, unique=True, index=True)
    user_id = db.Column(Uuid, db.ForeignKey("users.id"), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default="active", index=True)
    source = db.Column(db.String(40), nullable=True, index=True)
    utm_source = db.Column(db.String(80), nullable=True)
    utm_medium = db.Column(db.String(80), nullable=True)
    utm_campaign = db.Column(db.String(120), nullable=True)
    utm_content = db.Column(db.String(120), nullable=True)
    referral_code = db.Column(db.String(16), nullable=False, unique=True, index=True)
    referred_by_id = db.Column(Uuid, db.ForeignKey("founding_participants.id"), nullable=True, index=True)
    started_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    ends_at = db.Column(db.DateTime(timezone=True), nullable=False)
    last_usage_at = db.Column(db.DateTime(timezone=True), nullable=True)
    feedback_text = db.Column(db.Text, nullable=True)
    testimonial_consent = db.Column(db.Boolean, nullable=False, default=False)
    emails_sent_json = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)

    tenant = db.relationship("Tenant")
    user = db.relationship("User")
    referrer = db.relationship("FoundingParticipant", remote_side=[id], uselist=False)

    def emails_sent(self) -> list[str]:
        raw = self.emails_sent_json or "[]"
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
        return data if isinstance(data, list) else []

    def mark_email_sent(self, key: str) -> bool:
        sent = self.emails_sent()
        if key in sent:
            return False
        sent.append(key)
        self.emails_sent_json = json.dumps(sent)
        return True

    def has_email(self, key: str) -> bool:
        return key in self.emails_sent()


class FoundingWaitlist(db.Model):
    __tablename__ = "founding_waitlist"

    id = db.Column(Uuid, primary_key=True, default=uuid.uuid4)
    name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(255), nullable=False, unique=True, index=True)
    phone = db.Column(db.String(50), nullable=True)
    trade_type = db.Column(db.String(30), nullable=True)
    city = db.Column(db.String(100), nullable=True)
    source = db.Column(db.String(40), nullable=True)
    utm_source = db.Column(db.String(80), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    notified_at = db.Column(db.DateTime(timezone=True), nullable=True)


class FoundingStatusEvent(db.Model):
    __tablename__ = "founding_status_events"

    id = db.Column(Uuid, primary_key=True, default=uuid.uuid4)
    participant_id = db.Column(
        Uuid, db.ForeignKey("founding_participants.id"), nullable=False, index=True
    )
    old_status = db.Column(db.String(20), nullable=True)
    new_status = db.Column(db.String(20), nullable=False)
    actor = db.Column(db.String(120), nullable=False, default="system")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)

    participant = db.relationship("FoundingParticipant")
