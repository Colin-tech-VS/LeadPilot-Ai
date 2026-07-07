"""Tests for B2B prospecting module."""
import json
import uuid
from unittest.mock import patch

from app.models.outreach_prospect import OutreachProspect


def _login_admin(client):
    with client.session_transaction() as sess:
        sess["admin_authenticated"] = True
        sess["admin_username"] = "admin"


def test_prospecting_page_requires_admin(client):
    response = client.get("/admin/prospecting")
    assert response.status_code in (302, 401, 403)


def test_prospecting_page_ok(client):
    _login_admin(client)
    response = client.get("/admin/prospecting")
    assert response.status_code == 200
    assert b"Prospection artisans" in response.data


def test_run_search_persists_prospects(app, client):
    _login_admin(client)
    email = f"contact-{uuid.uuid4().hex[:8]}@plomberie-martin-lyon.fr"
    hits = [
        {
            "title": "Plomberie Martin — Lyon",
            "url": "https://plomberie-martin-lyon.fr",
            "snippet": f"Artisan plombier à Lyon. Contact : {email}",
        }
    ]
    with patch("app.services.prospect_search.web_search", return_value=hits), patch(
        "app.services.prospect_search.harvest_emails_from_site",
        return_value=[email],
    ), patch(
        "app.services.prospecting.content_ai.is_available",
        return_value=False,
    ):
        response = client.post(
            "/admin/api/prospecting/search",
            json={"trade_type": "plombier", "city": "Lyon", "max_results": 5},
        )
    assert response.status_code == 200
    data = response.get_json()
    assert data["found"] == 1
    assert data["with_email"] == 1

    with app.app_context():
        row = OutreachProspect.query.filter_by(email=email).first()
        assert row is not None
        assert row.trade_type == "plombier"
        assert row.city == "Lyon"


def test_generate_and_send_outreach_email(app, client):
    _login_admin(client)
    with app.app_context():
        from app.core.extensions import db

        prospect = OutreachProspect(
            id=uuid.uuid4(),
            first_name="Jean",
            last_name="Martin",
            company_name="Plomberie Martin",
            email=f"test-{uuid.uuid4().hex[:8]}@example.com",
            trade_type="plombier",
            city="Lyon",
            status="ready",
        )
        db.session.add(prospect)
        db.session.commit()
        pid = str(prospect.id)

    ai_payload = {
        "subject": "PilotCore pour votre activité de plombier",
        "body_plain": "Bonjour Jean,\n\nPilotCore aide les artisans à ne plus rater d'appels.",
        "body_html": "<p>Bonjour Jean,</p>",
    }
    with patch(
        "app.services.prospecting.content_ai.is_available",
        return_value=True,
    ), patch(
        "app.services.prospecting.content_ai._complete",
        return_value=json.dumps(ai_payload),
    ):
        gen = client.post(f"/admin/api/prospecting/{pid}/generate-email", json={"tone": "professionnel"})
    assert gen.status_code == 200
    assert gen.get_json()["outreach_subject"]

    with patch("app.services.prospecting.admin_email.send_email") as send_mock:
        send_mock.return_value.status = "simulated"
        send = client.post(f"/admin/prospecting/{pid}/send", follow_redirects=True)
    assert send.status_code == 200
    send_mock.assert_called_once()

    with app.app_context():
        from app.core.extensions import db

        row = db.session.get(OutreachProspect, uuid.UUID(pid))
        assert row.status == "contacted"
        assert row.last_contacted_at is not None


def test_search_requires_city(client):
    _login_admin(client)
    response = client.post("/admin/api/prospecting/search", json={"trade_type": "plombier", "city": ""})
    assert response.status_code == 400
