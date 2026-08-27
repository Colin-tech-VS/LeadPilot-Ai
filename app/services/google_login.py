"""« Se connecter avec Google » — OAuth2 sign-in for artisan accounts.

Deliberately thin, and modelled on :mod:`app.services.google_gsc`: the same
Google endpoints, the same ``requests`` calls, no new dependency. The code is
exchanged server-side over TLS with the client secret, so the identity Google
returns needs no further signature check — we never accept an ID token handed
to us by the browser.

Google gives us an e-mail and a name. It cannot give us a company name or a
trade, and an artisan account is worthless without a tenant, so sign-in does
not replace the sign-up form: it removes the password and the typing, and the
form still asks the two things only the artisan knows.
"""
from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

import requests
from flask import current_app, url_for

logger = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

# Sign-in only. Nothing here reads Gmail, Drive or anything else, which keeps
# the consent screen to the "basic" tier that needs no Google verification.
SCOPES = "openid email profile"

TIMEOUT = 15


class GoogleLoginError(Exception):
    """OAuth exchange failed, or Google returned an unusable identity."""


@dataclass(frozen=True)
class GoogleIdentity:
    """What Google tells us about the person who just signed in."""

    sub: str
    email: str
    email_verified: bool
    first_name: str | None = None
    last_name: str | None = None

    def as_session_payload(self) -> dict:
        return {
            "sub": self.sub,
            "email": self.email,
            "first_name": self.first_name,
            "last_name": self.last_name,
        }


def _cfg(key: str, default: str = "") -> str:
    return (current_app.config.get(key) or default).strip()


def is_configured() -> bool:
    """No credentials → the button is never rendered and the routes 404.

    Sign-in must stay optional: the app has to boot and serve /login and
    /register perfectly well on an install that has never touched Google.
    """
    return bool(_cfg("GOOGLE_OAUTH_CLIENT_ID") and _cfg("GOOGLE_OAUTH_CLIENT_SECRET"))


def redirect_uri() -> str:
    """Canonical callback — must match Google Cloud « Authorized redirect URIs ».

    Pinned to PUBLIC_BASE_URL rather than the request host so that a visit over
    a preview domain, or behind a proxy that rewrites Host, cannot produce a URI
    Google will reject.
    """
    base = (current_app.config.get("PUBLIC_BASE_URL") or "").rstrip("/")
    if base:
        return f"{base}/auth/google/callback"
    try:
        return url_for("web.google_callback", _external=True)
    except RuntimeError:
        return "/auth/google/callback"


def new_state() -> str:
    """CSRF token for the round trip. Stored in the session, echoed by Google."""
    return secrets.token_urlsafe(24)


def build_auth_url(state: str) -> str:
    params = {
        "client_id": _cfg("GOOGLE_OAUTH_CLIENT_ID"),
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": SCOPES,
        # Artisans often share a phone with family; always let them pick.
        "prompt": "select_account",
        "state": state,
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def exchange_code(code: str) -> GoogleIdentity:
    """Trade the one-shot code for an access token, then read the profile."""
    try:
        response = requests.post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": _cfg("GOOGLE_OAUTH_CLIENT_ID"),
                "client_secret": _cfg("GOOGLE_OAUTH_CLIENT_SECRET"),
                "redirect_uri": redirect_uri(),
                "grant_type": "authorization_code",
            },
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        raise GoogleLoginError("Could not reach Google") from exc

    if response.status_code != 200:
        logger.warning("Google token exchange failed: %s %s", response.status_code, response.text[:300])
        raise GoogleLoginError("Google refused the authorization code")

    access_token = (response.json() or {}).get("access_token")
    if not access_token:
        raise GoogleLoginError("Google returned no access token")

    return _fetch_identity(access_token)


def _fetch_identity(access_token: str) -> GoogleIdentity:
    try:
        response = requests.get(
            USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        raise GoogleLoginError("Could not read the Google profile") from exc

    if response.status_code != 200:
        logger.warning("Google userinfo failed: %s %s", response.status_code, response.text[:300])
        raise GoogleLoginError("Google refused the profile request")

    data = response.json() or {}
    sub = (data.get("id") or "").strip()
    email = (data.get("email") or "").strip().lower()
    if not sub or not email:
        raise GoogleLoginError("Google returned no usable identity")

    return GoogleIdentity(
        sub=sub,
        email=email,
        # An unverified address must never take over an existing account by
        # e-mail match — anyone can put any address on a fresh Google profile.
        email_verified=bool(data.get("verified_email")),
        first_name=(data.get("given_name") or "").strip() or None,
        last_name=(data.get("family_name") or "").strip() or None,
    )
