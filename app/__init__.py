import logging
import sys
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask

from app.core.errors import register_error_handlers
from app.core.extensions import db
from app.core.i18n import register_i18n
from app.routes import register_blueprints
from config import get_config

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def configure_logging(app):
    """Configure stdout logging for 12-factor deployment."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s [%(name)s] %(message)s"
        )
    )
    app.logger.handlers.clear()
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.INFO if not app.debug else logging.DEBUG)


def create_app(config_object=None):
    app = Flask(
        __name__,
        template_folder=str(BASE_DIR / "templates"),
        static_folder=str(BASE_DIR / "static"),
    )

    if config_object is None:
        config_object = get_config()
    app.config.from_object(config_object)

    configure_logging(app)
    db.init_app(app)
    register_error_handlers(app)
    register_i18n(app)
    register_blueprints(app)

    with app.app_context():
        db.create_all()
        _ensure_schema_updates()

    return app


def _ensure_schema_updates():
    """Lightweight schema patches for MVP (until Alembic)."""
    from sqlalchemy import inspect, text

    inspector = inspect(db.engine)
    if "leads" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("leads")}
    if "booking_metadata" not in columns:
        with db.engine.begin() as conn:
            conn.execute(text("ALTER TABLE leads ADD COLUMN booking_metadata TEXT"))
    if "latitude" not in columns:
        with db.engine.begin() as conn:
            conn.execute(text("ALTER TABLE leads ADD COLUMN latitude FLOAT"))
    if "longitude" not in columns:
        with db.engine.begin() as conn:
            conn.execute(text("ALTER TABLE leads ADD COLUMN longitude FLOAT"))
    if "archived_at" not in columns:
        ts_type = "TIMESTAMP WITH TIME ZONE" if db.engine.dialect.name == "postgresql" else "DATETIME"
        with db.engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE leads ADD COLUMN archived_at {ts_type}"))

    if "tenants" not in inspector.get_table_names():
        return
    tenant_columns = {col["name"] for col in inspector.get_columns("tenants")}
    ts_type = "TIMESTAMP WITH TIME ZONE" if db.engine.dialect.name == "postgresql" else "DATETIME"
    tenant_patches = {
        "first_name": "VARCHAR(100)",
        "last_name": "VARCHAR(100)",
        "ai_assistant_name": "VARCHAR(100)",
        "siret": "VARCHAR(14)",
        "ai_phone_number": "VARCHAR(50)",
        "address": "VARCHAR(500)",
        "postal_code": "VARCHAR(10)",
        "city": "VARCHAR(100)",
        "latitude": "FLOAT",
        "longitude": "FLOAT",
        "service_radius_km": "INTEGER",
        "plan": "VARCHAR(20)",
        "trial_ends_at": ts_type,
        "stripe_customer_id": "VARCHAR(64)",
        "stripe_subscription_id": "VARCHAR(64)",
    }
    for col_name, col_type in tenant_patches.items():
        if col_name not in tenant_columns:
            with db.engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE tenants ADD COLUMN {col_name} {col_type}"))
