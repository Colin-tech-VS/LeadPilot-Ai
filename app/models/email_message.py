import uuid
from datetime import datetime, timezone

from sqlalchemy import Uuid

from app.core.extensions import db


def utcnow():
    return datetime.now(timezone.utc)


DIRECTION_OUTBOUND = "outbound"
DIRECTION_INBOUND = "inbound"

STATUS_QUEUED = "queued"
STATUS_SENT = "sent"
STATUS_SIMULATED = "simulated"  # no SMTP configured — logged only
STATUS_FAILED = "failed"
STATUS_RECEIVED = "received"


class EmailMessage(db.Model):
    """Every email the platform sends or receives, stored so the admin console
    can list, read and search them (outbox + inbox)."""

    __tablename__ = "email_messages"

    id = db.Column(Uuid, primary_key=True, default=uuid.uuid4)
    direction = db.Column(db.String(10), nullable=False, default=DIRECTION_OUTBOUND, index=True)
    status = db.Column(db.String(12), nullable=False, default=STATUS_QUEUED, index=True)
    from_addr = db.Column(db.String(255), nullable=True)
    to_addr = db.Column(db.String(255), nullable=True, index=True)
    subject = db.Column(db.String(500), nullable=True)
    body = db.Column(db.Text, nullable=True)
    is_html = db.Column(db.Boolean, nullable=False, default=False)
    provider_id = db.Column(db.String(255), nullable=True)
    tenant_id = db.Column(Uuid, nullable=True, index=True)
    error = db.Column(db.String(500), nullable=True)
    read_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )

    @property
    def preview(self):
        text = (self.body or "").strip().replace("\n", " ")
        return (text[:140] + "…") if len(text) > 140 else text

    def to_dict(self):
        return {
            "id": str(self.id),
            "direction": self.direction,
            "status": self.status,
            "from_addr": self.from_addr,
            "to_addr": self.to_addr,
            "subject": self.subject,
            "body": self.body,
            "is_html": self.is_html,
            "preview": self.preview,
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "error": self.error,
            "read": self.read_at is not None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
