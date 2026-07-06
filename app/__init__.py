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

import app.models  # noqa: F401 — register all ORM models

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

    from app.core.production import register_security_headers, validate_production_config

    register_security_headers(app)
    validate_production_config(app)

    from app.core.tracking import register_tracking

    register_tracking(app)

    with app.app_context():
        if app.config.get("ENV") != "production":
            db.create_all()
            _ensure_schema_updates()
        _backfill_lead_status()
        _backfill_completed_appointments()
        _backfill_directory_visibility()

    return app


def _backfill_directory_visibility():
    """Publish tenants who have a profile but were created before auto-listing."""
    try:
        from app.services.artisan_directory import backfill_directory_visibility

        n = backfill_directory_visibility()
        if n:
            logging.getLogger(__name__).info("Directory backfill: %s tenant(s) published", n)
    except Exception:
        logging.getLogger(__name__).exception("directory visibility backfill failed")


def _backfill_lead_status():
    """Self-heal leads left as "new" after a devis was accepted / a RDV booked.

    Older records (created before the booking flow promoted the lead) can sit at
    status "new" while already having an accepted devis and a scheduled RDV,
    which shows a misleading "en attente" badge. Promote them to "booked" once so
    the acceptance badge reflects reality. Idempotent and cheap: after the first
    run no rows match.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(db.engine)
    tables = set(inspector.get_table_names())
    if not {"leads", "appointments"} <= tables:
        return

    lead_cols = {c["name"] for c in inspector.get_columns("leads")}
    cancelled_guard = " AND cancelled_at IS NULL" if "cancelled_at" in lead_cols else ""

    conditions = [
        "id IN (SELECT lead_id FROM appointments "
        "WHERE lead_id IS NOT NULL AND status IN ('scheduled', 'confirmed'))"
    ]
    if "quotes" in tables:
        conditions.append(
            "id IN (SELECT lead_id FROM quotes "
            "WHERE lead_id IS NOT NULL AND doc_type = 'devis' AND status = 'accepted')"
        )

    sql = (
        "UPDATE leads SET status = 'booked' "
        "WHERE status = 'new' AND archived_at IS NULL" + cancelled_guard +
        " AND (" + " OR ".join(conditions) + ")"
    )
    try:
        with db.engine.begin() as conn:
            conn.execute(text(sql))
    except Exception:
        logging.getLogger(__name__).exception("lead status backfill failed")


def _backfill_completed_appointments():
    """Archived leads should not keep active RDV on the agenda."""
    from sqlalchemy import inspect, text

    inspector = inspect(db.engine)
    tables = set(inspector.get_table_names())
    if not {"leads", "appointments"} <= tables:
        return

    sql = (
        "UPDATE appointments SET status = 'completed' "
        "WHERE status IN ('scheduled', 'confirmed') "
        "AND lead_id IN (SELECT id FROM leads WHERE archived_at IS NOT NULL)"
    )
    try:
        with db.engine.begin() as conn:
            conn.execute(text(sql))
    except Exception:
        logging.getLogger(__name__).exception("completed appointments backfill failed")


def _ensure_schema_updates():
    """Lightweight schema patches for MVP (until Alembic)."""
    from sqlalchemy import inspect, text

    inspector = inspect(db.engine)
    if "leads" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("leads")}
    ts_type = "TIMESTAMP WITH TIME ZONE" if db.engine.dialect.name == "postgresql" else "DATETIME"
    lead_patches = {
        "email": "VARCHAR(255)",
        "booking_metadata": "TEXT",
        "latitude": "FLOAT",
        "longitude": "FLOAT",
        "cancelled_at": ts_type,
        "cancel_reason": "TEXT",
        "archived_at": ts_type,
    }
    for col_name, col_type in lead_patches.items():
        if col_name not in columns:
            with db.engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE leads ADD COLUMN {col_name} {col_type}"))

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
        "signature": "TEXT",
        "iban": "VARCHAR(40)",
        "bic": "VARCHAR(15)",
        "bank_holder": "VARCHAR(255)",
        "plan": "VARCHAR(20)",
        "trial_ends_at": ts_type,
        "stripe_customer_id": "VARCHAR(64)",
        "stripe_subscription_id": "VARCHAR(64)",
        "last_overage_period": "VARCHAR(7)",
        "trade_type": "VARCHAR(30)",
        "public_slug": "VARCHAR(100)",
        "is_public": "BOOLEAN",
        "public_blurb": "VARCHAR(500)",
    }
    for col_name, col_type in tenant_patches.items():
        if col_name not in tenant_columns:
            with db.engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE tenants ADD COLUMN {col_name} {col_type}"))

    try:
        with db.engine.begin() as conn:
            conn.execute(text("UPDATE tenants SET is_public = TRUE WHERE is_public IS NULL"))
            conn.execute(text("UPDATE tenants SET trade_type = 'plombier' WHERE trade_type IS NULL OR trade_type = ''"))
    except Exception:
        logging.getLogger(__name__).debug("tenant directory defaults patch skipped", exc_info=True)

    if "quotes" not in inspector.get_table_names():
        return
    quote_columns = {col["name"] for col in inspector.get_columns("quotes")}
    quote_patches = {
        "client_email": "VARCHAR(255)",
        "sent_channel": "VARCHAR(20)",
    }
    for col_name, col_type in quote_patches.items():
        if col_name not in quote_columns:
            with db.engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE quotes ADD COLUMN {col_name} {col_type}"))

    if "email_messages" not in inspector.get_table_names():
        return
    email_columns = {col["name"] for col in inspector.get_columns("email_messages")}
    email_patches = {
        "html_body": "TEXT",
        "cc_addrs": "VARCHAR(500)",
        "in_reply_to_id": "UUID",
        "rfc_in_reply_to": "VARCHAR(255)",
        "references_header": "TEXT",
        "imap_uid": "VARCHAR(64)",
        "imap_folder": "VARCHAR(64)",
        "attachments_json": "TEXT",
    }
    for col_name, col_type in email_patches.items():
        if col_name not in email_columns:
            with db.engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE email_messages ADD COLUMN {col_name} {col_type}"))

    if "users" not in inspector.get_table_names():
        return
    user_columns = {col["name"] for col in inspector.get_columns("users")}
    user_patches = {
        "first_name": "VARCHAR(100)",
        "last_name": "VARCHAR(100)",
        "phone": "VARCHAR(50)",
    }
    for col_name, col_type in user_patches.items():
        if col_name not in user_columns:
            with db.engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE users ADD COLUMN {col_name} {col_type}"))
