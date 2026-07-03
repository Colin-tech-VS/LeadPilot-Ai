import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import Float, Uuid

from app.core.extensions import db

# Length of the free trial offered on the landing page. Kept here so the
# signup flow and the subscription checks stay in sync.
TRIAL_DAYS = 14


def utcnow():
    return datetime.now(timezone.utc)


class Tenant(db.Model):
    __tablename__ = "tenants"

    id = db.Column(Uuid, primary_key=True, default=uuid.uuid4)
    name = db.Column(db.String(255), nullable=False)
    first_name = db.Column(db.String(100), nullable=True)
    last_name = db.Column(db.String(100), nullable=True)
    # First name the plumber gives to the AI receptionist (how it introduces
    # itself: "je suis {ai_assistant_name}, l'assistante de {first_name}").
    ai_assistant_name = db.Column(db.String(100), nullable=True)
    siret = db.Column(db.String(14), nullable=True)
    phone_number = db.Column(db.String(50), nullable=True)
    ai_phone_number = db.Column(db.String(50), nullable=True)
    address = db.Column(db.String(500), nullable=True)
    postal_code = db.Column(db.String(10), nullable=True)
    city = db.Column(db.String(100), nullable=True)
    latitude = db.Column(Float, nullable=True)
    longitude = db.Column(Float, nullable=True)
    service_radius_km = db.Column(db.Integer, nullable=True, default=30)
    # Billing: "trial" until the plumber upgrades to a paid plan (e.g. "solo",
    # "pro"). The AI phone line only answers while the subscription is active.
    plan = db.Column(db.String(20), nullable=False, default="trial")
    trial_ends_at = db.Column(db.DateTime(timezone=True), nullable=True)
    stripe_customer_id = db.Column(db.String(64), nullable=True)
    stripe_subscription_id = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    users = db.relationship("User", back_populates="tenant", lazy="dynamic")
    leads = db.relationship("Lead", back_populates="tenant", lazy="dynamic")
    appointments = db.relationship("Appointment", back_populates="tenant", lazy="dynamic")

    @property
    def full_address(self):
        parts = [p for p in (self.address, self.postal_code, self.city) if p]
        return ", ".join(parts)

    @property
    def trial_end_date(self):
        """When the free trial expires (falls back to created_at + TRIAL_DAYS
        for tenants created before trial tracking existed). Always returned as
        a timezone-aware datetime — SQLite hands back naive values."""
        if self.trial_ends_at:
            end = self.trial_ends_at
            return end if end.tzinfo else end.replace(tzinfo=timezone.utc)
        base = self.created_at or utcnow()
        if base.tzinfo is None:
            base = base.replace(tzinfo=timezone.utc)
        return base + timedelta(days=TRIAL_DAYS)

    @property
    def is_paid(self):
        return bool(self.plan) and self.plan != "trial"

    @property
    def is_trialing(self):
        """True while still on the free trial and not yet expired."""
        return not self.is_paid and utcnow() <= self.trial_end_date

    @property
    def subscription_active(self):
        """Whether the AI phone line should answer: paid plan, or trial not
        yet expired."""
        return self.is_paid or utcnow() <= self.trial_end_date

    @property
    def trial_days_left(self):
        """Whole days remaining on the trial (0 once expired)."""
        if self.is_paid:
            return None
        remaining = self.trial_end_date - utcnow()
        return max(0, remaining.days + (1 if remaining.seconds or remaining.microseconds else 0))

    def to_dict(self):
        return {
            "id": str(self.id),
            "name": self.name,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "ai_assistant_name": self.ai_assistant_name,
            "plan": self.plan,
            "trial_ends_at": self.trial_end_date.isoformat() if not self.is_paid else None,
            "subscription_active": self.subscription_active,
            "siret": self.siret,
            "phone_number": self.phone_number,
            "ai_phone_number": self.ai_phone_number,
            "address": self.address,
            "postal_code": self.postal_code,
            "city": self.city,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "service_radius_km": self.service_radius_km,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
