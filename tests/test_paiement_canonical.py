"""IP publique + redirections HTTP de /paiement vers https://pilotcore.fr/paiement."""
import re
import uuid
from pathlib import Path

from config import Config

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_IP = "185.98.131.229"
PAIEMENT_URL = "https://pilotcore.fr/paiement"
FORBIDDEN_HOST_RE = re.compile(r"SCALINGO_HOSTNAME|localhost|10\.100\.4\.")


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


def test_nginx_http_paiement_redirects_to_canonical_https():
    nginx = _read("nginx.conf")
    assert PUBLIC_IP in nginx
    assert "location = /paiement" in nginx
    assert "return 301 https://pilotcore.fr/paiement;" in nginx


def test_http_paiement_redirects_to_canonical_https(client):
    response = client.get("/paiement", follow_redirects=False)
    assert response.status_code == 301
    assert response.headers["Location"] == PAIEMENT_URL


def test_http_paiement_trailing_slash_redirects_to_canonical_https(client):
    response = client.get("/paiement/", follow_redirects=False)
    assert response.status_code == 301
    assert response.headers["Location"] == PAIEMENT_URL


def test_paiement_on_www_or_ip_redirects_to_canonical_https(client):
    for base in (
        "https://www.pilotcore.fr",
        f"https://{PUBLIC_IP}",
        "http://pilotcore.fr",
    ):
        response = client.get("/paiement", base_url=base, follow_redirects=False)
        assert response.status_code == 301, base
        assert response.headers["Location"] == PAIEMENT_URL


def test_https_canonical_paiement_requires_login(client):
    response = client.get(
        "/paiement",
        base_url="https://pilotcore.fr",
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
    assert response.headers["Location"] != PAIEMENT_URL


def test_canonical_paiement_url_does_not_redirect_to_itself(client):
    """https://pilotcore.fr/paiement must never 301 back to the same URL."""
    response = client.get(
        "/paiement",
        base_url="https://pilotcore.fr",
        follow_redirects=False,
    )
    assert response.status_code != 301
    assert response.headers.get("Location") != PAIEMENT_URL


def test_https_canonical_paiement_renders_billing_when_signed_in(client, app):
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

    # Keep the test-client cookie host; the view keys off forwarded proto/host.
    headers = {
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": "pilotcore.fr",
    }
    response = client.get("/paiement", headers=headers, follow_redirects=False)
    assert response.status_code == 200
    billing = client.get("/billing")
    assert billing.status_code == 200
