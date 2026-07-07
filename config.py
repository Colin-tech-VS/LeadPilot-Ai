import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")


def _normalize_database_url(url):
    """Supabase/Scalingo may provide postgres:// — SQLAlchemy requires postgresql://."""
    if url and url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


def _normalize_public_base_url(value, scheme="https"):
    if not value or not str(value).strip():
        return None
    url = str(value).strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        url = f"{scheme}://{url}"
    return url


class Config:
    """Base configuration."""

    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")
    DATABASE_URL = _normalize_database_url(os.environ.get("DATABASE_URL", ""))
    JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", SECRET_KEY)
    JWT_ALGORITHM = "HS256"
    JWT_EXPIRATION_HOURS = int(os.environ.get("JWT_EXPIRATION_HOURS", "24"))

    SQLALCHEMY_DATABASE_URI = DATABASE_URL
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
    }

    MISTRAL_API_KEY = os.environ.get("MISTRAL_API_KEY", "")
    MISTRAL_MODEL = os.environ.get("MISTRAL_MODEL", "mistral-small-latest")
    WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")

    # Stripe billing (subscriptions for the Starter / Pro / Premium plans). When
    # STRIPE_SECRET_KEY is unset the app runs exactly as before and the
    # billing pages show an "unavailable" notice instead of a checkout.
    STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
    STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
    STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    # Optional pre-created Stripe Price IDs. Left empty -> the app creates the
    # monthly prices on the fly via price_data.
    STRIPE_PRICE_STARTER = os.environ.get("STRIPE_PRICE_STARTER", "")
    STRIPE_PRICE_PRO = os.environ.get("STRIPE_PRICE_PRO", "")
    STRIPE_PRICE_PREMIUM = os.environ.get("STRIPE_PRICE_PREMIUM", "")
    # Price billed per call handled beyond the plan's monthly allowance, in euro
    # cents. Set this above your real marginal cost (Twilio + transcription +
    # LLM) to keep a margin. Default: 0,50 € / extra call.
    CALL_OVERAGE_PRICE_CENTS = int(os.environ.get("CALL_OVERAGE_PRICE_CENTS", "50"))

    # Voice pipeline (Whisper STT + OpenAI TTS — optional, text fallback supported)
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
    WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "whisper-1")
    TTS_MODEL = os.environ.get("TTS_MODEL", "tts-1")
    TTS_VOICE = os.environ.get("TTS_VOICE", "nova")
    # Twilio <Say> voice — Amazon Polly neural for a natural, human tone.
    TWILIO_VOICE = os.environ.get("TWILIO_VOICE", "Polly.Lea-Neural")
    # Twilio speech-recognition model. IMPORTANT: the legacy "phone_call" enhanced
    # model only supports English (en-US) — pairing it with French silently
    # cripples recognition ("l'IA n'a pas compris"). "googlev2" is multilingual
    # and transcribes natural French speech on phone audio, so it is the default.
    TWILIO_SPEECH_MODEL = os.environ.get("TWILIO_SPEECH_MODEL", "googlev2")
    TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
    TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
    TWILIO_DEFAULT_TENANT_ID = os.environ.get("TWILIO_DEFAULT_TENANT_ID", "")
    TWILIO_AI_PHONE_NUMBER = os.environ.get("TWILIO_AI_PHONE_NUMBER", "+33159169691")
    TWILIO_AI_PHONE_DISPLAY = os.environ.get("TWILIO_AI_PHONE_DISPLAY", "+33 1 59 16 96 91")
    # Sender number for outbound SMS (e.g. the signed-devis link texted to the
    # client). Falls back to the AI phone number. When Twilio is not configured
    # the app degrades gracefully — no SMS is sent and nothing breaks.
    TWILIO_SMS_FROM = os.environ.get("TWILIO_SMS_FROM", "")

    # Automatic per-tenant AI number provisioning. In a real multi-tenant setup
    # each plumber needs their OWN number: a phone call carries no login, so the
    # dialed number is the only way to know which tenant the caller wants. When
    # enabled (and Twilio is configured) a dedicated number is bought and wired
    # to the voice webhook automatically at signup — the plumber does nothing.
    # Set TWILIO_AUTO_PROVISION_NUMBERS=0 to disable (e.g. to avoid per-number
    # costs during testing); tenants then share TWILIO_AI_PHONE_NUMBER.
    TWILIO_AUTO_PROVISION_NUMBERS = os.environ.get("TWILIO_AUTO_PROVISION_NUMBERS", "1") not in ("0", "false", "False", "")
    # Country (ISO-3166 alpha-2) the AI numbers are purchased in, and an optional
    # preferred area/regional code (e.g. "1" for Paris local numbers).
    TWILIO_NUMBER_COUNTRY = os.environ.get("TWILIO_NUMBER_COUNTRY", "FR")
    TWILIO_NUMBER_AREA_CODE = os.environ.get("TWILIO_NUMBER_AREA_CODE", "")

    # Validate incoming Twilio webhook signatures (X-Twilio-Signature). Enabled
    # by default in production — it stops anyone but Twilio from hitting the
    # voice endpoints (which cost money: STT + LLM + TTS per call). Requires
    # TWILIO_AUTH_TOKEN. Set TWILIO_VALIDATE_SIGNATURE=0 to disable (e.g. local).
    TWILIO_VALIDATE_SIGNATURE = os.environ.get("TWILIO_VALIDATE_SIGNATURE", "1") not in ("0", "false", "False", "")

    # ------------------------------------------------------------------ Admin
    # Standalone admin console (/admin), fully separate from the artisan app.
    # The username is not secret. The password is read from ADMIN_PASSWORD
    # (plaintext, preferred in prod) or ADMIN_PASSWORD_HASH. A default hash ships
    # so the console works out of the box; override it in production.
    ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "PilotCore_Admin")
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
    ADMIN_PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH", "")

    # ------------------------------------------------------------------ Email
    # Outbound email (admin console + system notices). Sent over SMTP when
    # configured; otherwise logged/simulated so nothing breaks. Inbound email is
    # received via a provider webhook (Mailgun/SendGrid) at /admin/email/inbound,
    # guarded by EMAIL_INBOUND_SECRET.
    SMTP_HOST = os.environ.get("SMTP_HOST", "")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
    SMTP_USER = os.environ.get("SMTP_USER", "")
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
    SMTP_USE_SSL = os.environ.get("SMTP_USE_SSL", "1") not in ("0", "false", "False", "")
    SMTP_USE_TLS = os.environ.get("SMTP_USE_TLS", "0") not in ("0", "false", "False", "")
    EMAIL_FROM = os.environ.get("EMAIL_FROM", "contact@pilotcore.fr")
    EMAIL_INBOUND_SECRET = os.environ.get("EMAIL_INBOUND_SECRET", "")

    # IMAP — réception boîte LWS (mail.pilotcore.fr:993)
    IMAP_HOST = os.environ.get("IMAP_HOST", "")
    IMAP_PORT = int(os.environ.get("IMAP_PORT", "993"))
    IMAP_USER = os.environ.get("IMAP_USER", "")
    IMAP_PASSWORD = os.environ.get("IMAP_PASSWORD", "")
    IMAP_USE_SSL = os.environ.get("IMAP_USE_SSL", "1") not in ("0", "false", "False", "")
    IMAP_FOLDER = os.environ.get("IMAP_FOLDER", "INBOX")

    # Canonical public URL (https://www.example.com) for Twilio webhooks and links.
    # Accepts PUBLIC_BASE_URL or legacy SERVER_NAME; never passed to Flask as
    # SERVER_NAME — that would 404 every request whose Host header does not match.
    PREFERRED_URL_SCHEME = os.environ.get("PREFERRED_URL_SCHEME", "https")
    PUBLIC_BASE_URL = _normalize_public_base_url(
        os.environ.get("PUBLIC_BASE_URL") or os.environ.get("SERVER_NAME"),
        scheme=PREFERRED_URL_SCHEME,
    )
    SERVER_NAME = None

    # Google Places Autocomplete (city fields). Restrict the key by HTTP referrer
    # in Google Cloud Console (Maps JavaScript API + Places API).
    GOOGLE_PLACES_API_KEY = os.environ.get("GOOGLE_PLACES_API_KEY", "")

    # Google Search Console (OAuth admin dashboard at /admin/gsc)
    GOOGLE_GSC_CLIENT_ID = os.environ.get("GOOGLE_GSC_CLIENT_ID", "")
    GOOGLE_GSC_CLIENT_SECRET = os.environ.get("GOOGLE_GSC_CLIENT_SECRET", "")


class DevelopmentConfig(Config):
    """Development configuration."""

    DEBUG = True
    ENV = "development"
    SQLALCHEMY_DATABASE_URI = Config.DATABASE_URL or "sqlite:///PilotCore_dev.db"
    # Dev-only fallback so /admin works without extra env vars locally.
    ADMIN_PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH") or (
        ""
        if os.environ.get("ADMIN_PASSWORD")
        else "scrypt:32768:8:1$iYS5rlPR2M5YlS8z$5f4ffac38bb021d9a80ad86495f6c581fab7631596ef6f57fc54491b41bbf2c0a549eb6e39132ae74f182ac2caec3a0f075c1d5173ffa1baead954cfe44e1154"
    )


class ProductionConfig(Config):
    """Production configuration."""

    DEBUG = False
    ENV = "production"
    PREFERRED_URL_SCHEME = "https"
    # Harden the session cookie in production (admin + artisan sessions).
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"


config_by_name = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig,
}


def get_config():
    env = os.environ.get("FLASK_ENV", "development")
    return config_by_name.get(env, DevelopmentConfig)
