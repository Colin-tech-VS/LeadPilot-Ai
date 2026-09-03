"""HTTPS www et apex servent /paiement. Aucun 301 d'hôte (boucle LWS)."""
import re
import uuid
from pathlib import Path

from config import Config

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_IP = "185.98.131.229"
CANONICAL_ORIGIN = "https://www.pilotcore.fr"
PAIEMENT_URL = f"{CANONICAL_ORIGIN}/paiement"
FORBIDDEN_HOST_RE = re.compile(r"SCALINGO_HOSTNAME|localhost|10\.100\.4\.")
PRIVATE_LOCATION_RE = re.compile(r"10\.|192\.168\.|127\.0\.0\.1")


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_public_ip_is_configured_on_config():
    assert Config.PUBLIC_IP == PUBLIC_IP


def test_env_declares_public_ip():
    env_text = _read(".env")
    assert f"PUBLIC_IP={PUBLIC_IP}" in env_text


def test_env_example_declares_public_ip():
    assert f"PUBLIC_IP={PUBLIC_IP}" in _read(".env.example")


def test_target_files_do_not_use_internal_hosts():
    for rel in (".env", "config.py", "nginx.conf"):
        for lineno, line in enumerate(_read(rel).splitlines(), start=1):
            assert not FORBIDDEN_HOST_RE.search(line), (
                f"{rel}:{lineno} still mentions a forbidden host: {line!r}"
            )


def test_nginx_does_not_bounce_paiement_to_the_other_host():
    nginx = _read("nginx.conf")
    assert PUBLIC_IP in nginx
    assert "return 301 https://www.pilotcore.fr$request_uri;" not in nginx
    assert "return 301 https://pilotcore.fr$request_uri;" not in nginx
    assert "https://pilotcore.fr/paiement" not in nginx
    assert "server_name pilotcore.fr www.pilotcore.fr" in nginx


def test_paiement_route_is_registered(app):
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    assert "/paiement" in rules
    assert (ROOT / "templates" / "artisan" / "billing.html").is_file()
    from app.routes.billing import PAIEMENT_CANONICAL_URL

    assert PAIEMENT_CANONICAL_URL == PAIEMENT_URL


def test_paiement_never_returns_404(client):
    response = client.get("/paiement", follow_redirects=False)
    assert response.status_code != 404
    assert response.status_code in (200, 302)


def test_http_paiement_trailing_slash_does_not_404(client):
    response = client.get("/paiement/", follow_redirects=False)
    assert response.status_code != 404
    assert response.status_code in (200, 302)


def test_apex_paiement_is_not_redirected_to_www(client):
    response = client.get(
        "/paiement",
        base_url="https://pilotcore.fr",
        follow_redirects=False,
    )
    assert response.status_code != 301
    location = response.headers.get("Location", "")
    assert location != PAIEMENT_URL
    assert not PRIVATE_LOCATION_RE.search(location)


def test_www_paiement_does_not_redirect_to_apex(client):
    """www/paiement → apex/paiement would loop with Apache force-www."""
    response = client.get(
        "/paiement",
        base_url=CANONICAL_ORIGIN,
        follow_redirects=False,
    )
    assert response.status_code != 301
    location = response.headers.get("Location", "")
    assert location != "https://pilotcore.fr/paiement"
    assert not location.startswith("https://pilotcore.fr")


def test_public_ip_paiement_redirects_to_www(client):
    response = client.get(
        "/paiement",
        base_url=f"https://{PUBLIC_IP}",
        follow_redirects=False,
    )
    assert response.status_code == 301
    assert response.headers["Location"] == PAIEMENT_URL


def test_https_www_paiement_requires_login(client):
    response = client.get(
        "/paiement",
        base_url=CANONICAL_ORIGIN,
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
    assert response.headers["Location"] != PAIEMENT_URL


def test_canonical_paiement_url_does_not_redirect_to_itself(client):
    """https://www.pilotcore.fr/paiement must never 301 back to the same URL."""
    response = client.get(
        "/paiement",
        base_url=CANONICAL_ORIGIN,
        follow_redirects=False,
    )
    assert response.status_code != 301
    assert response.headers.get("Location") != PAIEMENT_URL


def test_paiement_apex_ignores_stale_www_forwarded_host(client):
    response = client.get(
        "/paiement",
        base_url="https://pilotcore.fr",
        headers={
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "www.pilotcore.fr",
        },
        follow_redirects=False,
    )
    assert response.status_code != 301
    assert response.headers.get("Location") != PAIEMENT_URL


def test_https_www_paiement_renders_billing_when_signed_in(client, app):
    email = f"pay-{uuid.uuid4().hex[:8]}@example.com"
    signup = client.post(
        "/register",
        data={
            "company_name": "Plomberie Paiement",
            "city": "Ajaccio",
            "trade_type": "plombier",
            "email": email,
            "password": "MotDePasse123",
        },
        follow_redirects=False,
    )
    assert signup.status_code == 302

    headers = {
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": "www.pilotcore.fr",
    }
    response = client.get("/paiement", headers=headers, follow_redirects=False)
    assert response.status_code == 200
    billing = client.get("/billing")
    assert billing.status_code == 200
