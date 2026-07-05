"""Server-side page-view tracking for the GA4-style traffic dashboard.

Registered as an ``after_request`` hook: every HTML page the public site / app
serves is recorded, with a visitor cookie (uniques) and a session cookie
(sessions / bounce rate). No external analytics, no client script required.
Admin pages, assets, API and webhook routes are never tracked.
"""
import hashlib
import re
import uuid
from datetime import datetime, timezone

from flask import request

from app.core.extensions import db
from app.models.page_view import PageView

VISITOR_COOKIE = "lp_vid"
SESSION_COOKIE = "lp_sid"
VISITOR_MAX_AGE = 60 * 60 * 24 * 365 * 2  # 2 years
SESSION_MAX_AGE = 60 * 30  # 30 min sliding window ≈ one session

# Paths we never count as "traffic".
_SKIP_PREFIXES = ("/admin", "/static", "/voice", "/webhook", "/api", "/health",
                  "/sw.js", "/manifest", "/robots", "/sitemap", "/favicon")

_BOT_RE = re.compile(r"bot|crawl|spider|slurp|bingpreview|facebookexternal|monitor|curl|wget|python-requests", re.I)


def _should_track(response):
    if request.method != "GET":
        return False
    if response.status_code != 200:
        return False
    ctype = response.headers.get("Content-Type", "")
    if "text/html" not in ctype:
        return False
    path = request.path or "/"
    return not any(path.startswith(p) for p in _SKIP_PREFIXES)


def _client_ip():
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote_addr or ""


def _device(ua):
    if _BOT_RE.search(ua):
        return "bot"
    if re.search(r"mobi|android|iphone|ipad|ipod", ua, re.I):
        return "mobile"
    return "desktop"


def _referrer_host(ref):
    if not ref:
        return None
    m = re.match(r"https?://([^/]+)", ref)
    return m.group(1) if m else None


def register_tracking(app):
    @app.after_request
    def _track(response):
        try:
            if not _should_track(response):
                return response

            visitor_id = request.cookies.get(VISITOR_COOKIE)
            session_id = request.cookies.get(SESSION_COOKIE)
            new_visitor = not visitor_id
            new_session = not session_id
            if new_visitor:
                visitor_id = uuid.uuid4().hex
            if new_session:
                session_id = uuid.uuid4().hex

            ua = request.headers.get("User-Agent", "")[:300]
            device = _device(ua)

            # Don't pollute the stats with bots, but still keep the cookies.
            if device != "bot":
                ref = request.referrer
                ip = _client_ip()
                pv = PageView(
                    visitor_id=visitor_id,
                    session_id=session_id,
                    path=(request.path or "/")[:500],
                    referrer=(ref or "")[:500] or None,
                    referrer_host=_referrer_host(ref),
                    user_agent=ua or None,
                    device=device,
                    lang=(request.accept_languages.best or "")[:10] or None,
                    ip_hash=hashlib.sha256(ip.encode()).hexdigest() if ip else None,
                    is_new_session=new_session,
                )
                db.session.add(pv)
                db.session.commit()

            # (Re)set cookies — session cookie slides on every view.
            secure = bool(app.config.get("SESSION_COOKIE_SECURE"))
            if new_visitor:
                response.set_cookie(VISITOR_COOKIE, visitor_id, max_age=VISITOR_MAX_AGE,
                                    httponly=True, samesite="Lax", secure=secure)
            response.set_cookie(SESSION_COOKIE, session_id, max_age=SESSION_MAX_AGE,
                                httponly=True, samesite="Lax", secure=secure)
        except Exception:  # tracking must never break a page
            app.logger.exception("page-view tracking failed")
            try:
                db.session.rollback()
            except Exception:
                pass
        return response
