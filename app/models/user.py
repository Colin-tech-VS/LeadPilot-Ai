import uuid
from datetime import datetime, timezone

from sqlalchemy import Uuid
from werkzeug.security import check_password_hash, generate_password_hash

from app.core.extensions import db


def utcnow():
    return datetime.now(timezone.utc)


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(Uuid, primary_key=True, default=uuid.uuid4)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    tenant_id = db.Column(Uuid, db.ForeignKey("tenants.id"), nullable=True, index=True)
    # "admin"/"user" = artisan side (has a tenant). "customer" = particulier who
    # books artisans (no tenant). The role is the single source of truth.
    role = db.Column(db.String(20), nullable=False, default="user")
    # Populated for customers (particuliers); optional for artisan accounts.
    first_name = db.Column(db.String(100), nullable=True)
    last_name = db.Column(db.String(100), nullable=True)
    phone = db.Column(db.String(50), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    tenant = db.relationship("Tenant", back_populates="users")

    @property
    def is_customer(self) -> bool:
        return self.role == "customer"

    @property
    def full_name(self) -> str | None:
        parts = [p for p in (self.first_name, self.last_name) if p]
        return " ".join(parts) if parts else None

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def to_dict(self):
        return {
            "id": str(self.id),
            "email": self.email,
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "role": self.role,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "phone": self.phone,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
