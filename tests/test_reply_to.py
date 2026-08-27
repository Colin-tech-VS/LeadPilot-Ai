"""Every automatic email must be answerable.

LWS refuses any envelope From other than SMTP_USER, and that mailbox is
no-reply@pilotcore.fr. Without an explicit Reply-To, a homeowner who answers a
devis writes into a mailbox nobody reads — and the artisan never learns their
client had a question.
"""
import uuid
from unittest.mock import patch

import pytest

from app.services import transactional_email as tx
from app.services.signup_service import register_plumber


@pytest.fixture
def sent(app):
    """Capture what would have gone to SMTP."""
    calls = []

    def _capture(**kwargs):
        calls.append(kwargs)
        return None

    with app.app_context():
        app.config["SMTP_USER"] = "no-reply@pilotcore.fr"
        app.config["EMAIL_FROM"] = "contact@pilotcore.fr"
        with patch("app.services.admin_email.send_email", side_effect=_capture):
            yield calls


@pytest.fixture
def artisan(app):
    with app.app_context():
        email = f"artisan-{uuid.uuid4().hex[:8]}@example.com"
        _, tenant = register_plumber(
            email=email, password="MotDePasse123", company_name="Plomberie Reply", send_welcome=False
        )
        return {"tenant_id": tenant.id, "email": email}


def test_the_envelope_from_is_pinned_to_the_smtp_user(app):
    """LWS rejects anything else — so the From can never be a human address."""
    from app.services import admin_email

    with app.app_context():
        app.config["SMTP_USER"] = "no-reply@pilotcore.fr"
        assert admin_email.smtp_from_addr() == "no-reply@pilotcore.fr"
        assert admin_email.smtp_from_addr("autre@pilotcore.fr") == "no-reply@pilotcore.fr"


def test_a_transactional_email_always_carries_a_reply_to(app, sent):
    tx.send_password_changed(_user("client@example.com"))
    assert sent, "nothing was sent"
    assert sent[0]["reply_to"] == "contact@pilotcore.fr"


def test_a_devis_is_answered_to_the_artisan_not_to_us(app, sent, artisan):
    tx.send_devis_to_client(
        "proprietaire@example.com",
        artisan_name="Plomberie Reply",
        quote_total_ttc=1250.0,
        sign_url="https://www.pilotcore.fr/devis/abc",
        tenant_id=artisan["tenant_id"],
    )
    assert sent[0]["reply_to"] == artisan["email"]


def test_a_booking_confirmation_is_answered_to_the_artisan(app, sent, artisan):
    tx.send_appointment_confirmation(
        "proprietaire@example.com",
        "mardi 3 à 9h",
        "Plomberie Reply",
        tenant_id=artisan["tenant_id"],
    )
    assert sent[0]["reply_to"] == artisan["email"]


def test_an_unknown_tenant_still_falls_back_to_a_monitored_mailbox(app, sent):
    """A missing artisan must degrade to contact@, never to no Reply-To at all."""
    tx.send_devis_to_client(
        "proprietaire@example.com",
        artisan_name="Inconnue",
        quote_total_ttc=10.0,
        sign_url="https://www.pilotcore.fr/devis/x",
        tenant_id=None,
    )
    assert sent[0]["reply_to"] == "contact@pilotcore.fr"


class _user:  # noqa: N801 - tiny stand-in, not worth a factory
    def __init__(self, email):
        self.email = email
        self.first_name = None
