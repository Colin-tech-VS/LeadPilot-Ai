import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import Uuid

from app.core.extensions import db


def utcnow():
    return datetime.now(timezone.utc)


class Offer(db.Model):
    """A pricing plan surfaced on the public landing page and editable from the
    admin console (price, name, description, features…). Seeded once from the
    hard-coded plans/i18n; afterwards the DB is the source of truth so the site
    owner can change prices without touching code."""

    __tablename__ = "offers"

    id = db.Column(Uuid, primary_key=True, default=uuid.uuid4)
    key = db.Column(db.String(30), unique=True, nullable=False, index=True)
    name = db.Column(db.String(80), nullable=False, default="")
    badge = db.Column(db.String(80), nullable=True)
    price = db.Column(db.String(40), nullable=False, default="")     # e.g. "149 €"
    period = db.Column(db.String(40), nullable=True)                 # e.g. "/ mois"
    calls = db.Column(db.String(120), nullable=True)                 # e.g. "150 appels inclus"
    description = db.Column(db.String(400), nullable=True)
    features = db.Column(db.Text, nullable=True)                     # JSON list of strings
    cta = db.Column(db.String(80), nullable=True)
    featured = db.Column(db.Boolean, default=False, nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    def feature_list(self):
        if not self.features:
            return []
        try:
            data = json.loads(self.features)
            return [str(f) for f in data if str(f).strip()]
        except (json.JSONDecodeError, TypeError):
            return [line for line in self.features.splitlines() if line.strip()]

    def set_features(self, items):
        self.features = json.dumps([str(i).strip() for i in items if str(i).strip()])
