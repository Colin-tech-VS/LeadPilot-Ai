"""Google Search Console OAuth helpers."""
from app import create_app
from app.services import google_gsc


def test_gsc_redirect_uri_uses_callback_route():
    app = create_app()
    app.config["GOOGLE_GSC_CLIENT_ID"] = "client-id"
    app.config["GOOGLE_GSC_CLIENT_SECRET"] = "client-secret"
    app.config["PUBLIC_BASE_URL"] = "https://www.pilotcore.fr"
    with app.test_request_context("/admin/gsc"):
        uri = google_gsc.redirect_uri()
    assert uri.endswith("/admin/gsc/callback")


def test_gsc_auth_url_contains_scopes():
    app = create_app()
    app.config["GOOGLE_GSC_CLIENT_ID"] = "client-id"
    app.config["GOOGLE_GSC_CLIENT_SECRET"] = "client-secret"
    with app.test_request_context("/admin/gsc"):
        url = google_gsc.build_auth_url("test-state")
    assert "webmasters.readonly" in url
    assert "client-id" in url
    assert "test-state" in url
    assert "offline" in url or "access_type=offline" in url


def test_gsc_status_when_not_configured():
    app = create_app()
    app.config["GOOGLE_GSC_CLIENT_ID"] = ""
    app.config["GOOGLE_GSC_CLIENT_SECRET"] = ""
    with app.app_context():
        status = google_gsc.status()
    assert status["configured"] is False
    assert status["connected"] is False
