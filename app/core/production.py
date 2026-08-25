"""Production startup checks and security helpers."""
import os
import secrets

from flask import current_app


def generate_secret(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def is_production(app=None) -> bool:
    app = app or current_app
    return app.config.get("ENV") == "production" or os.environ.get("FLASK_ENV") == "production"


def live_provider_spend_allowed(app=None) -> bool:
    """Bill Twilio / OpenAI / Places only on the real production app.

    Pytest and local ``python main.py`` (even when ``.env`` holds live keys)
    must never buy numbers, send SMS, or call billed Google/OpenAI APIs.
    Set ``LIVE_PROVIDER_SPEND=0`` on Scalingo to freeze spend there too.
    """
    app = app or current_app
    if app.config.get("TESTING"):
        return False
    if not is_production(app):
        return False
    flag = str(os.environ.get("LIVE_PROVIDER_SPEND") or app.config.get("LIVE_PROVIDER_SPEND") or "1")
    return flag.lower() not in ("0", "false", "no", "off")


def validate_production_config(app) -> None:
    """Fail fast on boot when critical production settings are missing."""
    if not is_production(app):
        return

    errors = []
    secret = app.config.get("SECRET_KEY", "")
    if not secret or secret == "dev-secret-change-in-production":
        errors.append("SECRET_KEY must be set to a strong random value")

    if not app.config.get("DATABASE_URL"):
        errors.append("DATABASE_URL must point to PostgreSQL (Supabase pooler)")

    if not app.config.get("PUBLIC_BASE_URL"):
        errors.append(
            "PUBLIC_BASE_URL must be set (e.g. https://www.pilotcore.fr)"
        )

    admin_pw = app.config.get("ADMIN_PASSWORD", "")
    admin_hash = app.config.get("ADMIN_PASSWORD_HASH", "")
    if not admin_pw and not admin_hash:
        errors.append("ADMIN_PASSWORD must be set (default admin hash disabled in production)")

    if not app.config.get("WEBHOOK_SECRET"):
        errors.append("WEBHOOK_SECRET must be set")

    if not app.config.get("EMAIL_INBOUND_SECRET"):
        errors.append("EMAIL_INBOUND_SECRET must be set")

    if app.config.get("STRIPE_SECRET_KEY") and not app.config.get("STRIPE_WEBHOOK_SECRET"):
        sk = app.config.get("STRIPE_SECRET_KEY", "")
        if sk.startswith("sk_live_"):
            errors.append("STRIPE_WEBHOOK_SECRET required when STRIPE_SECRET_KEY is live")

    if not app.config.get("MISTRAL_API_KEY"):
        errors.append("MISTRAL_API_KEY must be set")

    if not app.config.get("TWILIO_AUTH_TOKEN"):
        errors.append("TWILIO_AUTH_TOKEN must be set for voice webhooks")

    if errors:
        raise RuntimeError(
            "Production configuration incomplete:\n- " + "\n- ".join(errors)
        )


def register_security_headers(app) -> None:
    @app.after_request
    def _security_headers(response):
        if is_production(app):
            response.headers.setdefault("X-Content-Type-Options", "nosniff")
            response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
            response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response
