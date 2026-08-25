import os
import tempfile
from pathlib import Path

import pytest

# Pytest must never write to production Supabase — ignore system DATABASE_URL.
os.environ["FLASK_ENV"] = "testing"
os.environ.pop("DATABASE_URL", None)

# Neutralize live provider keys BEFORE any app import / load_dotenv. dotenv
# does not override existing variables, so this blocks billed Twilio / Stripe /
# OpenAI / Places calls even when the developer .env holds real credentials.
os.environ["TWILIO_ACCOUNT_SID"] = "ACffffffffffffffffffffffffffffffff"
os.environ["TWILIO_AUTH_TOKEN"] = "pytest-never-live"
os.environ["TWILIO_AUTO_PROVISION_NUMBERS"] = "0"
os.environ["TWILIO_VALIDATE_SIGNATURE"] = "0"
os.environ["LIVE_PROVIDER_SPEND"] = "0"
os.environ["STRIPE_SECRET_KEY"] = "sk_test_pytest"
os.environ["OPENAI_API_KEY"] = ""
os.environ["GOOGLE_PLACES_API_KEY"] = ""

# Each pytest session gets its own SQLite file.
#
# The default (``sqlite:///PilotCore_test.db``) is a file in the project root
# that is created once and never reset, so rows accumulate from run to run. That
# makes the suite depend on its own history: the indexability tests assert that
# « plombier / Lyon » is thin enough to stay noindex, and they pass on a virgin
# checkout — then fail on the second run, because a public Lyon plumber created
# by an earlier test is still sitting in the file and has opened the gate.
#
# A per-session temp file keeps the sharing that tests inside one run rely on
# (nothing is rolled back between tests, and helpers like ``_set_guides`` are
# written for that) while guaranteeing every run starts from empty.
_TMP_DB = Path(tempfile.mkdtemp(prefix="pilotcore-tests-")) / "test.db"
os.environ["TEST_DATABASE_URL"] = f"sqlite:///{_TMP_DB}"


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
