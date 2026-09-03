"""Permanent redirect from www.pilotcore.fr to the apex host.

Search Console still tracks the www property; the public site origin is
https://pilotcore.fr. Only the www host is rewritten — never an IP (public or
RFC1918), so Scalingo probes that arrive on an internal address keep a 200
instead of a Location the healthcheck would refuse.

Health endpoints stay on the incoming host. POST/PUT/PATCH/DELETE are left
alone so Twilio and Stripe webhooks pinned to PUBLIC_BASE_URL keep working.
"""
from __future__ import annotations

from flask import redirect, request

WWW_HOST = "www.pilotcore.fr"
CANONICAL_HOST = "pilotcore.fr"
CANONICAL_ORIGIN = f"https://{CANONICAL_HOST}"

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "testserver"}
_HEALTH_PATHS = {"/health", "/health/ready", "/api/health", "/api"}
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def forwarded_host() -> str:
    raw = request.headers.get("X-Forwarded-Host") or request.host or ""
    host = raw.split(",")[0].strip().split(":")[0].lower()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    return host


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
    host = forwarded_host()
    if not host or host in _LOCAL_HOSTS:
        return False
    return host == WWW_HOST


def register_canonical_host(app) -> None:
    @app.before_request
    def _redirect_www_to_apex():
        if should_redirect_www_to_apex():
            return redirect(canonical_location(), code=301)
