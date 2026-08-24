"""A request to take ownership of a registry-sourced directory listing.

Kept as its own table rather than a few columns on the listing: several people
can claim the same business, and who asked for what — and what was decided —
is exactly the trail worth having if an ownership decision is ever disputed.

A claim never transfers anything by itself. It records intent; a human decides.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import ForeignKey, Uuid

from app.core.extensions import db


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"

STATUS_LABELS = {
    STATUS_PENDING: "En attente",
    STATUS_APPROVED: "Validée",
    STATUS_REJECTED: "Refusée",
}


class ListingClaim(db.Model):
    __tablename__ = "listing_claims"

    id = db.Column(Uuid, primary_key=True, default=uuid.uuid4)
    listing_id = db.Column(
        Uuid, ForeignKey("registry_listings.id"), nullable=False, index=True
    )
    siren = db.Column(db.String(9), nullable=False, index=True)

    contact_name = db.Column(db.String(160), nullable=False)
    email = db.Column(db.String(255), nullable=False, index=True)
    phone = db.Column(db.String(50), nullable=True)

    status = db.Column(db.String(20), nullable=False, default=STATUS_PENDING, index=True)
    decided_at = db.Column(db.DateTime(timezone=True), nullable=True)
    decision_note = db.Column(db.Text, nullable=True)
    created_tenant_id = db.Column(Uuid, ForeignKey("tenants.id"), nullable=True)

    # Kept for the audit trail: a claim arriving from a different network than
    # the business' own area is not proof of anything, but it is context a
    # human reviewer may want when a decision looks doubtful.
    ip_hash = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    listing = db.relationship("RegistryListing", backref="claims")

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    @property
    def is_pending(self) -> bool:
        return self.status == STATUS_PENDING
