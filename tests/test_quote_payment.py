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


def _tenant(iban=None):
    t = Tenant(name="Test Artisan")
    t.iban = iban
    return t


def test_payment_context_card_preferred_when_both_available():
    quote = _quote()
    tenant = _tenant(iban="FR7612345678901234567890123")
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


def test_deposit_required_matches_card_availability():
    quote = _quote()
    with patch.object(quote_payment, "deposit_checkout_available", return_value=True):
        assert quote_payment.deposit_required(quote) is True
    with patch.object(quote_payment, "deposit_checkout_available", return_value=False):
        assert quote_payment.deposit_required(quote) is False
