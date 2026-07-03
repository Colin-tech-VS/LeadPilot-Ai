import uuid
from datetime import datetime, timezone

from sqlalchemy import Uuid

from app.core.extensions import db


def utcnow():
    return datetime.now(timezone.utc)


class Appointment(db.Model):
    __tablename__ = "appointments"

    id = db.Column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id = db.Column(Uuid, db.ForeignKey("tenants.id"), nullable=False, index=True)
    lead_id = db.Column(Uuid, db.ForeignKey("leads.id"), nullable=False, index=True)
    date_time = db.Column(db.DateTime(timezone=True), nullable=False)
    status = db.Column(db.String(50), nullable=False, default="scheduled")
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    tenant = db.relationship("Tenant", back_populates="appointments")
    lead = db.relationship("Lead", back_populates="appointments")

    def to_dict(self):
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "lead_id": str(self.lead_id),
            "date_time": self.date_time.isoformat() if self.date_time else None,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
