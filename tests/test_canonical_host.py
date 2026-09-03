"""www.pilotcore.fr → https://pilotcore.fr (301). Never redirect to an internal IP."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANONICAL_ORIGIN = "https://pilotcore.fr"
WWW_ORIGIN = "https://www.pilotcore.fr"
PRIVATE_LOCATION_RE = re.compile(r"10\.|192\.168\.|127\.0\.0\.1")


def test_nginx_www_redirects_to_apex_not_internal_ip():
    nginx = (ROOT / "nginx.conf").read_text(encoding="utf-8")
    assert "server_name www.pilotcore.fr;" in nginx
    assert "return 301 https://pilotcore.fr$request_uri;" in nginx
    assert "https://www.pilotcore.fr$request_uri" not in nginx
    assert "10.100.4." not in nginx


def test_www_home_redirects_to_apex(client):
    response = client.get("/", base_url=WWW_ORIGIN, follow_redirects=False)
    assert response.status_code == 301
    assert response.headers["Location"] == f"{CANONICAL_ORIGIN}/"
    assert not PRIVATE_LOCATION_RE.search(response.headers["Location"])


def test_www_path_and_query_are_preserved(client):
    response = client.get(
        "/pro?utm_source=gsc",
        base_url=WWW_ORIGIN,
        follow_redirects=False,
    )
    assert response.status_code == 301
    assert response.headers["Location"] == f"{CANONICAL_ORIGIN}/pro?utm_source=gsc"


def test_www_forwarded_host_redirects_to_apex(client):
    response = client.get(
        "/register",
        headers={"X-Forwarded-Host": "www.pilotcore.fr"},
        follow_redirects=False,
    )
    assert response.status_code == 301
    assert response.headers["Location"] == f"{CANONICAL_ORIGIN}/register"


def test_apex_home_is_not_redirected_to_www(client):
    response = client.get("/", base_url=CANONICAL_ORIGIN, follow_redirects=False)
    assert response.status_code != 301
    location = response.headers.get("Location", "")
    assert not location.startswith(WWW_ORIGIN)


def test_www_health_stays_200(client):
    response = client.get("/api/health", base_url=WWW_ORIGIN)
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_health_on_internal_host_stays_200(client):
    response = client.get("/api/health", base_url="https://10.100.4.106")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_internal_ip_is_not_redirected_to_a_private_location(client):
    response = client.get("/", base_url="https://10.100.4.106", follow_redirects=False)
    location = response.headers.get("Location", "")
    assert "10.100.4" not in location
    if response.status_code == 301:
        assert not PRIVATE_LOCATION_RE.search(location)


def test_www_webhook_post_is_not_redirected(client):
    response = client.post(
        "/webhook/inbound-call",
        base_url=WWW_ORIGIN,
        json={"foo": "bar"},
        follow_redirects=False,
    )
    assert response.status_code != 301
