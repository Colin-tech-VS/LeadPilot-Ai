"""Neither public host may 301 to the other — that is the TooManyRedirects loop."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANONICAL_ORIGIN = "https://www.pilotcore.fr"
APEX_ORIGIN = "https://pilotcore.fr"
PUBLIC_IP = "185.98.131.229"
PRIVATE_LOCATION_RE = re.compile(r"10\.|192\.168\.|127\.0\.0\.1")
WWW_REDIRECT_TARGET_RE = re.compile(
    r"(?:Redirect\S*|RewriteRule)\s+\S[^\n]*https://www\.pilotcore\.fr",
    re.IGNORECASE,
)


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_nginx_does_not_bounce_hosts():
    nginx = _read("nginx.conf")
    assert "return 301 https://pilotcore.fr$request_uri;" not in nginx
    assert "return 301 https://www.pilotcore.fr$request_uri;" not in nginx
    assert "proxy_pass http://185.98.131.229:5000;" in nginx
    assert "10.100.4." not in nginx
    assert "server_name pilotcore.fr www.pilotcore.fr" in nginx
    assert "location ~ ^/(api/health|health/ready|health|api)$" in nginx
    assert "map $host $https_origin" in nginx


def test_apache_never_redirects_www_to_itself_or_apex():
    """LWS + CNAME www→apex : un force-www sans exclusion de www boucle."""
    htaccess = _read(".htaccess")
    for rel in ("apache.conf", "apache/pilotcore.conf"):
        vhost = _read(rel)
        assert "Redirect permanent" not in vhost
        assert WWW_REDIRECT_TARGET_RE.search(vhost) is None
        assert "ProxyPass / http://127.0.0.1:5000/" in vhost
        assert "ServerAlias pilotcore.fr" in vhost
        assert "RewriteRule ^ https://%{HTTP_HOST}%{REQUEST_URI} [R=301,L]" not in vhost
        assert "RewriteRule ^/(?:api/health|health/ready|health|api)/?$ - [L,NC]" in vhost
        assert "RewriteCond %{HTTPS} on" in vhost
        assert "RewriteCond %{HTTP:X-Forwarded-Proto} =https" in vhost
    assert "RewriteEngine Off" in htaccess
    assert "https://www.pilotcore.fr%{REQUEST_URI}" not in htaccess
    assert WWW_REDIRECT_TARGET_RE.search(htaccess) is None
    assert "%{HTTPS} off" not in htaccess


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


def test_apex_ignores_stale_www_forwarded_host(client):
    """Apache still injecting X-Forwarded-Host: www must not 301 apex to www."""
    response = client.get(
        "/",
        base_url=APEX_ORIGIN,
        headers={"X-Forwarded-Host": "www.pilotcore.fr", "X-Forwarded-Proto": "https"},
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


def test_apex_http_upgrades_to_https_apex_not_www(client):
    response = client.get("/", base_url="http://pilotcore.fr", follow_redirects=False)
    assert response.status_code == 301
    assert response.headers["Location"] == f"{APEX_ORIGIN}/"
    assert not response.headers["Location"].startswith(CANONICAL_ORIGIN)


def test_www_health_stays_200(client):
    response = client.get("/api/health", base_url=CANONICAL_ORIGIN)
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_apex_health_stays_200(client):
    response = client.get("/api/health", base_url=APEX_ORIGIN)
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_http_health_is_not_upgraded(client):
    """Sondes : pas de 301, même en HTTP."""
    for base in (CANONICAL_ORIGIN.replace("https", "http"), "http://pilotcore.fr"):
        response = client.get("/api/health", base_url=base, follow_redirects=False)
        assert response.status_code == 200, base
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


def test_admin_is_not_a_host_redirect(client):
    """/admin may 302 to login; it must never 301 www ↔ apex."""
    for base in (CANONICAL_ORIGIN, APEX_ORIGIN):
        response = client.get("/admin", base_url=base, follow_redirects=False)
        assert response.status_code in (200, 302), base
        assert response.status_code != 301
