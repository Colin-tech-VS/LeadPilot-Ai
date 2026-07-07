"""Quote delivery — email/SMS send from artisan dashboard."""

from app.models.quote import Quote
from app.models.tenant import Tenant
from app.services import quote_delivery, quote_engine


def test_resolve_channels_falls_back_to_contacts():
    quote = Quote(client_email="a@b.com", client_phone="0612345678")
    assert quote_delivery.resolve_channels(quote, []) == ["email", "sms"]
    assert quote_delivery.resolve_channels(quote, ["email"]) == ["email"]


def test_send_quote_email_simulated(app):
    with app.app_context():
        tenant = Tenant.query.first()
        quote = quote_engine.build_draft_from_lead(None, tenant)
        quote.number = "DEV-TEST-SEND"
        quote.client_email = "client@example.com"
        quote.client_name = "Jean Dupont"

        with app.test_request_context(base_url="https://www.pilotcore.fr"):
            result = quote_delivery.send_quote(quote, tenant, channels=["email"])

        assert result["any"] is True
        assert result["email"] is True
        assert result["channel"] == "email"
        assert quote.public_token


def test_send_quote_no_contact(app):
    with app.app_context():
        tenant = Tenant.query.first()
        quote = quote_engine.build_draft_from_lead(None, tenant)
        quote.number = "DEV-TEST-NO"

        with app.test_request_context(base_url="https://www.pilotcore.fr"):
            result = quote_delivery.send_quote(quote, tenant)

        assert result["any"] is False
        assert result["error"] == "no_contact"
