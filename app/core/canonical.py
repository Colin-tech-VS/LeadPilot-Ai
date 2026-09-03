"""Do not 301 between www.pilotcore.fr and pilotcore.fr.

Live Apache/LWS still issues ``Redirect / https://www.pilotcore.fr/``
(iso-8859-1). www is a CNAME of the apex, so that rule 301s
https://www.pilotcore.fr/ to itself (TooManyRedirects) on every path,
including /api/health. Flask echoing the opposite hop (www → apex) or
the same hop (apex → www) recreates the loop as soon as a request
reaches this app.

Serve both public hosts. HTTP → HTTPS keeps the same host. The public
IPv4 is rewritten to https://www.pilotcore.fr. Health probes and
mutating methods are never rewritten.

A stale ``X-Forwarded-Host: www`` on an apex request must not win.
"""
from __future__ import annotations

import ipaddress

from flask import redirect, request

WWW_HOST = "www.pilotcore.fr"
APEX_HOST = "pilotcore.fr"
CANONICAL_HOST = WWW_HOST
CANONICAL_ORIGIN = f"https://{CANONICAL_HOST}"
PUBLIC_HOSTS = {WWW_HOST, APEX_HOST}

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

    Prefer the direct Host when it is already a public name. A reverse
    proxy that still injects the other public host in X-Forwarded-Host
    must not trigger a 301 to that other host.
    """
    direct = _parse_host(request.host)
    forwarded = _parse_host(request.headers.get("X-Forwarded-Host"))
    if direct in PUBLIC_HOSTS:
        return direct
    if forwarded:
        return forwarded
    return direct


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
    host = visible_host()
    if host in PUBLIC_HOSTS:
        origin = f"https://{host}"
    else:
        origin = CANONICAL_ORIGIN
    path = request.path or "/"
    if not path.startswith("/"):
        path = f"/{path}"
    url = f"{origin}{path}"
    qs = request.query_string.decode() if request.query_string else ""
    if qs:
        url += f"?{qs}"
    return url


def should_redirect_to_canonical() -> bool:
    if request.method not in _SAFE_METHODS:
        return False
    if request.path in _HEALTH_PATHS:
        return False
    host = visible_host()
    if not host or host in _LOCAL_HOSTS:
        return False
    if _is_private_or_loopback(host):
        return False
    # Never 301 www ↔ apex. Either hop fights LWS force-www + CNAME.
    if host in PUBLIC_HOSTS:
        return forwarded_proto() == "http"
    if _is_public_ip(host):
        return True
    return False


def register_canonical_host(app) -> None:
    @app.before_request
    def _redirect_http_and_public_ip():
        if should_redirect_to_canonical():
            return redirect(canonical_location(), code=301)
