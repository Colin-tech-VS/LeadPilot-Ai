import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import Uuid

from app.core.extensions import db


def utcnow():
    return datetime.now(timezone.utc)


# Broad buckets used to colour/filter the log. Kept as plain strings so new
# categories can be added without a migration.
CAT_AUTH = "auth"
CAT_LEAD = "lead"
CAT_APPOINTMENT = "appointment"
CAT_QUOTE = "quote"
CAT_BILLING = "billing"
CAT_EMAIL = "email"
CAT_ADMIN = "admin"
CAT_SYSTEM = "system"

LEVEL_INFO = "info"
LEVEL_SUCCESS = "success"
LEVEL_WARNING = "warning"
LEVEL_ERROR = "error"


class Event(db.Model):
    """A single audit/analytics event, surfaced in the admin log and used to
    build funnels. Deliberately schema-light so any part of the app can record
    something meaningful without a migration."""

    __tablename__ = "events"

    id = db.Column(Uuid, primary_key=True, default=uuid.uuid4)
    category = db.Column(db.String(30), nullable=False, default=CAT_SYSTEM, index=True)
    action = db.Column(db.String(60), nullable=False, index=True)
    level = db.Column(db.String(10), nullable=False, default=LEVEL_INFO)
    actor = db.Column(db.String(120), nullable=True)  # "admin", "system", email…
    tenant_id = db.Column(Uuid, nullable=True, index=True)
    summary = db.Column(db.String(500), nullable=True)
    meta = db.Column(db.Text, nullable=True)  # JSON blob
    ip = db.Column(db.String(64), nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )

    def get_meta(self):
        if not self.meta:
            return {}
        try:
            return json.loads(self.meta)
        except (json.JSONDecodeError, TypeError):
            return {}

    def to_dict(self):
        return {
            "id": str(self.id),
            "category": self.category,
            "action": self.action,
            "level": self.level,
            "actor": self.actor,
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "summary": self.summary,
            "meta": self.get_meta(),
            "ip": self.ip,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
