"""Apache/LWS force-www (apex → www). Flask must not 301 www → apex (loop)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANONICAL_ORIGIN = "https://www.pilotcore.fr"
APEX_ORIGIN = "https://pilotcore.fr"
PUBLIC_IP = "185.98.131.229"
PRIVATE_LOCATION_RE = re.compile(r"10\.|192\.168\.|127\.0\.0\.1")


def test_nginx_apex_redirects_to_www_not_the_other_way():
    nginx = (ROOT / "nginx.conf").read_text(encoding="utf-8")
    assert "server_name www.pilotcore.fr;" in nginx
    assert "return 301 https://www.pilotcore.fr$request_uri;" in nginx
    assert "https://pilotcore.fr$request_uri" not in nginx
    assert "https://pilotcore.fr/paiement" not in nginx
    assert "10.100.4." not in nginx


def test_apache_never_redirects_www_to_itself_or_apex():
    """LWS + CNAME www→apex : un force-www sans exclusion de www boucle."""
    htaccess = (ROOT / ".htaccess").read_text(encoding="utf-8")
    vhost = (ROOT / "apache" / "pilotcore.conf").read_text(encoding="utf-8")
    assert r"!^www\.pilotcore\.fr$" in htaccess
    assert "RewriteRule ^ https://www.pilotcore.fr%{REQUEST_URI} [R=301,L]" in htaccess
    assert "https://pilotcore.fr" not in htaccess.split("RewriteRule", 1)[-1]
    assert "ServerName www.pilotcore.fr" in vhost
    assert "Redirect permanent / https://www.pilotcore.fr/" in vhost
    assert "ProxyPass / http://127.0.0.1:5000/" in vhost
    www_vhost = vhost.rsplit("ServerName www.pilotcore.fr", 1)[-1]
    assert "Redirect" not in www_vhost
    assert "https://pilotcore.fr/" not in www_vhost


def test_www_home_is_not_redirected_to_apex(client):
    """Regression for d66875a: www → apex fights Apache apex → www."""
    response = client.get("/", base_url=CANONICAL_ORIGIN, follow_redirects=False)
    assert response.status_code != 301
    location = response.headers.get("Location", "")
    assert not location.startswith(APEX_ORIGIN)
    assert not PRIVATE_LOCATION_RE.search(location)


def test_apex_home_redirects_to_www(client):
    response = client.get("/", base_url=APEX_ORIGIN, follow_redirects=False)
    assert response.status_code == 301
    assert response.headers["Location"] == f"{CANONICAL_ORIGIN}/"
    assert not PRIVATE_LOCATION_RE.search(response.headers["Location"])


def test_apex_path_and_query_are_preserved(client):
    response = client.get(
        "/pro?utm_source=gsc",
        base_url=APEX_ORIGIN,
        follow_redirects=False,
    )
    assert response.status_code == 301
    assert response.headers["Location"] == f"{CANONICAL_ORIGIN}/pro?utm_source=gsc"


def test_apex_forwarded_host_redirects_to_www(client):
    response = client.get(
        "/register",
        headers={"X-Forwarded-Host": "pilotcore.fr", "X-Forwarded-Proto": "https"},
        follow_redirects=False,
    )
    assert response.status_code == 301
    assert response.headers["Location"] == f"{CANONICAL_ORIGIN}/register"


def test_www_http_upgrades_to_https_www_not_apex(client):
    response = client.get("/", base_url="http://www.pilotcore.fr", follow_redirects=False)
    assert response.status_code == 301
    assert response.headers["Location"] == f"{CANONICAL_ORIGIN}/"
    assert not response.headers["Location"].startswith(APEX_ORIGIN)


def test_www_health_stays_200(client):
    response = client.get("/api/health", base_url=CANONICAL_ORIGIN)
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
