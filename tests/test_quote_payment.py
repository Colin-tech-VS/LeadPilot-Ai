"""Quote deposit payment — card (Stripe) + bank transfer."""

from types import SimpleNamespace
from unittest.mock import patch

from app.models.tenant import Tenant
from app.services import quote_payment


def _quote(deposit=100.0, paid=False):
    q = SimpleNamespace(
        deposit_amount=deposit if deposit else None,
        deposit_percent=30 if deposit else None,
        deposit_paid_at=None,
    )
    if paid:
        from datetime import datetime, timezone

        q.deposit_paid_at = datetime.now(timezone.utc)
    return q


def _tenant(iban=None, connect_ready=False):
    t = Tenant(name="Test Artisan")
    t.iban = iban
    t.stripe_connect_account_id = "acct_test" if connect_ready else None
    t.stripe_connect_charges_enabled = connect_ready
    return t


def test_payment_context_card_preferred_when_both_available(app):
    quote = _quote()
    tenant = _tenant(iban="FR7612345678901234567890123", connect_ready=True)
    with app.app_context():
        app.config["STRIPE_SECRET_KEY"] = "sk_test_x"
        with patch.object(quote_payment, "deposit_checkout_available", return_value=True):
            ctx = quote_payment.payment_context(quote, tenant)
    assert ctx["card_available"] is True
    assert ctx["wire_available"] is True
    assert ctx["default_method"] == "card"
    assert ctx["can_collect_deposit"] is True


def test_payment_context_wire_only_without_stripe():
    quote = _quote()
    tenant = _tenant(iban="FR7612345678901234567890123")
    with patch.object(quote_payment, "deposit_checkout_available", return_value=False):
        ctx = quote_payment.payment_context(quote, tenant)
    assert ctx["card_available"] is False
    assert ctx["wire_available"] is True
    assert ctx["default_method"] == "wire"


def test_payment_context_no_method_without_stripe_or_rib():
    quote = _quote()
    tenant = _tenant()
    with patch.object(quote_payment, "deposit_checkout_available", return_value=False):
        ctx = quote_payment.payment_context(quote, tenant)
    assert ctx["can_collect_deposit"] is False


def test_payment_context_no_deposit_always_collectable():
    quote = _quote(deposit=0)
    tenant = _tenant()
    with patch.object(quote_payment, "deposit_checkout_available", return_value=False):
        ctx = quote_payment.payment_context(quote, tenant)
    assert ctx["has_deposit"] is False
    assert ctx["can_collect_deposit"] is True


def test_deposit_required_matches_card_availability(app):
    quote = _quote()
    tenant = _tenant(connect_ready=True)
    with app.app_context():
        app.config["STRIPE_SECRET_KEY"] = "sk_test_x"
        with patch.object(quote_payment, "deposit_checkout_available", return_value=True):
            assert quote_payment.deposit_required(quote, tenant) is True
        with patch.object(quote_payment, "deposit_checkout_available", return_value=False):
            assert quote_payment.deposit_required(quote, tenant) is False
