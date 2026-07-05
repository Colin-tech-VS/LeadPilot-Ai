"""Lightweight security helpers: a per-process rate limiter and Twilio webhook
signature validation. Both are dependency-free so they add no install weight.

The rate limiter is in-memory (per worker). On a multi-worker deployment that
means the effective limit is roughly ``limit × workers`` — good enough as a
brute-force / abuse speed bump for an MVP, and a clean seam to swap for Redis
later without touching call sites.
"""
import time
from collections import defaultdict, deque
from functools import wraps

from flask import current_app, request

from app.core.errors import AppError

# key -> deque[timestamps]
_HITS = defaultdict(deque)


class RateLimitError(AppError):
    status_code = 429


def _client_ip():
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote_addr or "unknown"


def rate_limit(limit=10, window=60, scope=None):
    """Decorator: allow at most ``limit`` requests per ``window`` seconds per
    client IP (and per ``scope`` label). Raises 429 when exceeded."""

    def decorator(f):
        label = scope or f.__name__

        @wraps(f)
        def wrapped(*args, **kwargs):
            key = f"{label}:{_client_ip()}"
            now = time.monotonic()
            hits = _HITS[key]
            cutoff = now - window
            while hits and hits[0] < cutoff:
                hits.popleft()
            if len(hits) >= limit:
                raise RateLimitError("Trop de tentatives, réessayez plus tard.")
            hits.append(now)
            return f(*args, **kwargs)

        return wrapped

    return decorator


def check_rate(scope, limit=10, window=60):
    """Non-raising variant for HTML flows. Returns True if the request is
    allowed, False if the limit is exceeded. Records the hit when allowed."""
    key = f"{scope}:{_client_ip()}"
    now = time.monotonic()
    hits = _HITS[key]
    cutoff = now - window
    while hits and hits[0] < cutoff:
        hits.popleft()
    if len(hits) >= limit:
        return False
    hits.append(now)
    return True


def validate_twilio_request():
    """Return True if the incoming request carries a valid Twilio signature (or
    validation is disabled / not configurable). False means reject.

    Validation is skipped when TWILIO_VALIDATE_SIGNATURE is off or when no auth
    token is set (can't validate) — in those cases we don't block, we just log.
    """
    if not current_app.config.get("TWILIO_VALIDATE_SIGNATURE", True):
        return True
    auth_token = current_app.config.get("TWILIO_AUTH_TOKEN")
    if not auth_token:
        current_app.logger.warning(
            "TWILIO_AUTH_TOKEN unset — Twilio signature not validated on %s",
            request.path,
        )
        return True

    from twilio.request_validator import RequestValidator

    validator = RequestValidator(auth_token)
    signature = request.headers.get("X-Twilio-Signature", "")
    # Rebuild the exact public URL Twilio signed. Behind Scalingo's TLS
    # terminator the scheme must be forced back to https.
    url = request.url
    proto = request.headers.get("X-Forwarded-Proto")
    if proto == "https" and url.startswith("http://"):
        url = "https://" + url[len("http://") :]
    params = request.form.to_dict()
    return validator.validate(url, params, signature)
