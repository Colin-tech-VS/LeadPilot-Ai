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


def _request_ip_hash() -> str | None:
    """Hashed caller IP, matching the convention used for page views.

    Hashed rather than stored raw: it only ever serves to compare two claims
    against each other, which a hash does just as well as the address itself.
    """
    import hashlib

    try:
        from flask import has_request_context, request

        if not has_request_context():
            return None
        ip = (
            request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            or request.remote_addr
            or ""
        )
        return hashlib.sha256(ip.encode()).hexdigest() if ip else None
    except Exception:  # noqa: BLE001 — never block a claim over telemetry
        return None


def submit(listing: RegistryListing, *, contact_name: str, email: str, phone: str = "") -> None:
    """Record a claim request and alert the admin.

    The listing stays ``listed`` — a claim is a request, not a transfer.
    """
    from app.models.listing_claim import ListingClaim
    from app.services import admin_email

    claim = ListingClaim(
        listing_id=listing.id,
        siren=listing.siren,
        contact_name=contact_name[:160],
        email=email[:255],
        phone=(phone or "")[:50] or None,
        ip_hash=_request_ip_hash(),
    )
    db.session.add(claim)
    db.session.commit()

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


def attach(siren: str, tenant_id) -> RegistryListing | None:
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


def approve(claim, *, note: str = "") -> tuple[bool, str]:
    """Turn an approved claim into a real artisan account.

    Creates the tenant from the registry data, attaches the listing to it, and
    mails the artisan a password-reset link so they set their own credentials —
    we never invent a password on someone's behalf and send it in the clear.

    Returns ``(ok, message)``; the message is meant for the admin.
    """
    from app.models.listing_claim import STATUS_APPROVED
    from app.models.registry_listing import STATUS_OPTED_OUT as _OUT
    from app.models.user import User

    listing = claim.listing
    if listing is None:
        return False, "Fiche introuvable."
    if listing.status == _OUT:
        return False, "Cette fiche a fait l'objet d'une demande de retrait."

    existing = User.query.filter_by(email=claim.email).first()
    if existing is not None:
        # Already has an account: link the listing to that tenant rather than
        # creating a duplicate one.
        attach(listing.siren, existing.tenant_id)
        claim.status = STATUS_APPROVED
        claim.decided_at = utcnow()
        claim.decision_note = (note or "Compte existant : fiche rattachée.")[:2000]
        claim.created_tenant_id = existing.tenant_id
        db.session.commit()
        return True, f"Fiche rattachée au compte existant {claim.email}."

    import secrets

    from app.services.signup_service import register_plumber

    try:
        _user, tenant = register_plumber(
            email=claim.email,
            # Never chosen by us in a durable sense: the artisan is sent a reset
            # link and picks their own. This value only has to survive until then.
            password=secrets.token_urlsafe(24),
            company_name=listing.name,
            phone=claim.phone,
            city=listing.city,
            trade_type=listing.trade_key,
        )
    except Exception as exc:  # noqa: BLE001 — surfaced to the admin as-is
        db.session.rollback()
        logger.exception("Claim approval failed for SIREN %s", listing.siren)
        return False, f"Création du compte impossible : {exc}"

    attach(listing.siren, tenant.id)
    claim.status = STATUS_APPROVED
    claim.decided_at = utcnow()
    claim.decision_note = (note or "")[:2000] or None
    claim.created_tenant_id = tenant.id
    db.session.commit()

    sent = _send_welcome(claim, tenant)
    suffix = "" if sent else " (e-mail non envoyé — à relayer à la main)"
    return True, f"Compte créé pour {claim.email}.{suffix}"


def reject(claim, *, note: str = "") -> None:
    from app.models.listing_claim import STATUS_REJECTED

    claim.status = STATUS_REJECTED
    claim.decided_at = utcnow()
    claim.decision_note = (note or "")[:2000] or None
    db.session.commit()


def _send_welcome(claim, tenant) -> bool:
    """Mail the artisan a link to set their password. Best-effort."""
    try:
        from flask import url_for

        from app.utils.seo import site_base_url

        link = site_base_url() + url_for("web.forgot_password")
    except Exception:  # noqa: BLE001
        link = "https://www.pilotcore.fr/forgot-password"

    try:
        from app.services import admin_email
        from app.services.transactional_email import render_email

        hello = f"Bonjour {claim.contact_name}," if claim.contact_name else "Bonjour,"
        html = render_email(
            f"Votre fiche {tenant.name} vous a été transférée",
            hello,
            kicker="Annuaire",
            lines=[
                f"Votre fiche « {tenant.name} » vous a été transférée sur PilotCore.",
                f"<strong>Identifiant :</strong> {claim.email}",
                "Définissez votre mot de passe pour publier vos coordonnées, "
                "votre zone d'intervention et activer la prise de rendez-vous en ligne.",
            ],
            cta_label="Définir mon mot de passe",
            cta_url=link,
        )
        text = "\n".join(
            [
                hello,
                "",
                f"Votre fiche « {tenant.name} » vous a été transférée sur PilotCore.",
                f"Définissez votre mot de passe : {link}",
                f"Identifiant : {claim.email}",
            ]
        )
        row = admin_email.send_email(
            claim.email,
            f"Votre fiche {tenant.name} vous a été transférée",
            text,
            is_html=True,
            html_body=html,
            tenant_id=tenant.id,
        )
        return bool(row)
    except Exception:  # noqa: BLE001
        logger.exception("Welcome email failed for claim %s", claim.id)
        return False


def pending_count() -> int:
    from app.models.listing_claim import STATUS_PENDING, ListingClaim

    return ListingClaim.query.filter_by(status=STATUS_PENDING).count()
