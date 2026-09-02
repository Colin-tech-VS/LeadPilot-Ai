"""Force the public site onto https://www.pilotcore.fr.

Apex (pilotcore.fr) and raw IP hosts — public or RFC1918 such as
10.100.4.106 — must never be the canonical origin. Probes treat a redirect
to an internal address as « site injoignable ». Health endpoints stay on
the incoming host so Scalingo's internal probes keep returning 200.
"""
from __future__ import annotations

import ipaddress

from flask import redirect, request

CANONICAL_HOST = "www.pilotcore.fr"
CANONICAL_ORIGIN = f"https://{CANONICAL_HOST}"
APEX_HOST = "pilotcore.fr"

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "testserver"}
_HEALTH_PATHS = {"/health", "/health/ready", "/api/health", "/api"}


def forwarded_proto() -> str:
    raw = request.headers.get("X-Forwarded-Proto") or request.scheme or ""
    return raw.split(",")[0].strip().lower()


def forwarded_host() -> str:
    raw = request.headers.get("X-Forwarded-Host") or request.host or ""
    host = raw.split(",")[0].strip().split(":")[0].lower()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    return host


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def canonical_location(path: str | None = None) -> str:
    path = request.path if path is None else path
    if not path.startswith("/"):
        path = f"/{path}"
    url = f"{CANONICAL_ORIGIN}{path}"
    qs = request.query_string.decode() if request.query_string else ""
    if qs:
        url += f"?{qs}"
    return url


def should_redirect_to_canonical() -> bool:
    if request.path in _HEALTH_PATHS:
        return False
    host = forwarded_host()
    if not host or host in _LOCAL_HOSTS:
        return False
    proto = forwarded_proto()
    if _is_ip(host) or host == APEX_HOST:
        return True
    if host == CANONICAL_HOST and proto != "https":
        return True
    return False


def register_canonical_host(app) -> None:
    @app.before_request
    def _redirect_apex_and_ips_to_www():
        if should_redirect_to_canonical():
            return redirect(canonical_location(), code=301)
