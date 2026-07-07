"""Public contact form — inbox + admin email console."""
from app.models.email_message import DIRECTION_INBOUND, EmailMessage


def test_contact_form_stores_inbound(client, app):
    response = client.post(
        "/contact",
        data={
            "name": "Jean Dupont",
            "email": "jean@example.com",
            "subject": "Question produit",
            "message": "Bonjour, j'ai une question sur PilotCore.",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "sent=1" in response.location

    with app.app_context():
        row = (
            EmailMessage.query.filter_by(direction=DIRECTION_INBOUND)
            .order_by(EmailMessage.created_at.desc())
            .first()
        )
        assert row is not None
        assert "jean@example.com" in row.from_addr
        assert row.subject.startswith("[Contact]")
        assert "Bonjour" in row.body
        assert row.to_addr == "contact@pilotcore.fr"


def test_contact_form_requires_fields(client):
    response = client.post(
        "/contact",
        data={"name": "", "email": "", "message": ""},
    )
    assert response.status_code == 200
    assert b"requis" in response.data or b"required" in response.data


def test_contact_honeypot_skips_storage(client, app):
    with app.app_context():
        before = EmailMessage.query.count()

    response = client.post(
        "/contact",
        data={
            "name": "Bot",
            "email": "bot@spam.com",
            "message": "spam",
            "website": "http://spam.com",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302

    with app.app_context():
        assert EmailMessage.query.count() == before


def test_contact_page_get_ok(client):
    response = client.get("/contact")
    assert response.status_code == 200
    assert b"contact@pilotcore.fr" in response.data
