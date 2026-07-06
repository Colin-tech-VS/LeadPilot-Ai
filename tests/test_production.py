from app.core.production import validate_production_config


def _production_app():
    from flask import Flask

    app = Flask(__name__)
    app.config.update(
        ENV="production",
        SECRET_KEY="real-secret",
        DATABASE_URL="postgresql://user:pass@localhost/db",
        SERVER_NAME="leadpilot-ai.osc-fr1.scalingo.io",
        ADMIN_PASSWORD="strong-admin-password",
        WEBHOOK_SECRET="webhook-secret",
        EMAIL_INBOUND_SECRET="inbound-secret",
        MISTRAL_API_KEY="mistral-key",
        TWILIO_AUTH_TOKEN="twilio-token",
    )
    return app


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
