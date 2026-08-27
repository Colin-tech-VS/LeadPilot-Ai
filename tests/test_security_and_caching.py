"""Security headers, and static assets that can be cached forever.

Two things that are invisible until they are wrong:

* A Content-Security-Policy that omits an origin the templates use does not
  fail here — it fails silently in the visitor's browser. So the policy is
  asserted against the origins the templates actually reference.
* A ``?v=`` token that does not change when the file changes means visitors
  keep a stale script for a year, because the response says ``immutable``.
"""
import re
from pathlib import Path

import pytest

from app.core import production
from app.core.assets import CACHE_FOREVER, CACHE_SHORT, asset_version

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
TEMPLATES = ROOT / "templates"


@pytest.fixture
def prod(app, monkeypatch):
    """Force the production header path — headers are a no-op otherwise."""
    monkeypatch.setattr(production, "is_production", lambda a=None: True)
    return app


# ── Security headers ─────────────────────────────────────────────────────────


def test_the_hardening_headers_are_all_present(prod, client):
    headers = client.get("/pro").headers
    for name in (
        "X-Content-Type-Options",
        "X-Frame-Options",
        "Referrer-Policy",
        "Strict-Transport-Security",
        "Content-Security-Policy",
        "Permissions-Policy",
    ):
        assert name in headers, f"{name} missing"


def test_the_policy_blocks_the_things_that_survive_unsafe_inline(prod, client):
    """Inline scripts force 'unsafe-inline', so these are what carry the weight.

    form-action stops an injected form from posting a sign-up elsewhere,
    base-uri stops a <base> tag repointing every relative URL on the page,
    object-src kills plugin vectors, frame-ancestors covers clickjacking.
    """
    csp = client.get("/pro").headers["Content-Security-Policy"]
    assert "form-action 'self'" in csp
    assert "base-uri 'self'" in csp
    assert "object-src 'none'" in csp
    assert "frame-ancestors 'self'" in csp
    assert "default-src 'self'" in csp


def test_the_policy_allows_every_origin_the_templates_load(prod, client):
    """A missing origin is invisible in tests and broken in the browser."""
    csp = client.get("/pro").headers["Content-Security-Policy"]

    # Only scripts and stylesheets are checked: those are the loads a missing
    # origin turns into a blank page. Images sit under `img-src … https:` and
    # <link rel=preconnect> is not governed by CSP at all.
    used = set()
    for path in list(TEMPLATES.rglob("*.html")) + list((STATIC / "js").glob("*.js")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        used.update(re.findall(r"<script[^>]+src=[\"']https://([a-zA-Z0-9.-]+)", text))
        used.update(
            re.findall(
                r"<link[^>]+rel=[\"']stylesheet[\"'][^>]+href=[\"']https://([a-zA-Z0-9.-]+)",
                text,
            )
        )
        used.update(
            re.findall(
                r"<link[^>]+href=[\"']https://([a-zA-Z0-9.-]+)[^>]+rel=[\"']stylesheet",
                text,
            )
        )

    assert used, "expected the templates to load at least one third-party asset"
    for host in sorted(used):
        assert host in csp, f"{host} is loaded by a template but absent from the CSP"


def test_geolocation_stays_allowed_and_the_rest_does_not(prod, client):
    """The agenda maps live positions; nothing else needs a device permission."""
    policy = client.get("/pro").headers["Permissions-Policy"]
    assert "geolocation=(self)" in policy
    for feature in ("camera", "microphone", "payment", "usb"):
        assert f"{feature}=()" in policy


def test_headers_are_not_applied_outside_production(app, client):
    """Local development must not fight a policy it cannot debug."""
    assert "Content-Security-Policy" not in client.get("/pro").headers


# ── Asset versioning and caching ─────────────────────────────────────────────


def test_static_urls_carry_a_content_digest(app, client):
    html = client.get("/pro").get_data(as_text=True)
    assets = re.findall(r'/static/(?:css|js)/[^"\']+', html)
    assert assets, "the page should load some css or js"
    for url in assets:
        assert re.search(r"\?v=[0-9a-f]{8}$", url), f"{url} has no content digest"


def test_the_digest_follows_the_bytes(app, tmp_path):
    """The whole point: a token that cannot go stale, and does not churn."""
    with app.app_context():
        target = STATIC / "css" / "auth-pro.css"
        first = asset_version("css/auth-pro.css")
        assert first and len(first) == 8
        # Same file, same answer — an unchanged asset keeps its cached copy
        # across deploys instead of being re-downloaded for nothing.
        assert asset_version("css/auth-pro.css") == first
        assert asset_version("css/pro.css") != first
        assert target.exists()


def test_a_missing_file_does_not_break_the_page(app):
    with app.app_context():
        assert asset_version("css/does-not-exist.css") is None


def test_the_digest_never_reaches_outside_static(app):
    """url_for takes template-controlled names, but traversal would hash — and
    thereby confirm the existence of — files outside the static folder."""
    with app.app_context():
        assert asset_version("../config.py") is None
        assert asset_version("../../etc/passwd") is None


def test_a_versioned_asset_is_cached_forever(app, client):
    response = client.get("/static/css/pro.css?v=deadbeef")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == CACHE_FOREVER


def test_an_unversioned_asset_is_only_cached_briefly(app, client):
    response = client.get("/static/css/pro.css")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == CACHE_SHORT


def test_the_service_worker_is_never_frozen(app, client):
    """It is served from /sw.js, outside /static — a bad one cached for a year
    would be unrecoverable for anyone who already had it."""
    response = client.get("/sw.js")
    assert response.status_code == 200
    assert "immutable" not in (response.headers.get("Cache-Control") or "")


def test_no_template_still_hand_maintains_a_version_token():
    """61 of them had drifted apart before the digest replaced them."""
    offenders = [
        str(p.relative_to(ROOT))
        for p in TEMPLATES.rglob("*.html")
        if re.search(r"\}\}\?v=[0-9]+", p.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"manual ?v= tokens are back in: {offenders}"
