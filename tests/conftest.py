import os

import pytest

os.environ.setdefault("FLASK_ENV", "development")


@pytest.fixture
def app():
    from app import create_app

    application = create_app()
    application.config["TESTING"] = True
    application.config["PUBLIC_BASE_URL"] = "https://www.pilotcore.fr"
    yield application


@pytest.fixture
def client(app):
    return app.test_client()
