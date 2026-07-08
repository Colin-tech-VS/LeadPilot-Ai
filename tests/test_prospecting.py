"""Tests for B2B prospecting module."""
import json
import uuid
from unittest.mock import MagicMock, patch

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


def test_ddg_ad_url_is_unwrapped_to_domain():
    """Sponsored /y.js ad redirect URLs collapse to the advertiser domain."""
    from app.services.prospect_search import _normalize_ddg_url

    href = (
        "//duckduckgo.com/y.js?ad_domain=mesdepanneurs.fr&ad_provider=bingv7aa"
        "&ad_type=txad&click_metadata=" + "x" * 900 + "&iurl=%7B1%7DIG%3Dabc"
    )
    assert _normalize_ddg_url(href) == "https://mesdepanneurs.fr"


def test_long_source_url_persists_without_truncation(app, client):
    """A 700+ char redirect URL must not raise StringDataRightTruncation."""
    _login_admin(client)
    long_url = "https://example-artisan.fr/?ref=" + "a" * 800
    hits = [{"title": "Artisan", "url": long_url, "snippet": "Plombier"}]
    with patch("app.services.prospect_search.web_search", return_value=hits), patch(
        "app.services.prospect_search.harvest_emails_from_site", return_value=[]
    ), patch(
        "app.services.prospect_search.fetch_page_text", return_value=""
    ), patch(
        "app.services.prospecting.content_ai.is_available", return_value=False
    ):
        response = client.post(
            "/admin/api/prospecting/search",
            json={"trade_type": "plombier", "city": "Paris", "max_results": 3},
        )
    assert response.status_code == 200
    assert response.get_json()["found"] == 1
    with app.app_context():
        row = OutreachProspect.query.filter_by(source_url=long_url).first()
        assert row is not None
        assert row.website_url == long_url


def test_search_requires_city(client):
    _login_admin(client)
    response = client.post("/admin/api/prospecting/search", json={"trade_type": "plombier", "city": ""})
    assert response.status_code == 400


def _make_prospect(app):
    with app.app_context():
        from app.core.extensions import db

        prospect = OutreachProspect(
            id=uuid.uuid4(),
            company_name="Plomberie Martin",
            email=f"test-{uuid.uuid4().hex[:8]}@example.com",
            trade_type="plombier",
            city="Lyon",
            status="ready",
        )
        db.session.add(prospect)
        db.session.commit()
        return str(prospect.id)


def test_generate_email_handles_unparseable_ai_json(app, client):
    """Malformed AI output must not surface as an HTTP 500."""
    _login_admin(client)
    pid = _make_prospect(app)
    with patch(
        "app.services.prospecting.content_ai.is_available", return_value=True
    ), patch(
        "app.services.prospecting.content_ai._complete",
        return_value="Voici l'e-mail : {oops not json",
    ):
        res = client.post(f"/admin/api/prospecting/{pid}/generate-email", json={"tone": "pro"})
    assert res.status_code == 400
    assert "error" in res.get_json()


def test_generate_email_accepts_fenced_json(app, client):
    """The model sometimes wraps JSON in ``` fences — it must still parse."""
    _login_admin(client)
    pid = _make_prospect(app)
    fenced = "```json\n" + json.dumps(
        {"subject": "Bonjour", "body_plain": "Corps de l'e-mail.", "body_html": "<p>Corps</p>"}
    ) + "\n```"
    with patch(
        "app.services.prospecting.content_ai.is_available", return_value=True
    ), patch(
        "app.services.prospecting.content_ai._complete", return_value=fenced
    ):
        res = client.post(f"/admin/api/prospecting/{pid}/generate-email", json={"tone": "pro"})
    assert res.status_code == 200
    assert res.get_json()["outreach_subject"] == "Bonjour"


def test_generate_email_bad_uuid_is_clean_error(client):
    """A malformed prospect id must be a clean JSON error, not a 500."""
    _login_admin(client)
    res = client.post("/admin/api/prospecting/not-a-uuid/generate-email", json={})
    assert res.status_code in (400, 404, 502)
    assert "error" in res.get_json()


def _make_contacted_prospect(app, *, email_status=None):
    """Prospect « contacté » avec, si demandé, un EmailMessage sortant associé."""
    with app.app_context():
        from app.core.extensions import db
        from app.models.email_message import DIRECTION_OUTBOUND, EmailMessage
        from app.models.outreach_prospect import utcnow

        prospect = OutreachProspect(
            id=uuid.uuid4(),
            company_name="Plomberie Martin",
            email=f"test-{uuid.uuid4().hex[:8]}@example.com",
            trade_type="plombier",
            city="Lyon",
            status="contacted",
            outreach_subject="PilotCore pour votre activité",
            outreach_body="Bonjour, découvrez PilotCore.",
            last_contacted_at=utcnow(),
        )
        db.session.add(prospect)
        if email_status:
            db.session.add(
                EmailMessage(
                    direction=DIRECTION_OUTBOUND,
                    status=email_status,
                    to_addr=prospect.email,
                    subject=prospect.outreach_subject,
                    body=prospect.outreach_body,
                )
            )
        db.session.commit()
        return str(prospect.id), prospect.email


def test_resend_targets_only_failed_sends(app, client):
    """« Renvoyer les échecs » ne renvoie qu'aux prospects dont l'e-mail a échoué."""
    _login_admin(client)
    _, failed_email = _make_contacted_prospect(app, email_status="failed")
    _, sent_email = _make_contacted_prospect(app, email_status="sent")

    with patch("app.services.prospecting.admin_email.send_email") as send_mock:
        send_mock.return_value.status = "sent"
        res = client.post("/admin/prospecting/resend", follow_redirects=True)
    assert res.status_code == 200
    called = [c.args[0] for c in send_mock.call_args_list]
    assert failed_email in called
    assert sent_email not in called


def _clear_prospects(app):
    """Vide la table pour isoler les tests du lot (max_batch) des résidus."""
    with app.app_context():
        from app.core.extensions import db

        OutreachProspect.query.delete()
        db.session.commit()


def test_resend_all_mode_forces_already_sent(app, client):
    """« Tout renvoyer » (mode=all) renvoie même aux prospects marqués « sent »."""
    _login_admin(client)
    _clear_prospects(app)
    _, sent_email = _make_contacted_prospect(app, email_status="sent")

    with patch("app.services.prospecting.admin_email.send_email") as send_mock:
        send_mock.return_value.status = "sent"
        send_mock.return_value.error = None
        res = client.post(
            "/admin/prospecting/resend", data={"mode": "all"}, follow_redirects=True
        )
    assert res.status_code == 200
    assert sent_email in [c.args[0] for c in send_mock.call_args_list]


def test_resend_failure_reports_smtp_error(app, client):
    """L'erreur SMTP enregistrée doit remonter dans le flash, pas juste l'adresse."""
    _login_admin(client)
    _clear_prospects(app)
    _, email = _make_contacted_prospect(app, email_status="failed")

    with patch("app.services.prospecting.admin_email.send_email") as send_mock:
        send_mock.return_value.status = "failed"
        send_mock.return_value.error = "550 blocked by content filter"
        res = client.post("/admin/prospecting/resend", follow_redirects=True)
    assert res.status_code == 200
    assert b"550 blocked by content filter" in res.data


def test_resend_survives_unexpected_error_on_one_prospect(app, client):
    """Une exception inattendue sur un prospect ne doit pas interrompre le lot."""
    _login_admin(client)
    _clear_prospects(app)
    _make_contacted_prospect(app, email_status="failed")
    _make_contacted_prospect(app, email_status="failed")

    ok = MagicMock(status="sent", error=None)
    state = {"calls": 0}

    def _boom_then_ok(*args, **kwargs):
        state["calls"] += 1
        if state["calls"] == 1:
            raise RuntimeError("boom")
        return ok

    with patch("app.services.prospecting.admin_email.send_email") as send_mock:
        send_mock.side_effect = _boom_then_ok
        res = client.post("/admin/prospecting/resend", follow_redirects=True)
    assert res.status_code == 200
    # Le premier envoi explose, mais le lot continue sur les suivants.
    assert send_mock.call_count >= 2


def test_resend_skips_opted_out(app, client):
    _login_admin(client)
    pid, email = _make_contacted_prospect(app, email_status="failed")
    with app.app_context():
        from app.core.extensions import db
        from app.models.outreach_prospect import utcnow

        row = db.session.get(OutreachProspect, uuid.UUID(pid))
        row.opted_out_at = utcnow()
        db.session.commit()

    with patch("app.services.prospecting.admin_email.send_email") as send_mock:
        send_mock.return_value.status = "sent"
        res = client.post("/admin/prospecting/resend", follow_redirects=True)
    assert res.status_code == 200
    assert email not in [c.args[0] for c in send_mock.call_args_list]
