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
    # When the artisan asked for their own receptionist line from the dashboard.
    # A trial without a dedicated number has no receptionist at all — calls to
    # the shared line are routed elsewhere — so this is the moment the free
    # trial starts being a trial rather than an empty dashboard. It is a request,
    # not a guarantee: the number may be bought a few seconds later, or by
    # ``scripts/provision_numbers.py`` if Twilio was unavailable.
    line_requested_at = db.Column(db.DateTime(timezone=True), nullable=True)
    address = db.Column(db.String(500), nullable=True)
    postal_code = db.Column(db.String(10), nullable=True)
    city = db.Column(db.String(100), nullable=True)
    latitude = db.Column(Float, nullable=True)
    longitude = db.Column(Float, nullable=True)
    service_radius_km = db.Column(db.Integer, nullable=True, default=30)
    # Artisan's handwritten signature, stored as a PNG data URL (drawn on the
    # settings signature pad). Printed on every devis so quotes the AI sends are
    # already signed by the plumber. Null = no signature configured yet.
    signature = db.Column(db.Text, nullable=True)
    # Bank details (RIB) printed on the devis and sent to the client so they can
    # pay the acompte (deposit). Configured once in Paramètres. Null = not set.
    iban = db.Column(db.String(40), nullable=True)
    bic = db.Column(db.String(15), nullable=True)
    bank_holder = db.Column(db.String(255), nullable=True)
    # Billing: "trial" until the plumber upgrades to a paid plan ("starter",
    # "pro", "premium"). The AI line only answers while the subscription is active.
    plan = db.Column(db.String(20), nullable=False, default="trial")
    trial_ends_at = db.Column(db.DateTime(timezone=True), nullable=True)
    stripe_customer_id = db.Column(db.String(64), nullable=True)
    stripe_subscription_id = db.Column(db.String(64), nullable=True)
    # Stripe Connect Express — client card deposits are paid out to this account.
    stripe_connect_account_id = db.Column(db.String(64), nullable=True)
    stripe_connect_charges_enabled = db.Column(db.Boolean, nullable=False, default=False)
    # Last calendar month whose call overage was billed to Stripe, as "YYYY-MM".
    # Guards the monthly overage job against double-billing the same period.
    last_overage_period = db.Column(db.String(7), nullable=True)
    # Public directory (Planity-style client booking)
    trade_type = db.Column(db.String(30), nullable=False, default="plombier", index=True)
    public_slug = db.Column(db.String(100), nullable=True, unique=True, index=True)
    is_public = db.Column(db.Boolean, nullable=False, default=False, index=True)
    public_blurb = db.Column(db.String(500), nullable=True)
    # When False (default), only the AI line is shown on the public profile.
    show_direct_phone_public = db.Column(db.Boolean, nullable=False, default=False)
    # Set once the artisan has answered « est-ce votre fiche ? ». A name+city
    # match is only a hint, so the merge always needs an explicit yes — but
    # asking during sign-up cost the account itself, so the question is put on
    # the dashboard instead and this records that it has been settled.
    listing_prompt_answered_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    users = db.relationship("User", back_populates="tenant", lazy="dynamic")
    leads = db.relationship("Lead", back_populates="tenant", lazy="dynamic")
    appointments = db.relationship("Appointment", back_populates="tenant", lazy="dynamic")

    @property
    def full_address(self):
        parts = [p for p in (self.address, self.postal_code, self.city) if p]
        return ", ".join(parts)

    @property
    def has_bank_details(self):
        """True once an IBAN is configured — required to invoice the acompte."""
        return bool((self.iban or "").strip())

    @property
    def stripe_connect_ready(self):
        """Cached flag: Connect onboarding done and card payouts enabled."""
        return bool(
            (self.stripe_connect_account_id or "").strip()
            and self.stripe_connect_charges_enabled
        )

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
    def line_requested(self):
        return self.line_requested_at is not None

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
            "line_requested": self.line_requested,
            "address": self.address,
            "postal_code": self.postal_code,
            "city": self.city,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "service_radius_km": self.service_radius_km,
            "trade_type": self.trade_type,
            "public_slug": self.public_slug,
            "is_public": self.is_public,
            "public_blurb": self.public_blurb,
            "show_direct_phone_public": bool(self.show_direct_phone_public),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
