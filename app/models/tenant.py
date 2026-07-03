import uuid
from datetime import datetime, timezone

from sqlalchemy import Float, Uuid

from app.core.extensions import db


def utcnow():
    return datetime.now(timezone.utc)


class Tenant(db.Model):
    __tablename__ = "tenants"

    id = db.Column(Uuid, primary_key=True, default=uuid.uuid4)
    name = db.Column(db.String(255), nullable=False)
    first_name = db.Column(db.String(100), nullable=True)
    last_name = db.Column(db.String(100), nullable=True)
    siret = db.Column(db.String(14), nullable=True)
    phone_number = db.Column(db.String(50), nullable=True)
    ai_phone_number = db.Column(db.String(50), nullable=True)
    address = db.Column(db.String(500), nullable=True)
    postal_code = db.Column(db.String(10), nullable=True)
    city = db.Column(db.String(100), nullable=True)
    latitude = db.Column(Float, nullable=True)
    longitude = db.Column(Float, nullable=True)
    service_radius_km = db.Column(db.Integer, nullable=True, default=30)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    users = db.relationship("User", back_populates="tenant", lazy="dynamic")
    leads = db.relationship("Lead", back_populates="tenant", lazy="dynamic")
    appointments = db.relationship("Appointment", back_populates="tenant", lazy="dynamic")

    @property
    def full_address(self):
        parts = [p for p in (self.address, self.postal_code, self.city) if p]
        return ", ".join(parts)

    def to_dict(self):
        return {
            "id": str(self.id),
            "name": self.name,
            "first_name": self.first_name,
            "last_name": self.last_name,
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
