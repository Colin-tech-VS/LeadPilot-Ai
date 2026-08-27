"""The schema the app boots on must match the schema the models declare.

Two independent guarantees, because production has been bitten by both:

1. ``alembic upgrade head`` — the release command — must replay on a database
   that predates the migration chain. It did not: revision one recreated tables
   that already existed, the release aborted on it, and no later revision was
   ever applied. A column added by a migration then never reached production and
   every page reading that table answered 500.

2. Whatever Alembic manages to do, the running app must not select a column the
   database lacks. ``_sync_orm_columns`` closes that gap at boot; this asserts
   it actually covers the ORM.
"""
import tempfile
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config


def _fresh_url():
    return f"sqlite:///{Path(tempfile.mkdtemp(prefix='pilotcore-schema-')) / 'db.sqlite'}"


def _alembic_config(url):
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.attributes["connection_url"] = url
    return cfg


def _upgrade(url):
    engine = sa.create_engine(url)
    with engine.begin() as connection:
        cfg = _alembic_config(url)
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "head")
    engine.dispose()


def test_migrations_replay_on_a_database_that_predates_them(app):
    """The production case: tables created by ``create_all`` long before the
    first revision was written. ``upgrade head`` must be a no-op, not a crash."""
    from app.core.extensions import db

    url = _fresh_url()
    engine = sa.create_engine(url)
    db.metadata.create_all(engine)  # the pre-Alembic database
    engine.dispose()

    _upgrade(url)  # must not raise

    engine = sa.create_engine(url)
    version = sa.inspect(engine).get_table_names()
    engine.dispose()
    assert "alembic_version" in version


def test_migrations_build_the_schema_from_empty(app):
    """The other direction: a brand-new database must still get every table."""
    from app.core.extensions import db

    url = _fresh_url()
    _upgrade(url)

    engine = sa.create_engine(url)
    built = set(sa.inspect(engine).get_table_names())
    engine.dispose()

    # Tables the initial revision is responsible for.
    core = {"tenants", "users", "leads", "appointments", "quotes", "email_messages"}
    assert core <= built
    assert core <= set(db.metadata.tables)


def test_boot_adds_every_orm_column_the_database_is_missing(app):
    """A column added to a model but not to any applied migration must be
    created at boot — otherwise the first request that selects it 500s."""
    from app import _sync_orm_columns
    from app.core.extensions import db

    inspector = sa.inspect(db.engine)
    assert "listing_prompt_answered_at" in {
        col["name"] for col in inspector.get_columns("tenants")
    }

    # Drop it the way production lacked it, then let boot heal the schema.
    with db.engine.begin() as conn:
        conn.execute(sa.text("ALTER TABLE tenants DROP COLUMN listing_prompt_answered_at"))
    assert "listing_prompt_answered_at" not in {
        col["name"] for col in sa.inspect(db.engine).get_columns("tenants")
    }

    _sync_orm_columns()

    assert "listing_prompt_answered_at" in {
        col["name"] for col in sa.inspect(db.engine).get_columns("tenants")
    }
    # Idempotent — a second boot must not fail on the column it just added.
    _sync_orm_columns()


@pytest.mark.parametrize("table_name", ["tenants", "users", "leads", "quotes"])
def test_no_orm_column_is_missing_from_the_live_schema(app, table_name):
    from app.core.extensions import db

    declared = {c.name for c in db.metadata.tables[table_name].columns}
    live = {col["name"] for col in sa.inspect(db.engine).get_columns(table_name)}
    assert declared <= live, f"{table_name} is missing {sorted(declared - live)}"
