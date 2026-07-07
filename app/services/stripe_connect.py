"""Stripe Connect Express — client deposit payments go to the artisan's account."""

import logging
import uuid

from flask import current_app

from app.core.extensions import db
from app.models.tenant import Tenant
from app.services.billing import _client, is_configured

logger = logging.getLogger(__name__)


def connect_available() -> bool:
    return is_configured()


def connect_ready(tenant: Tenant) -> bool:
    """True when the artisan can receive card deposits via Connect."""
    return bool(
        connect_available()
        and (tenant.stripe_connect_account_id or "").strip()
        and tenant.stripe_connect_charges_enabled
    )


def application_fee_cents(amount_cents: int) -> int:
    """Optional platform commission on client deposits (0 = artisan keeps full amount)."""
    pct = int(current_app.config.get("STRIPE_CONNECT_FEE_PERCENT", 0) or 0)
    if pct <= 0 or amount_cents <= 0:
        return 0
    return min(amount_cents, int(round(amount_cents * pct / 100)))


def ensure_connect_account(tenant: Tenant) -> str:
    """Create a Stripe Express account for the tenant if missing. Caller commits."""
    existing = (tenant.stripe_connect_account_id or "").strip()
    if existing:
        return existing

    email = None
    user = tenant.users.first() if tenant.users else None
    if user:
        email = user.email

    stripe = _client()
    account = stripe.Account.create(
        type="express",
        country="FR",
        email=email,
        capabilities={
            "card_payments": {"requested": True},
            "transfers": {"requested": True},
        },
        business_type="individual",
        metadata={"tenant_id": str(tenant.id)},
    )
    tenant.stripe_connect_account_id = account.id
    tenant.stripe_connect_charges_enabled = False
    logger.info("Stripe Connect account created tenant=%s account=%s", tenant.id, account.id)
    return account.id


def sync_connect_status(tenant: Tenant) -> bool:
    """Refresh charges_enabled from Stripe. Caller commits."""
    account_id = (tenant.stripe_connect_account_id or "").strip()
    if not account_id or not connect_available():
        return False
    stripe = _client()
    account = stripe.Account.retrieve(account_id)
    enabled = bool(account.get("charges_enabled"))
    tenant.stripe_connect_charges_enabled = enabled
    return enabled


def create_onboarding_link(tenant: Tenant, return_url: str, refresh_url: str) -> str:
    """Return a Stripe-hosted onboarding URL for the artisan."""
    if not connect_available():
        raise RuntimeError("Stripe is not configured")
    account_id = ensure_connect_account(tenant)
    db.session.flush()
    stripe = _client()
    link = stripe.AccountLink.create(
        account=account_id,
        refresh_url=refresh_url,
        return_url=return_url,
        type="account_onboarding",
    )
    return link.url


def handle_account_updated(account_obj: dict) -> bool:
    """Webhook: sync Connect account status when Stripe updates it."""
    account_id = account_obj.get("id")
    if not account_id:
        return False
    tenant = Tenant.query.filter_by(stripe_connect_account_id=account_id).first()
    if not tenant:
        return False
    tenant.stripe_connect_charges_enabled = bool(account_obj.get("charges_enabled"))
    db.session.commit()
    logger.info(
        "Stripe Connect account updated tenant=%s charges_enabled=%s",
        tenant.id,
        tenant.stripe_connect_charges_enabled,
    )
    return True


def tenant_for_account(account_id: str | None):
    if not account_id:
        return None
    return Tenant.query.filter_by(stripe_connect_account_id=account_id).first()
