"""Permanent redirect from www.pilotcore.fr to the apex host.

Search Console still tracks the www property; the public site origin is
https://pilotcore.fr. Only the www host is rewritten — never an IP (public or
RFC1918), so Scalingo probes that arrive on an internal address keep a 200
instead of a Location the healthcheck would refuse.

Health endpoints stay on the incoming host. POST/PUT/PATCH/DELETE are left
alone so Twilio and Stripe webhooks pinned to PUBLIC_BASE_URL keep working.

A stale ``X-Forwarded-Host: www`` on an apex request must not win: that 301s
to https://pilotcore.fr while the browser is already there (TooManyRedirects).
The LWS Apache edge historically forced www; Flask must not bounce back.
"""
from __future__ import annotations

from flask import redirect, request

WWW_HOST = "www.pilotcore.fr"
CANONICAL_HOST = "pilotcore.fr"
CANONICAL_ORIGIN = f"https://{CANONICAL_HOST}"

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "testserver"}
_HEALTH_PATHS = {"/health", "/health/ready", "/api/health", "/api"}
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _parse_host(raw: str | None) -> str:
    host = (raw or "").split(",")[0].strip().split(":")[0].lower()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    return host


def forwarded_proto() -> str:
    raw = request.headers.get("X-Forwarded-Proto") or request.scheme or ""
    return raw.split(",")[0].strip().lower()


def forwarded_host() -> str:
    return _parse_host(request.headers.get("X-Forwarded-Host") or request.host or "")


def visible_host() -> str:
    """Host the client actually addressed.

    Prefer the direct Host when it is already the apex. A reverse proxy that
    still injects ``X-Forwarded-Host: www.pilotcore.fr`` on https://pilotcore.fr
    must not trigger a 301 to the same URL.
    """
    direct = _parse_host(request.host)
    forwarded = _parse_host(request.headers.get("X-Forwarded-Host"))
    if direct == CANONICAL_HOST:
        return direct
    if forwarded:
        return forwarded
    return direct


def canonical_location() -> str:
    path = request.path or "/"
    if not path.startswith("/"):
        path = f"/{path}"
    url = f"{CANONICAL_ORIGIN}{path}"
    qs = request.query_string.decode() if request.query_string else ""
    if qs:
        url += f"?{qs}"
    return url


def should_redirect_www_to_apex() -> bool:
    if request.method not in _SAFE_METHODS:
        return False
    if request.path in _HEALTH_PATHS:
        return False
    host = visible_host()
    if not host or host in _LOCAL_HOSTS:
        return False
    if host == CANONICAL_HOST:
        return False
    if host != WWW_HOST:
        return False
    target = canonical_location()
    incoming = (request.url or "").split("#", 1)[0]
    return incoming.rstrip("/") != target.rstrip("/")


def register_canonical_host(app) -> None:
    @app.before_request
    def _redirect_www_to_apex():
        if should_redirect_www_to_apex():
            return redirect(canonical_location(), code=301)
