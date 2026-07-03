import os


def _normalize_database_url(url):
    """Supabase/Scalingo may provide postgres:// — SQLAlchemy requires postgresql://."""
    if url and url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
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

    # Voice pipeline (Whisper STT + OpenAI TTS — optional, text fallback supported)
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
    WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "whisper-1")
    TTS_MODEL = os.environ.get("TTS_MODEL", "tts-1")
    TTS_VOICE = os.environ.get("TTS_VOICE", "nova")
    # Twilio <Say> voice — Amazon Polly neural for a natural, human tone.
    TWILIO_VOICE = os.environ.get("TWILIO_VOICE", "Polly.Lea-Neural")
    TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
    TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
    TWILIO_DEFAULT_TENANT_ID = os.environ.get("TWILIO_DEFAULT_TENANT_ID", "")
    TWILIO_AI_PHONE_NUMBER = os.environ.get("TWILIO_AI_PHONE_NUMBER", "+33159169691")
    TWILIO_AI_PHONE_DISPLAY = os.environ.get("TWILIO_AI_PHONE_DISPLAY", "+33 1 59 16 96 91")

    # Public URL for Twilio webhooks (Scalingo: your-app.osc-fr1.scalingo.io)
    # Must be None (not "") when unset — an empty string makes Flask host-match
    # against "" and 404 every route.
    SERVER_NAME = os.environ.get("SERVER_NAME") or None
    PREFERRED_URL_SCHEME = os.environ.get("PREFERRED_URL_SCHEME", "https")


class DevelopmentConfig(Config):
    """Development configuration."""

    DEBUG = True
    ENV = "development"
    SQLALCHEMY_DATABASE_URI = Config.DATABASE_URL or "sqlite:///leadpilot_dev.db"


class ProductionConfig(Config):
    """Production configuration."""

    DEBUG = False
    ENV = "production"
    PREFERRED_URL_SCHEME = "https"


config_by_name = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig,
}


def get_config():
    env = os.environ.get("FLASK_ENV", "development")
    return config_by_name.get(env, DevelopmentConfig)
