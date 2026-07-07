import os

import pytest

# Pytest must never write to production Supabase — ignore system DATABASE_URL.
os.environ["FLASK_ENV"] = "testing"
os.environ.pop("DATABASE_URL", None)


@pytest.fixture
def app():
    from app import create_app
    from app.core.extensions import db

    application = create_app()
    application.config["PUBLIC_BASE_URL"] = "https://www.pilotcore.fr"

    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()


@pytest.fixture
def client(app):
    return app.test_client()
