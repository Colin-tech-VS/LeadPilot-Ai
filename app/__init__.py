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
    _register_plan_context(app)

    from app.core.production import register_security_headers, validate_production_config

    register_security_headers(app)
    validate_production_config(app)

    from app.core.assets import register_asset_versioning
    from app.core.tracking import register_tracking

    register_asset_versioning(app)
    register_tracking(app)

    with app.app_context():
        if app.config.get("ENV") != "production":
            db.create_all()
        # Production skips ``create_all`` because schema is Alembic's job — but
        # this database predates the migration chain, whose first revision
        # recreates tables that already exist, so ``upgrade head`` cannot replay
        # and a newly added table never lands. Creating only the *missing*
        # tables is idempotent (it never touches an existing one) and keeps a
        # new feature from 500-ing on its first request after deploy.
        try:
            _ensure_missing_tables()
        except Exception:
            logging.getLogger(__name__).exception("table creation failed — app continues")
        # Columns the ORM declares and the database is missing, derived from the
        # models themselves so a new one cannot be forgotten. Must run in
        # production too when Alembic lags behind the ORM (otherwise every page
        # reading the table 500s after deploy).
        try:
            _sync_orm_columns()
        except Exception:
            logging.getLogger(__name__).exception("column sync failed — app continues")
        # Idempotent hand-written patches: data backfills and type fixes the
        # generic sweep above cannot express.
        try:
            _ensure_schema_updates()
        except Exception:
            logging.getLogger(__name__).exception("schema patch failed — app continues")
        _backfill_lead_status()
        _backfill_completed_appointments()
        _backfill_directory_visibility()
        try:
            from app.services.blog import (
                backfill_post_categories,
                ensure_blog_schema,
                ensure_default_categories,
            )

            ensure_blog_schema()
            ensure_default_categories()
            backfill_post_categories()
        except Exception:
            logging.getLogger(__name__).exception("blog schema/category seed failed")

    return app


def _register_plan_context(app):
    """Inject plan capabilities for logged-in artisans."""

    # The register stores names in uppercase; templates that surface them need a
    # cased form without each one re-implementing the rules.
    from app.utils.naming import display_name as _display_name

    app.jinja_env.filters["display_name"] = _display_name

    @app.context_processor
    def inject_plan_caps():
        from flask import g

        tenant_id = getattr(g, "tenant_id", None)
        if not tenant_id:
            return {}
        try:
            from app.models.tenant import Tenant
            from app.services.plan_features import plan_summary

            tenant = db.session.get(Tenant, tenant_id)
            if tenant:
                return {"plan_caps": plan_summary(tenant)}
        except Exception:
            pass
        return {}


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


def _ensure_missing_tables():
    """Create tables the ORM declares and the database does not have yet.

    Deliberately narrow: it adds absent tables and nothing else. Columns added
    to an existing table are still :func:`_ensure_schema_updates`'s job.
    """
    from sqlalchemy import inspect

    existing = set(inspect(db.engine).get_table_names())
    missing = [t for name, t in db.metadata.tables.items() if name not in existing]
    if not missing:
        return
    db.metadata.create_all(bind=db.engine, tables=missing)
    logging.getLogger(__name__).info(
        "Created missing tables: %s", ", ".join(sorted(t.name for t in missing))
    )


def _sync_orm_columns():
    """Add columns the ORM declares and the database does not have yet.

    The production database predates the Alembic chain: the first revision
    calls ``op.create_table`` on tables that already exist, so
    ``alembic upgrade head`` fails on revision one and *every* later revision
    stays unapplied. Until that is untangled, a column added to a model only
    reaches production if someone also remembers to list it by hand in
    :func:`_ensure_schema_updates` — and forgetting once takes down every page
    that reads the table (a missing ``tenants.listing_prompt_answered_at``
    turned /admin/clients, the dashboard and the sign-up into 500s).

    So derive the patch from the models instead of from a list kept in sync by
    hand. Only columns that can be added to a table that already holds rows are
    touched — nullable ones, or ones carrying a server default; anything else is
    logged and left to a real migration. The sweep is idempotent: it is guarded
    by the inspector and never rewrites or drops anything.
    """
    from sqlalchemy import inspect, text
    from sqlalchemy.schema import CreateColumn

    inspector = inspect(db.engine)
    existing_tables = set(inspector.get_table_names())
    dialect = db.engine.dialect
    preparer = dialect.identifier_preparer
    log = logging.getLogger(__name__)
    added = []

    for table_name, table in db.metadata.tables.items():
        if table_name not in existing_tables:
            continue  # whole tables are _ensure_missing_tables()'s job
        db_columns = {col["name"] for col in inspector.get_columns(table_name)}
        for column in table.columns:
            if column.name in db_columns:
                continue
            if column.primary_key or getattr(column, "computed", None) is not None:
                log.warning(
                    "%s.%s is missing and cannot be added automatically — "
                    "needs a migration",
                    table_name,
                    column.name,
                )
                continue
            if not column.nullable and column.server_default is None:
                log.warning(
                    "%s.%s is missing and is NOT NULL without a server default "
                    "— needs a migration",
                    table_name,
                    column.name,
                )
                continue
            ddl = CreateColumn(column).compile(dialect=dialect).string
            statement = f"ALTER TABLE {preparer.format_table(table)} ADD COLUMN {ddl}"
            try:
                with db.engine.begin() as conn:
                    conn.execute(text(statement))
                added.append(f"{table_name}.{column.name}")
            except Exception:
                log.exception("could not add column %s.%s", table_name, column.name)

    if added:
        log.info("Added missing columns: %s", ", ".join(sorted(added)))


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
        "stripe_connect_account_id": "VARCHAR(64)",
        "stripe_connect_charges_enabled": "BOOLEAN",
        "last_overage_period": "VARCHAR(7)",
        "trade_type": "VARCHAR(30)",
        "public_slug": "VARCHAR(100)",
        "is_public": "BOOLEAN",
        "public_blurb": "VARCHAR(500)",
        "show_direct_phone_public": "BOOLEAN",
    }
    for col_name, col_type in tenant_patches.items():
        if col_name not in tenant_columns:
            with db.engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE tenants ADD COLUMN {col_name} {col_type}"))

    try:
        with db.engine.begin() as conn:
            conn.execute(text("UPDATE tenants SET is_public = TRUE WHERE is_public IS NULL"))
            conn.execute(text("UPDATE tenants SET trade_type = 'plombier' WHERE trade_type IS NULL OR trade_type = ''"))
            conn.execute(text("UPDATE tenants SET show_direct_phone_public = FALSE WHERE show_direct_phone_public IS NULL"))
            conn.execute(text("UPDATE tenants SET stripe_connect_charges_enabled = FALSE WHERE stripe_connect_charges_enabled IS NULL"))
    except Exception:
        logging.getLogger(__name__).debug("tenant directory defaults patch skipped", exc_info=True)

    # Independent of quotes/users tables — do not skip behind later early returns.
    if "social_posts" in inspector.get_table_names():
        sp_columns = {col["name"] for col in inspector.get_columns("social_posts")}
        if "image_path" not in sp_columns:
            with db.engine.begin() as conn:
                conn.execute(text("ALTER TABLE social_posts ADD COLUMN image_path VARCHAR(300)"))
        if "target_key" not in sp_columns:
            with db.engine.begin() as conn:
                conn.execute(text("ALTER TABLE social_posts ADD COLUMN target_key VARCHAR(40)"))
        if "scheduled_for" not in sp_columns:
            with db.engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE social_posts ADD COLUMN scheduled_for {ts_type}"))
        if "image_blob" not in sp_columns:
            blob_type = "BYTEA" if db.engine.dialect.name == "postgresql" else "BLOB"
            with db.engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE social_posts ADD COLUMN image_blob {blob_type}"))

    if "quotes" not in inspector.get_table_names():
        return
    quote_columns = {col["name"] for col in inspector.get_columns("quotes")}
    quote_patches = {
        "client_email": "VARCHAR(255)",
        "sent_channel": "VARCHAR(20)",
        "client_signed_name": "VARCHAR(255)",
        "client_signed_at": ts_type,
        "deposit_paid_at": ts_type,
        "stripe_deposit_session_id": "VARCHAR(255)",
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
        "track_token": "VARCHAR(64)",
        "open_count": "INTEGER DEFAULT 0",
        "click_count": "INTEGER DEFAULT 0",
        "first_opened_at": ts_type,
        "last_opened_at": ts_type,
        "first_clicked_at": ts_type,
        "last_clicked_at": ts_type,
        "click_urls_json": "TEXT",
    }
    for col_name, col_type in email_patches.items():
        if col_name not in email_columns:
            with db.engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE email_messages ADD COLUMN {col_name} {col_type}"))

    if "voice_call_sessions" not in inspector.get_table_names():
        with db.engine.begin() as conn:
            conn.execute(
                text(
                    f"""
                    CREATE TABLE voice_call_sessions (
                        call_id VARCHAR(64) PRIMARY KEY,
                        tenant_id VARCHAR(64) NOT NULL,
                        caller_phone VARCHAR(50),
                        state_json TEXT NOT NULL,
                        updated_at {ts_type}
                    )
                    """
                )
            )

    if "users" not in inspector.get_table_names():
        return
    user_columns = {col["name"] for col in inspector.get_columns("users")}
    user_patches = {
        "first_name": "VARCHAR(100)",
        "last_name": "VARCHAR(100)",
        "phone": "VARCHAR(50)",
        "google_sub": "VARCHAR(64)",
    }
    for col_name, col_type in user_patches.items():
        if col_name not in user_columns:
            with db.engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE users ADD COLUMN {col_name} {col_type}"))

    if "page_views" in inspector.get_table_names():
        pv_columns = {col["name"] for col in inspector.get_columns("page_views")}
        pv_patches = {
            "geo_country_code": "VARCHAR(2)",
            "geo_country": "VARCHAR(80)",
            "geo_region": "VARCHAR(100)",
            "geo_city": "VARCHAR(100)",
            "geo_postal_code": "VARCHAR(20)",
            "geo_latitude": "FLOAT",
            "geo_longitude": "FLOAT",
            "utm_source": "VARCHAR(80)",
            "utm_medium": "VARCHAR(80)",
            "utm_campaign": "VARCHAR(120)",
            "utm_content": "VARCHAR(120)",
        }
        for col_name, col_type in pv_patches.items():
            if col_name not in pv_columns:
                with db.engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE page_views ADD COLUMN {col_name} {col_type}"))

    table_names = set(inspector.get_table_names())

    # ``outreach_prospects.id`` must be a native ``uuid`` column so that
    # ``db.session.get(OutreachProspect, uuid)`` (which binds the parameter as
    # ``::UUID`` on Postgres) matches. When the table pre-dates the Alembic
    # migration — the migration early-returns if the table already exists — the
    # column can linger as ``character varying``, which raises
    # "operator does not exist: character varying = uuid" on outreach email
    # generation. Normalise it in place (Postgres only; SQLite has no uuid type).
    if "outreach_prospects" in table_names and db.engine.dialect.name == "postgresql":
        id_col = next(
            (c for c in inspector.get_columns("outreach_prospects") if c["name"] == "id"),
            None,
        )
        if id_col is not None and "uuid" not in str(id_col["type"]).lower():
            try:
                with db.engine.begin() as conn:
                    conn.execute(
                        text(
                            "ALTER TABLE outreach_prospects "
                            "ALTER COLUMN id TYPE uuid USING id::uuid"
                        )
                    )
            except Exception:
                logging.getLogger(__name__).exception(
                    "outreach_prospects.id uuid normalisation failed"
                )

    if "heatmap_events" not in table_names:
        with db.engine.begin() as conn:
            conn.execute(
                text(
                    f"""
                    CREATE TABLE heatmap_events (
                        id UUID PRIMARY KEY,
                        visitor_id VARCHAR(40),
                        session_id VARCHAR(40),
                        event_type VARCHAR(20) NOT NULL DEFAULT 'click',
                        path VARCHAR(500),
                        x_ratio FLOAT,
                        y_px INTEGER,
                        vw INTEGER,
                        vh INTEGER,
                        doc_w INTEGER,
                        doc_h INTEGER,
                        scroll_depth INTEGER,
                        el_selector VARCHAR(300),
                        el_text VARCHAR(200),
                        device VARCHAR(20),
                        created_at {ts_type} NOT NULL
                    )
                    """
                )
            )
            for col in ("visitor_id", "session_id", "event_type", "path", "created_at"):
                conn.execute(
                    text(
                        f"CREATE INDEX IF NOT EXISTS ix_heatmap_events_{col} "
                        f"ON heatmap_events ({col})"
                    )
                )

    if "session_recordings" not in table_names:
        with db.engine.begin() as conn:
            conn.execute(
                text(
                    f"""
                    CREATE TABLE session_recordings (
                        rec_id VARCHAR(40) PRIMARY KEY,
                        visitor_id VARCHAR(40),
                        session_id VARCHAR(40),
                        path VARCHAR(500),
                        device VARCHAR(20),
                        vw INTEGER,
                        vh INTEGER,
                        doc_w INTEGER,
                        doc_h INTEGER,
                        duration_ms INTEGER,
                        samples INTEGER,
                        click_count INTEGER,
                        track TEXT,
                        created_at {ts_type} NOT NULL,
                        updated_at {ts_type} NOT NULL
                    )
                    """
                )
            )
            for col in ("visitor_id", "session_id", "path", "created_at"):
                conn.execute(
                    text(
                        f"CREATE INDEX IF NOT EXISTS ix_session_recordings_{col} "
                        f"ON session_recordings ({col})"
                    )
                )

    if "ip_geo_cache" not in table_names:
        with db.engine.begin() as conn:
            conn.execute(
                text(
                    f"""
                    CREATE TABLE ip_geo_cache (
                        ip_hash VARCHAR(64) PRIMARY KEY,
                        country_code VARCHAR(2),
                        country VARCHAR(80),
                        region VARCHAR(100),
                        city VARCHAR(100),
                        postal_code VARCHAR(20),
                        latitude FLOAT,
                        longitude FLOAT,
                        looked_up_at {ts_type}
                    )
                    """
                )
            )

    inspector = inspect(db.engine)
    table_names = set(inspector.get_table_names())
    from app.models.founding import FoundingParticipant, FoundingStatusEvent, FoundingWaitlist

    for model in (FoundingParticipant, FoundingWaitlist, FoundingStatusEvent):
        if model.__tablename__ not in table_names:
            model.__table__.create(db.engine, checkfirst=True)
