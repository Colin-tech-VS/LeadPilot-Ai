"""Stripe Connect — deposit routing to artisan accounts."""

from unittest.mock import MagicMock, patch

from app.models.tenant import Tenant
from app.services import quote_payment, stripe_connect


def _tenant(connect_id="acct_test", charges_enabled=True, iban=None):
    t = Tenant(name="Test Artisan")
    t.stripe_connect_account_id = connect_id
    t.stripe_connect_charges_enabled = charges_enabled
    t.iban = iban
    return t


def test_connect_ready_requires_charges_enabled():
    tenant = _tenant(charges_enabled=False)
    with patch.object(stripe_connect, "connect_available", return_value=True):
        assert stripe_connect.connect_ready(tenant) is False
    tenant.stripe_connect_charges_enabled = True
    with patch.object(stripe_connect, "connect_available", return_value=True):
        assert stripe_connect.connect_ready(tenant) is True


def test_application_fee_zero_by_default(app):
    with app.app_context():
        assert stripe_connect.application_fee_cents(10000) == 0


def test_application_fee_percent(app):
    with app.app_context():
        app.config["STRIPE_CONNECT_FEE_PERCENT"] = 10
        assert stripe_connect.application_fee_cents(10000) == 1000


def test_card_deposit_requires_connect(app):
    from types import SimpleNamespace

    quote = SimpleNamespace(deposit_amount=100.0, deposit_paid_at=None)
    tenant = _tenant(charges_enabled=False, iban="FR7612345678901234567890123")
    with app.app_context():
        with patch.object(quote_payment, "deposit_checkout_available", return_value=True):
            ctx = quote_payment.payment_context(quote, tenant)
    assert ctx["card_available"] is False
    assert ctx["wire_available"] is True

    tenant.stripe_connect_charges_enabled = True
    with app.app_context():
        app.config["STRIPE_SECRET_KEY"] = "sk_test_x"
        with patch.object(quote_payment, "deposit_checkout_available", return_value=True):
            ctx = quote_payment.payment_context(quote, tenant)
    assert ctx["card_available"] is True


def test_handle_account_updated():
    tenant = _tenant(charges_enabled=False)
    with patch("app.services.stripe_connect.Tenant") as TenantModel:
        TenantModel.query.filter_by.return_value.first.return_value = tenant
        with patch("app.services.stripe_connect.db") as mock_db:
            ok = stripe_connect.handle_account_updated(
                {"id": "acct_test", "charges_enabled": True}
            )
    assert ok is True
    assert tenant.stripe_connect_charges_enabled is True
    mock_db.session.commit.assert_called_once()
