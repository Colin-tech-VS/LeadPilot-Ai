"""Stripe billing for the Starter / Pro / Premium subscription plans.

Everything here is guarded by ``is_configured()``: when ``STRIPE_SECRET_KEY``
is absent the app keeps working exactly as before (free trial only) and the
billing UI shows an "unavailable" notice instead of a checkout button.
"""
import logging
from datetime import datetime, timezone

from flask import current_app

from app.core.extensions import db
from app.models.tenant import Tenant

logger = logging.getLogger(__name__)

# Monthly plans, in euro cents. Kept in sync with the landing pricing section.
# ``included_calls`` is the monthly call allowance surfaced on the pricing grid;
# calls handled beyond it are billed as usage (overage).
PLANS = {
    "starter": {
        "name": "Starter",
        "amount": 14900,
        "price_config_key": "STRIPE_PRICE_STARTER",
        "included_calls": 150,
    },
    "pro": {
        "name": "Pro",
        "amount": 34900,
        "price_config_key": "STRIPE_PRICE_PRO",
        "included_calls": 500,
    },
    "premium": {
        "name": "Premium",
        "amount": 69900,
        "price_config_key": "STRIPE_PRICE_PREMIUM",
        "included_calls": 1500,
    },
}
CURRENCY = "eur"


def is_configured() -> bool:
    return bool(current_app.config.get("STRIPE_SECRET_KEY"))


def available_plans() -> dict:
    return PLANS


def included_calls(plan_key: str):
    """Monthly call allowance for a plan, or None for the free trial / unknown."""
    plan = PLANS.get(plan_key)
    return plan.get("included_calls") if plan else None


def monthly_call_usage(tenant) -> int:
    """Calls handled for this tenant since the start of the current calendar
    month. Each qualified inbound call creates a Lead, so we use that as the
    usage signal shown against the plan's included-call allowance."""
    from app.models.lead import Lead

    start = datetime.now(timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    return Lead.query.filter(
        Lead.tenant_id == tenant.id, Lead.created_at >= start
    ).count()


def _client():
    import stripe

    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]
    return stripe


def _line_item(plan_key: str) -> dict:
    """Use a pre-created Price when the plumber configured one, otherwise build
    the monthly price on the fly so only the secret key is needed."""
    plan = PLANS[plan_key]
    price_id = current_app.config.get(plan["price_config_key"])
    if price_id:
        return {"price": price_id, "quantity": 1}
    return {
        "quantity": 1,
        "price_data": {
            "currency": CURRENCY,
            "unit_amount": plan["amount"],
            "recurring": {"interval": "month"},
            "product_data": {"name": f"LeadPilot AI — {plan['name']}"},
        },
    }


def create_checkout_session(tenant: Tenant, plan_key: str, success_url: str, cancel_url: str) -> str:
    """Create a Stripe Checkout subscription session and return its URL."""
    if plan_key not in PLANS:
        raise ValueError(f"Unknown plan: {plan_key}")
    if not is_configured():
        raise RuntimeError("Stripe is not configured")

    stripe = _client()
    email = None
    user = tenant.users.first() if tenant.users else None
    if user:
        email = user.email

    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[_line_item(plan_key)],
        client_reference_id=str(tenant.id),
        customer=tenant.stripe_customer_id or None,
        customer_email=None if tenant.stripe_customer_id else email,
        metadata={"tenant_id": str(tenant.id), "plan": plan_key},
        subscription_data={"metadata": {"tenant_id": str(tenant.id), "plan": plan_key}},
        success_url=success_url,
        cancel_url=cancel_url,
        allow_promotion_codes=True,
    )
    return session.url


def handle_webhook(payload: bytes, signature: str) -> bool:
    """Verify a Stripe webhook and apply it. Returns True when handled."""
    if not is_configured():
        return False
    stripe = _client()
    secret = current_app.config.get("STRIPE_WEBHOOK_SECRET")
    if secret:
        event = stripe.Webhook.construct_event(payload, signature, secret)
    else:
        # No signing secret configured — fall back to parsing (dev only).
        import json

        event = json.loads(payload)
        logger.warning("Stripe webhook received without signature verification")

    return apply_event(event.get("type"), (event.get("data") or {}).get("object") or {})


def apply_event(event_type: str, obj: dict) -> bool:
    """Apply a parsed Stripe event to the tenant's plan. Pure DB logic, kept
    separate from signature handling so it can be unit-tested."""
    if event_type == "checkout.session.completed":
        tenant_id = str((obj.get("metadata") or {}).get("tenant_id") or obj.get("client_reference_id") or "")
        plan = (obj.get("metadata") or {}).get("plan")
        tenant = _get_tenant(tenant_id)
        if not tenant or plan not in PLANS:
            return False
        tenant.plan = plan
        if obj.get("customer"):
            tenant.stripe_customer_id = obj["customer"]
        if obj.get("subscription"):
            tenant.stripe_subscription_id = obj["subscription"]
        db.session.commit()
        logger.info("Tenant %s upgraded to plan=%s via Stripe", tenant_id, plan)
        return True

    if event_type in ("customer.subscription.deleted", "customer.subscription.canceled"):
        tenant = _tenant_by_subscription(obj.get("id"), obj.get("customer"))
        if not tenant:
            return False
        tenant.plan = "trial"
        db.session.commit()
        logger.info("Tenant %s subscription ended — reverted to trial", tenant.id)
        return True

    if event_type == "customer.subscription.updated":
        status = obj.get("status")
        tenant = _tenant_by_subscription(obj.get("id"), obj.get("customer"))
        if not tenant:
            return False
        if status in ("canceled", "unpaid", "incomplete_expired"):
            tenant.plan = "trial"
            db.session.commit()
            logger.info("Tenant %s subscription %s — reverted to trial", tenant.id, status)
            return True
        return False

    return False


def _get_tenant(tenant_id: str):
    import uuid

    if not tenant_id:
        return None
    try:
        return db.session.get(Tenant, uuid.UUID(tenant_id))
    except (ValueError, TypeError):
        return None


def _tenant_by_subscription(subscription_id: str | None, customer_id: str | None):
    query = Tenant.query
    if subscription_id:
        found = query.filter_by(stripe_subscription_id=subscription_id).first()
        if found:
            return found
    if customer_id:
        return query.filter_by(stripe_customer_id=customer_id).first()
    return None
