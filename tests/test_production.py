from app.core.production import validate_production_config


def _production_app():
    from flask import Flask

    app = Flask(__name__)
    app.config.update(
        ENV="production",
        SECRET_KEY="real-secret",
        DATABASE_URL="postgresql://user:pass@localhost/db",
        PUBLIC_BASE_URL="https://www.pilotcore.fr",
        ADMIN_PASSWORD="strong-admin-password",
        WEBHOOK_SECRET="webhook-secret",
        EMAIL_INBOUND_SECRET="inbound-secret",
        MISTRAL_API_KEY="mistral-key",
        TWILIO_AUTH_TOKEN="twilio-token",
    )
    return app


def test_address_autocomplete_needs_no_api_key(app):
    """Autocomplete ships on every public page; the Places key never does.

    The browser talks to our origin. Google (or BAN as fallback) is called
    server-side, so the page must load the script whether or not a Places key
    happens to be configured."""
    app.config["GOOGLE_PLACES_API_KEY"] = "test-places-key"
    with app.test_client() as client:
        response = client.get("/")
        assert response.status_code == 200
        assert b"address-autocomplete.js" in response.data
        assert b"api-adresse.data.gouv.fr" in response.data
        assert b"test-places-key" not in response.data


def test_production_config_ok_when_complete():
    validate_production_config(_production_app())


def test_production_config_fails_without_secrets():
    app = _production_app()
    app.config["SECRET_KEY"] = "dev-secret-change-in-production"
    try:
        validate_production_config(app)
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "SECRET_KEY" in str(exc)


def test_production_config_requires_stripe_webhook_when_stripe_live():
    app = _production_app()
    app.config["STRIPE_SECRET_KEY"] = "sk_live_xxx"
    app.config["STRIPE_WEBHOOK_SECRET"] = ""
    try:
        validate_production_config(app)
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "STRIPE_WEBHOOK_SECRET" in str(exc)


def test_production_config_allows_stripe_test_without_webhook_secret():
    app = _production_app()
    app.config["STRIPE_SECRET_KEY"] = "sk_test_xxx"
    app.config["STRIPE_WEBHOOK_SECRET"] = ""
    validate_production_config(app)
