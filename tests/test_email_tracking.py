"""Open / click tracking for outbound mail (Brevo-style, admin hits ignored)."""
from urllib.parse import quote

from app.core.extensions import db
from app.models.email_message import (
    DIRECTION_OUTBOUND,
    STATUS_SENT,
    EmailMessage,
)
from app.services.admin_email import _build_mime, send_email
from app.services import email_tracking

HTML = """<!DOCTYPE html><html><body>
<p>Bonjour, <a href="https://www.pilotcore.fr/sign/abc">voir le devis</a>.</p>
<p><a href="mailto:contact@pilotcore.fr">nous écrire</a></p>
</body></html>"""


def _row(app, **kwargs):
    defaults = dict(
        direction=DIRECTION_OUTBOUND,
        status=STATUS_SENT,
        to_addr="client@example.com",
        subject="Devis",
        body="Bonjour",
        html_body=HTML,
        is_html=True,
        track_token=email_tracking.new_track_token(),
        open_count=0,
        click_count=0,
    )
    defaults.update(kwargs)
    row = EmailMessage(**defaults)
    db.session.add(row)
    db.session.commit()
    return row


def test_wraps_http_links_and_adds_pixel_not_mailto(app):
    with app.app_context():
        token = "tok_test_abc"
        out = email_tracking.instrument_html(HTML, token, "https://www.pilotcore.fr")
        assert f"/t/o/{token}.gif" in out
        assert f"/t/c/{token}?u=" in out
        assert "mailto:contact@pilotcore.fr" in out
        assert out.count("/t/o/") == 1
        stored = HTML
        assert "/t/o/" not in stored


def test_should_not_track_internal_recipients(app):
    with app.app_context():
        assert email_tracking.should_track_recipients("client@example.com")
        assert not email_tracking.should_track_recipients("contact@pilotcore.fr")
        assert not email_tracking.should_track_recipients("Colin <contact@pilotcore.fr>")
        assert email_tracking.should_track_recipients(
            "client@example.com", "contact@pilotcore.fr"
        )


def test_send_email_stores_untracked_html_but_assigns_token(app):
    app.config["SMTP_HOST"] = ""
    with app.app_context():
        row = send_email(
            "artisan@example.com",
            "Votre devis",
            "Bonjour",
            is_html=True,
            html_body=HTML,
        )
        assert row.track_token
        assert row.html_body == HTML
        assert "/t/o/" not in (row.html_body or "")
        mime_html, _ = email_tracking.instrument_bodies(
            row.track_token, HTML, "Bonjour", True
        )
        mime = _build_mime(
            "contact@pilotcore.fr",
            "artisan@example.com",
            "Votre devis",
            "Bonjour",
            is_html=True,
            html_body=mime_html,
        )
        html_part = mime.get_payload(1).get_payload(decode=True).decode("utf-8")
        assert f"/t/o/{row.track_token}.gif" in html_part
        assert "/t/c/" in html_part
        plain = mime.get_payload(0).get_payload(decode=True).decode("utf-8")
        assert "/t/c/" in plain


def test_send_email_skips_tracking_for_internal(app):
    app.config["SMTP_HOST"] = ""
    with app.app_context():
        row = send_email("contact@pilotcore.fr", "Notif", "Hello")
        assert row.track_token is None


def test_open_pixel_increments_and_returns_gif(client, app):
    with app.app_context():
        row = _row(app)
        token = row.track_token
        row_id = row.id
    resp = client.get(f"/t/o/{token}.gif")
    assert resp.status_code == 200
    assert resp.mimetype == "image/gif"
    assert resp.data[:6] == b"GIF89a"
    with app.app_context():
        row = db.session.get(EmailMessage, row_id)
        assert row.opens == 1
        assert row.was_opened
        assert row.first_opened_at is not None


def test_gmail_image_proxy_counts_as_open(client, app):
    with app.app_context():
        row = _row(app)
        token = row.track_token
        row_id = row.id
    ua = "Mozilla/5.0 (Windows NT 5.1; rv:11.0) Gecko Firefox/11.0 (via ggpht.com GoogleImageProxy)"
    client.get(f"/t/o/{token}", headers={"User-Agent": ua})
    with app.app_context():
        assert db.session.get(EmailMessage, row_id).opens == 1


def test_admin_session_does_not_count_open(client, app):
    with app.app_context():
        row = _row(app)
        token = row.track_token
        row_id = row.id
    with client.session_transaction() as sess:
        sess["admin_authenticated"] = True
        sess["admin_username"] = "PilotCore_Admin"
    client.get(f"/t/o/{token}.gif")
    with app.app_context():
        assert db.session.get(EmailMessage, row_id).opens == 0


def test_admin_referer_does_not_count_open(client, app):
    with app.app_context():
        row = _row(app)
        token = row.track_token
        row_id = row.id
    client.get(
        f"/t/o/{token}",
        headers={"Referer": "https://www.pilotcore.fr/admin/emails"},
    )
    with app.app_context():
        assert db.session.get(EmailMessage, row_id).opens == 0


def test_click_redirects_and_counts(client, app):
    dest = "https://www.pilotcore.fr/sign/abc"
    with app.app_context():
        row = _row(app)
        token = row.track_token
        row_id = row.id
    resp = client.get(f"/t/c/{token}?u={quote(dest, safe='')}", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["Location"] == dest
    with app.app_context():
        row = db.session.get(EmailMessage, row_id)
        assert row.clicks == 1
        assert row.was_clicked
        assert row.was_opened  # click implies open
        links = row.clicked_links()
        assert links[0]["url"] == dest
        assert links[0]["count"] == 1


def test_admin_session_click_redirects_but_does_not_count(client, app):
    dest = "https://www.pilotcore.fr/pro"
    with app.app_context():
        row = _row(app)
        token = row.track_token
        row_id = row.id
    with client.session_transaction() as sess:
        sess["admin_authenticated"] = True
    resp = client.get(f"/t/c/{token}?u={quote(dest, safe='')}", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["Location"] == dest
    with app.app_context():
        assert db.session.get(EmailMessage, row_id).clicks == 0


def test_outbound_stats_rates(app):
    with app.app_context():
        a = _row(app)
        b = _row(app)
        email_tracking.record_open(a.track_token)
        email_tracking.record_click(b.track_token, "https://www.pilotcore.fr/pro")
        stats = email_tracking.outbound_stats()
        assert stats["sent"] >= 2
        assert stats["unique_opens"] >= 2  # click also marks open
        assert stats["unique_clicks"] >= 1
        assert stats["open_rate"] != "—"
        assert stats["click_rate"] != "—"


def test_outbox_page_shows_rates(client, app):
    with app.app_context():
        _row(app)
    with client.session_transaction() as sess:
        sess["admin_authenticated"] = True
        sess["admin_username"] = "admin"
    resp = client.get("/admin/emails?box=outbox")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Taux d" in html and "ouverture" in html
    assert "Taux de clic" in html
    assert "Non ouvert" in html


def test_email_detail_shows_engagement_panel(client, app):
    with app.app_context():
        row = _row(app)
        message_id = row.id
    with client.session_transaction() as sess:
        sess["admin_authenticated"] = True
        sess["admin_username"] = "admin"
    resp = client.get(f"/admin/emails/{message_id}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Ouvertures" in html
    assert "session admin ne sont pas comptés" in html


def test_head_on_pixel_does_not_count(client, app):
    with app.app_context():
        row = _row(app)
        token = row.track_token
        row_id = row.id
    client.head(f"/t/o/{token}.gif")
    with app.app_context():
        assert db.session.get(EmailMessage, row_id).opens == 0
