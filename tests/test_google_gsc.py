"""Google Search Console OAuth helpers."""
from app import create_app
from app.services import google_gsc


def test_gsc_redirect_uri_uses_public_base_url():
    app = create_app()
    app.config["GOOGLE_GSC_CLIENT_ID"] = "client-id"
    app.config["GOOGLE_GSC_CLIENT_SECRET"] = "client-secret"
    app.config["PUBLIC_BASE_URL"] = "https://www.pilotcore.fr"
    with app.test_request_context("/admin/gsc"):
        uri = google_gsc.redirect_uri()
    assert uri == "https://www.pilotcore.fr/admin/gsc/callback"


def test_gsc_redirect_uri_uses_callback_route():
    app = create_app()
    app.config["GOOGLE_GSC_CLIENT_ID"] = "client-id"
    app.config["GOOGLE_GSC_CLIENT_SECRET"] = "client-secret"
    app.config["PUBLIC_BASE_URL"] = ""
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
        google_gsc.disconnect()
        status = google_gsc.status()
    assert status["configured"] is False
    assert status["connected"] is False


def test_gsc_table_rows_expose_query_as_label():
    """Jinja ``row.keys`` is dict.keys(), so the template must not read that field."""
    rows = google_gsc._table_rows(
        [
            {
                "keys": ["chauffagiste toulouse"],
                "clicks": 0,
                "impressions": 47,
                "ctr": 0.0,
                "position": 12.4,
            }
        ]
    )
    assert rows[0]["label"] == "chauffagiste toulouse"
    assert rows[0]["impressions"] == 47
    assert "keys" not in rows[0]


def test_gsc_query_label_renders_in_jinja():
    from jinja2 import Environment

    env = Environment()
    raw = {"keys": ["plombier toulouse"], "clicks": 0}
    broken = env.from_string("{{ row.keys[0] if row.keys else 'missing' }}").render(row=raw)
    assert broken == ""
    fixed = env.from_string("{{ row.label }}").render(
        row=google_gsc._table_rows([raw])[0]
    )
    assert fixed == "plombier toulouse"
