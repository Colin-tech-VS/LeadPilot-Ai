"""Stripe Checkout for client devis deposit (acompte) payments."""

import logging
import uuid
from datetime import datetime, timezone

from flask import current_app, url_for

from app.core.extensions import db
from app.models.quote import Quote, STATUS_ACCEPTED
from app.services import quote_engine

logger = logging.getLogger(__name__)

CURRENCY = "eur"
MIN_DEPOSIT_CENTS = 50


def utcnow():
    return datetime.now(timezone.utc)


def deposit_checkout_available() -> bool:
    from app.services.billing import is_configured

    return is_configured()


def deposit_required(quote: Quote) -> bool:
    amount = quote.deposit_amount
    if not amount or amount <= 0:
        return False
    if quote.deposit_paid_at:
        return False
    if not deposit_checkout_available():
        return False
    return int(round(amount * 100)) >= MIN_DEPOSIT_CENTS


def _stripe():
    import stripe

    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]
    return stripe


def create_deposit_session(quote: Quote, success_url: str, cancel_url: str) -> str:
    """Create a one-time Checkout session for the devis acompte. Caller commits."""
    if not deposit_checkout_available():
        raise RuntimeError("Stripe is not configured")
    amount_cents = int(round((quote.deposit_amount or 0) * 100))
    if amount_cents < MIN_DEPOSIT_CENTS:
        raise ValueError("Deposit amount too small for card payment")

    stripe = _stripe()
    session = stripe.checkout.Session.create(
        mode="payment",
        line_items=[
            {
                "quantity": 1,
                "price_data": {
                    "currency": CURRENCY,
                    "unit_amount": amount_cents,
                    "product_data": {
                        "name": f"Acompte devis {quote.number or ''}".strip(),
                        "description": (
                            f"Acompte ({quote.deposit_percent} %) — "
                            f"signature et confirmation du rendez-vous"
                        ),
                    },
                },
            }
        ],
        customer_email=(quote.client_email or "").strip() or None,
        metadata={
            "kind": "quote_deposit",
            "quote_id": str(quote.id),
            "tenant_id": str(quote.tenant_id),
        },
        success_url=success_url,
        cancel_url=cancel_url,
    )
    quote.stripe_deposit_session_id = session.id
    return session.url


def deposit_success_url(quote: Quote, token: str) -> str:
    return url_for(
        "quotes.deposit_success",
        quote_id=quote.id,
        token=token,
        _external=True,
    ) + "?session_id={CHECKOUT_SESSION_ID}"


def deposit_cancel_url(quote: Quote, token: str) -> str:
    return url_for("quotes.public_quote", quote_id=quote.id, token=token, _external=True)


def mark_deposit_paid(quote: Quote, session_id: str | None = None) -> bool:
    """Record deposit payment. Returns True when newly marked."""
    if quote.deposit_paid_at:
        return False
    quote.deposit_paid_at = utcnow()
    if session_id:
        quote.stripe_deposit_session_id = session_id
    return True


def finalize_after_deposit(quote: Quote) -> dict:
    """Accept the devis once deposit is paid and client has signed."""
    if quote.status == STATUS_ACCEPTED:
        return {"already": True, "appointment": None, "invoice": None}
    if not quote.client_signed_name:
        return {"already": False, "appointment": None, "invoice": None}
    return quote_engine.accept_quote(quote)


def verify_session_and_finalize(session_id: str | None, quote: Quote) -> dict:
    """Verify Stripe session after redirect and complete acceptance."""
    if not session_id or not deposit_checkout_available():
        return {"paid": False, "accepted": False}

    stripe = _stripe()
    try:
        session = stripe.checkout.Session.retrieve(session_id)
    except Exception:
        logger.exception("Stripe session retrieve failed quote=%s", quote.id)
        return {"paid": False, "accepted": False}

    meta = session.get("metadata") or {}
    if meta.get("kind") != "quote_deposit" or meta.get("quote_id") != str(quote.id):
        return {"paid": False, "accepted": False}
    if session.get("payment_status") != "paid":
        return {"paid": False, "accepted": False}

    mark_deposit_paid(quote, session_id)
    result = finalize_after_deposit(quote)
    db.session.commit()
    return {"paid": True, "accepted": quote.status == STATUS_ACCEPTED, "result": result}


def complete_deposit_checkout(session_obj: dict) -> bool:
    """Stripe webhook handler for checkout.session.completed (quote deposit)."""
    meta = session_obj.get("metadata") or {}
    if meta.get("kind") != "quote_deposit":
        return False

    quote_id = meta.get("quote_id")
    if not quote_id:
        return False
    try:
        qid = uuid.UUID(str(quote_id))
    except (ValueError, TypeError):
        return False

    quote = db.session.get(Quote, qid)
    if not quote:
        logger.warning("quote_deposit webhook: unknown quote %s", quote_id)
        return False

    if session_obj.get("payment_status") != "paid":
        return False

    mark_deposit_paid(quote, session_obj.get("id"))
    finalize_after_deposit(quote)
    db.session.commit()
    logger.info("Quote deposit paid quote=%s session=%s", quote.id, session_obj.get("id"))
    return True
