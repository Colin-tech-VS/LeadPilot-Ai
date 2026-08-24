"""Claim and delisting handling for registry-sourced directory entries.

Two flows, deliberately asymmetric:

* **Claiming** notifies the admin and leaves the listing public and unclaimed
  until a human confirms. Handing a business page to whoever fills in a form
  first would be an obvious impersonation route, so ownership is never granted
  automatically.
* **Delisting** takes effect immediately, with no account and no justification
  asked. Publishing someone's business data is our choice; removing it on
  request is their right, and putting friction in front of it would be
  indefensible.
"""
from __future__ import annotations

import logging

from app.core.extensions import db
from app.models.registry_listing import (
    STATUS_LISTED,
    STATUS_OPTED_OUT,
    RegistryListing,
    utcnow,
)

logger = logging.getLogger(__name__)


def submit(listing: RegistryListing, *, contact_name: str, email: str, phone: str = "") -> None:
    """Record a claim request and alert the admin.

    The listing stays ``listed`` — a claim is a request, not a transfer.
    """
    from app.services import admin_email

    lines = [
        "Demande de revendication de fiche",
        "",
        f"Entreprise : {listing.name}",
        f"SIREN      : {listing.siren}",
        f"Métier     : {listing.trade_key}",
        f"Commune    : {listing.city or '—'} ({listing.postal_code or '—'})",
        "",
        f"Nom        : {contact_name}",
        f"E-mail     : {email}",
        f"Téléphone  : {phone or '—'}",
        "",
        "À vérifier avant de transférer la fiche : correspondance entre le",
        "demandeur et l'entreprise (e-mail au domaine, extrait Kbis, ou appel",
        "sur le numéro public de l'entreprise).",
    ]
    try:
        from flask import current_app

        admin_email.send_email(
            current_app.config.get("EMAIL_FROM") or "contact@pilotcore.fr",
            f"[PilotCore] Revendication — {listing.name} ({listing.siren})",
            "\n".join(lines),
        )
    except Exception:  # noqa: BLE001 — the artisan must not see a mail failure
        logger.exception("Claim notification failed for SIREN %s", listing.siren)

    try:
        from app.services.events import CAT_ADMIN, LEVEL_INFO, log_event

        log_event(
            CAT_ADMIN,
            "listing_claim",
            summary=f"Revendication « {listing.name} » ({listing.siren}) par {email}",
            level=LEVEL_INFO,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Claim event logging failed")


def opt_out(siren: str, *, reason: str = "") -> bool:
    """Delist a business permanently. Returns whether a listing was affected.

    The caller must not surface that boolean: answering differently for a known
    and an unknown SIREN would turn the form into a way of probing the
    directory's contents.
    """
    siren = (siren or "").strip()
    listing = RegistryListing.query.filter_by(siren=siren).one_or_none()
    if listing is None:
        # Tombstone the SIREN anyway, so a later import cannot list a business
        # that has already asked not to be listed.
        db.session.add(
            RegistryListing(
                siren=siren,
                name="(retrait demandé)",
                trade_key="autre",
                status=STATUS_OPTED_OUT,
                opted_out_at=utcnow(),
            )
        )
        db.session.commit()
        return False

    if listing.status != STATUS_OPTED_OUT:
        listing.status = STATUS_OPTED_OUT
        listing.opted_out_at = utcnow()
        db.session.commit()

    try:
        from app.services.events import CAT_ADMIN, LEVEL_INFO, log_event

        log_event(
            CAT_ADMIN,
            "listing_optout",
            summary=f"Retrait de fiche demandé — {siren}" + (f" ({reason[:120]})" if reason else ""),
            level=LEVEL_INFO,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Opt-out event logging failed")
    return True


def approve(siren: str, tenant_id) -> RegistryListing | None:
    """Attach a listing to a tenant once ownership has been verified."""
    from app.models.registry_listing import STATUS_CLAIMED

    listing = RegistryListing.query.filter_by(siren=(siren or "").strip()).one_or_none()
    if listing is None or listing.status == STATUS_OPTED_OUT:
        return None
    listing.status = STATUS_CLAIMED
    listing.claimed_tenant_id = tenant_id
    listing.claimed_at = utcnow()
    db.session.commit()
    return listing


def pending_count() -> int:
    return RegistryListing.query.filter_by(status=STATUS_LISTED).count()
