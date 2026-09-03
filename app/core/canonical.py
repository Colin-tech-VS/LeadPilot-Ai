"""Align Flask with the Apache/LWS edge: serve https://www.pilotcore.fr.

Live DNS splits the two public hosts:

* ``www.pilotcore.fr`` reaches Flask (Scalingo).
* ``pilotcore.fr`` is answered by Apache/LWS, which already 301s to www
  (force-www). Search Console tracks the www property.

Commit ``d66875a`` made Flask 301 www → apex. That is the opposite hop of
Apache's apex → www, so browsers hit TooManyRedirects. Flask and nginx must
never send www back to the apex.

Only the apex hostname and the public IPv4 are rewritten — never an RFC1918
or loopback address, so Scalingo probes that arrive on an internal host keep
a 200 instead of a Location the healthcheck would refuse.

Health endpoints stay on the incoming host. POST/PUT/PATCH/DELETE are left
alone so Twilio and Stripe webhooks pinned to PUBLIC_BASE_URL keep working.
"""
from __future__ import annotations

import ipaddress

from flask import redirect, request

CANONICAL_HOST = "www.pilotcore.fr"
CANONICAL_ORIGIN = f"https://{CANONICAL_HOST}"
APEX_HOST = "pilotcore.fr"

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "testserver"}
_HEALTH_PATHS = {"/health", "/health/ready", "/api/health", "/api"}
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def forwarded_proto() -> str:
    raw = request.headers.get("X-Forwarded-Proto") or request.scheme or ""
    return raw.split(",")[0].strip().lower()


def forwarded_host() -> str:
    raw = request.headers.get("X-Forwarded-Host") or request.host or ""
    host = raw.split(",")[0].strip().split(":")[0].lower()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    return host


def _ip_or_none(host: str):
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def _is_private_or_loopback(host: str) -> bool:
    ip = _ip_or_none(host)
    if ip is None:
        return False
    return bool(ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved)


def _is_public_ip(host: str) -> bool:
    ip = _ip_or_none(host)
    return ip is not None and not _is_private_or_loopback(host)


def canonical_location() -> str:
    path = request.path or "/"
    if not path.startswith("/"):
        path = f"/{path}"
    url = f"{CANONICAL_ORIGIN}{path}"
    qs = request.query_string.decode() if request.query_string else ""
    if qs:
        url += f"?{qs}"
    return url


def should_redirect_to_canonical() -> bool:
    if request.method not in _SAFE_METHODS:
        return False
    if request.path in _HEALTH_PATHS:
        return False
    host = forwarded_host()
    if not host or host in _LOCAL_HOSTS:
        return False
    if _is_private_or_loopback(host):
        return False
    proto = forwarded_proto()
    if host == APEX_HOST or _is_public_ip(host):
        return True
    if host == CANONICAL_HOST and proto != "https":
        return True
    return False


def register_canonical_host(app) -> None:
    @app.before_request
    def _redirect_apex_to_www():
        if should_redirect_to_canonical():
            return redirect(canonical_location(), code=301)
