import os

import pytest

os.environ.setdefault("FLASK_ENV", "development")


@pytest.fixture
def app():
    from app import create_app

    application = create_app()
    application.config["TESTING"] = True
    yield application


@pytest.fixture
def client(app):
    return app.test_client()
