"""Unclaimed directory entries sourced from the official business registry.

A marketplace with an empty directory cannot be sold to either side: the
visitor finds nobody, and the professional is asked to join a page with no
audience. Seeding the directory from the public registry breaks that deadlock —
every artisan already has a page, and the pitch becomes "claim yours" instead
of "sign up".

Boundaries this model enforces, because publishing third-party business data
carries obligations:

* Only records the registry marks as publicly diffusible (``statut_diffusion``
  ``"O"``) and administratively active are ever stored. A business that opted
  out of public diffusion at INSEE stays out here too.
* No personal data about the director is kept. The registry exposes birth dates;
  none of it is needed to list a business, so none of it is stored.
* Nothing is invented. Every field is a registry field. No ratings, no reviews,
  no availability, no implied relationship with PilotCore.
* ``opted_out`` is terminal: a business that asks to be delisted is kept as a
  tombstone so a later ingestion cannot silently resurrect it.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import ForeignKey, Uuid

from app.core.extensions import db


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Listing lifecycle.
STATUS_LISTED = "listed"        # public, unclaimed, from the registry
STATUS_CLAIMED = "claimed"      # an artisan proved ownership; a Tenant now owns it
STATUS_OPTED_OUT = "opted_out"  # delisting requested — never display, never re-ingest

STATUSES = (STATUS_LISTED, STATUS_CLAIMED, STATUS_OPTED_OUT)


class RegistryListing(db.Model):
    __tablename__ = "registry_listings"

    id = db.Column(Uuid, primary_key=True, default=uuid.uuid4)
    siren = db.Column(db.String(9), nullable=False, unique=True, index=True)
    siret = db.Column(db.String(14), nullable=True)
    name = db.Column(db.String(255), nullable=False)

    trade_key = db.Column(db.String(30), nullable=False, index=True)
    naf_code = db.Column(db.String(10), nullable=True)

    address = db.Column(db.String(400), nullable=True)
    postal_code = db.Column(db.String(10), nullable=True, index=True)
    city = db.Column(db.String(120), nullable=True)
    city_slug = db.Column(db.String(140), nullable=True, index=True)
    dept_code = db.Column(db.String(5), nullable=True, index=True)
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)

    date_creation = db.Column(db.String(10), nullable=True)
    employee_range = db.Column(db.String(10), nullable=True)

    status = db.Column(db.String(20), nullable=False, default=STATUS_LISTED, index=True)
    claimed_tenant_id = db.Column(Uuid, ForeignKey("tenants.id"), nullable=True, index=True)
    claimed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    opted_out_at = db.Column(db.DateTime(timezone=True), nullable=True)

    first_seen_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    __table_args__ = (
        db.Index("ix_registry_listings_trade_city", "trade_key", "city_slug"),
    )

    @property
    def is_public(self) -> bool:
        return self.status == STATUS_LISTED

    @property
    def years_active(self) -> int | None:
        """Years since registration — the one credibility signal we can state
        from the registry alone, without inventing anything."""
        if not self.date_creation:
            return None
        try:
            year = int(self.date_creation[:4])
        except (ValueError, TypeError):
            return None
        delta = utcnow().year - year
        return delta if delta >= 0 else None
