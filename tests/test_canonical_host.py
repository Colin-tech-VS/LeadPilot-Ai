"""Neither public host may 301 to the other — that is the TooManyRedirects loop."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANONICAL_ORIGIN = "https://www.pilotcore.fr"
APEX_ORIGIN = "https://pilotcore.fr"
PUBLIC_IP = "185.98.131.229"
PRIVATE_LOCATION_RE = re.compile(r"10\.|192\.168\.|127\.0\.0\.1")


def test_nginx_does_not_bounce_hosts():
    nginx = (ROOT / "nginx.conf").read_text(encoding="utf-8")
    assert "return 301 https://pilotcore.fr$request_uri;" not in nginx
    assert "return 301 https://www.pilotcore.fr$request_uri;" not in nginx
    assert "proxy_pass http://185.98.131.229:5000;" in nginx
    assert "10.100.4." not in nginx
    assert "server_name pilotcore.fr www.pilotcore.fr" in nginx


def test_apache_never_redirects_www_to_itself_or_apex():
    """LWS + CNAME www→apex : un force-www sans exclusion de www boucle."""
    htaccess = (ROOT / ".htaccess").read_text(encoding="utf-8")
    vhost = (ROOT / "apache" / "pilotcore.conf").read_text(encoding="utf-8")
    assert "RewriteEngine Off" in htaccess
    assert "https://www.pilotcore.fr%{REQUEST_URI}" not in htaccess
    assert "Redirect permanent" not in vhost
    assert "ProxyPass / http://127.0.0.1:5000/" in vhost
    assert "ServerAlias pilotcore.fr" in vhost
    assert "https://pilotcore.fr/" not in vhost


def test_www_home_is_served_not_redirected_to_apex(client):
    response = client.get("/", base_url=CANONICAL_ORIGIN, follow_redirects=False)
    assert response.status_code == 200
    location = response.headers.get("Location", "")
    assert not location.startswith(APEX_ORIGIN)
    assert not PRIVATE_LOCATION_RE.search(location)


def test_apex_home_is_served_not_redirected_to_www(client):
    """https://pilotcore.fr/ must be HTTP 200 when Flask sees the request."""
    response = client.get("/", base_url=APEX_ORIGIN, follow_redirects=False)
    assert response.status_code == 200
    location = response.headers.get("Location", "")
    assert not location.startswith(CANONICAL_ORIGIN)
    assert not PRIVATE_LOCATION_RE.search(location)


def test_apex_path_is_not_rewritten_to_www(client):
    response = client.get(
        "/pro?utm_source=gsc",
        base_url=APEX_ORIGIN,
        follow_redirects=False,
    )
    assert response.status_code == 200
    location = response.headers.get("Location", "")
    assert not location.startswith(CANONICAL_ORIGIN)


def test_apex_forwarded_host_is_not_redirected_to_www(client):
    response = client.get(
        "/register",
        headers={"X-Forwarded-Host": "pilotcore.fr", "X-Forwarded-Proto": "https"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    location = response.headers.get("Location", "")
    assert not location.startswith(CANONICAL_ORIGIN)


def test_www_http_upgrades_to_https_www_not_apex(client):
    response = client.get("/", base_url="http://www.pilotcore.fr", follow_redirects=False)
    assert response.status_code == 301
    assert response.headers["Location"] == f"{CANONICAL_ORIGIN}/"
    assert not response.headers["Location"].startswith(APEX_ORIGIN)


def test_www_health_stays_200(client):
    response = client.get("/api/health", base_url=CANONICAL_ORIGIN)
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_apex_health_stays_200(client):
    response = client.get("/api/health", base_url=APEX_ORIGIN)
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_health_on_internal_host_stays_200(client):
    response = client.get("/api/health", base_url="https://10.100.4.106")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_internal_ip_is_not_redirected(client):
    """RFC1918 probes must not be rewritten (Scalingo healthcheck)."""
    response = client.get("/", base_url="https://10.100.4.106", follow_redirects=False)
    assert response.status_code != 301
    location = response.headers.get("Location", "")
    assert "10.100.4" not in location
    assert not PRIVATE_LOCATION_RE.search(location)


def test_public_ip_redirects_to_www_not_internal(client):
    response = client.get("/", base_url=f"https://{PUBLIC_IP}", follow_redirects=False)
    assert response.status_code == 301
    assert response.headers["Location"] == f"{CANONICAL_ORIGIN}/"
    assert not PRIVATE_LOCATION_RE.search(response.headers["Location"])


def test_www_webhook_post_is_not_redirected(client):
    response = client.post(
        "/webhook/inbound-call",
        base_url=CANONICAL_ORIGIN,
        json={"foo": "bar"},
        follow_redirects=False,
    )
    assert response.status_code != 301
