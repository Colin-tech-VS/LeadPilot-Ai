"""Google Search Console — OAuth2 + Search Analytics for the admin console."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote, urlencode

import requests
from flask import current_app, url_for

from app.services import content_studio as content

logger = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
SITES_URL = "https://www.googleapis.com/webmasters/v3/sites"
# Single scope keeps the Google consent screen simpler (fewer verification issues).
SCOPES = "https://www.googleapis.com/auth/webmasters.readonly"

SETTING_REFRESH_TOKEN = "gsc_refresh_token"
SETTING_ACCESS_TOKEN = "gsc_access_token"
SETTING_TOKEN_EXPIRES = "gsc_token_expires_at"
SETTING_USER_EMAIL = "gsc_user_email"
SETTING_SITE_URL = "gsc_site_url"


class GscError(Exception):
    """GSC API or OAuth failure."""


def _cfg(key: str, default: str = "") -> str:
    return (current_app.config.get(key) or default).strip()


def is_configured() -> bool:
    return bool(_cfg("GOOGLE_GSC_CLIENT_ID") and _cfg("GOOGLE_GSC_CLIENT_SECRET"))


def is_connected() -> bool:
    return bool((content.get_setting(SETTING_REFRESH_TOKEN) or "").strip())


def redirect_uri() -> str:
    """Canonical callback URL — must match Google Cloud « Authorized redirect URIs »."""
    base = (current_app.config.get("PUBLIC_BASE_URL") or "").rstrip("/")
    if base:
        return f"{base}/admin/gsc/callback"
    try:
        return url_for("admin.gsc_callback", _external=True)
    except RuntimeError:
        return "/admin/gsc/callback"


def build_auth_url(state: str, *, oauth_redirect_uri: str | None = None) -> str:
    uri = (oauth_redirect_uri or "").strip() or redirect_uri()
    params = {
        "client_id": _cfg("GOOGLE_GSC_CLIENT_ID"),
        "redirect_uri": uri,
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent select_account",
        "state": state,
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def _save_tokens(data: dict) -> None:
    access = data.get("access_token")
    if not access:
        raise GscError("Réponse Google sans jeton d'accès.")
    try:
        content.set_setting(SETTING_ACCESS_TOKEN, access)
        expires_in = int(data.get("expires_in") or 3600)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=max(60, expires_in - 60))
        content.set_setting(SETTING_TOKEN_EXPIRES, expires_at.isoformat())
        refresh = data.get("refresh_token")
        if refresh:
            content.set_setting(SETTING_REFRESH_TOKEN, refresh)
    except Exception as exc:
        logger.exception("Failed to persist GSC tokens")
        raise GscError("Impossible d'enregistrer les jetons Search Console.") from exc


def exchange_code(code: str, *, oauth_redirect_uri: str | None = None) -> None:
    uri = (oauth_redirect_uri or "").strip() or redirect_uri()
    try:
        resp = requests.post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": _cfg("GOOGLE_GSC_CLIENT_ID"),
                "client_secret": _cfg("GOOGLE_GSC_CLIENT_SECRET"),
                "redirect_uri": uri,
                "grant_type": "authorization_code",
            },
            timeout=20,
        )
    except requests.RequestException as exc:
        raise GscError(f"Connexion Google impossible : {exc}") from exc

    try:
        data = resp.json()
    except ValueError as exc:
        raise GscError("Réponse Google invalide.") from exc

    if not resp.ok:
        raise GscError((data.get("error_description") or data.get("error") or resp.text)[:500])
    _save_tokens(data)
    email = _fetch_user_email(data["access_token"])
    if email:
        try:
            content.set_setting(SETTING_USER_EMAIL, email)
        except Exception:
            logger.exception("Failed to save GSC user email")


def _fetch_user_email(access_token: str) -> str | None:
    try:
        resp = requests.get(
            USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=12,
        )
        if resp.ok:
            return (resp.json().get("email") or "").strip() or None
    except requests.RequestException:
        logger.exception("GSC userinfo failed")
    return None


def _token_expired() -> bool:
    raw = content.get_setting(SETTING_TOKEN_EXPIRES)
    if not raw:
        return True
    try:
        expires = datetime.fromisoformat(raw)
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= expires
    except ValueError:
        return True


def _refresh_access_token() -> str:
    refresh = (content.get_setting(SETTING_REFRESH_TOKEN) or "").strip()
    if not refresh:
        raise GscError("Search Console non connecté.")
    resp = requests.post(
        TOKEN_URL,
        data={
            "client_id": _cfg("GOOGLE_GSC_CLIENT_ID"),
            "client_secret": _cfg("GOOGLE_GSC_CLIENT_SECRET"),
            "refresh_token": refresh,
            "grant_type": "refresh_token",
        },
        timeout=20,
    )
    data = resp.json()
    if not resp.ok:
        msg = (data.get("error_description") or data.get("error") or resp.text)[:500]
        raise GscError(msg)
    _save_tokens(data)
    return data["access_token"]


def access_token() -> str:
    cached = (content.get_setting(SETTING_ACCESS_TOKEN) or "").strip()
    if cached and not _token_expired():
        return cached
    return _refresh_access_token()


def disconnect() -> None:
    for key in (
        SETTING_REFRESH_TOKEN,
        SETTING_ACCESS_TOKEN,
        SETTING_TOKEN_EXPIRES,
        SETTING_USER_EMAIL,
        SETTING_SITE_URL,
    ):
        content.set_setting(key, "")


def _api_request(method: str, url: str, **kwargs):
    token = access_token()
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bearer {token}"
    resp = requests.request(method, url, headers=headers, timeout=25, **kwargs)
    if resp.status_code == 401:
        token = _refresh_access_token()
        headers["Authorization"] = f"Bearer {token}"
        resp = requests.request(method, url, headers=headers, timeout=25, **kwargs)
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if not resp.ok:
        err = data.get("error", {})
        message = err.get("message") if isinstance(err, dict) else str(err or resp.text)
        raise GscError((message or "Erreur API Search Console")[:500])
    return data


def list_sites() -> list[dict]:
    data = _api_request("GET", SITES_URL)
    entries = data.get("siteEntry") or []
    sites = []
    for entry in entries:
        url = entry.get("siteUrl")
        if not url:
            continue
        sites.append(
            {
                "site_url": url,
                "permission_level": entry.get("permissionLevel") or "—",
            }
        )
    sites.sort(key=lambda s: s["site_url"])
    return sites


def _guess_site_url(sites: list[dict]) -> str | None:
    if not sites:
        return None
    base = (current_app.config.get("PUBLIC_BASE_URL") or "").rstrip("/")
    candidates = []
    if base:
        candidates.extend([f"{base}/", base, base.replace("https://", "sc-domain:")])
        host = base.split("://", 1)[-1]
        candidates.append(f"sc-domain:{host.removeprefix('www.')}")
    for candidate in candidates:
        for site in sites:
            if site["site_url"] == candidate:
                return candidate
    return sites[0]["site_url"]


def selected_site_url(sites: list[dict] | None = None) -> str | None:
    saved = (content.get_setting(SETTING_SITE_URL) or "").strip()
    if saved:
        return saved
    if sites is None:
        try:
            sites = list_sites()
        except GscError:
            return None
    guessed = _guess_site_url(sites)
    if guessed:
        content.set_setting(SETTING_SITE_URL, guessed)
    return guessed


def set_site_url(site_url: str) -> None:
    content.set_setting(SETTING_SITE_URL, (site_url or "").strip())


def _date_range(days: int = 28) -> tuple[str, str]:
    end = date.today() - timedelta(days=3)
    start = end - timedelta(days=max(1, days) - 1)
    return start.isoformat(), end.isoformat()


def search_analytics(
    site_url: str,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    dimensions: list[str] | None = None,
    row_limit: int = 25,
) -> dict:
    if not start_date or not end_date:
        start_date, end_date = _date_range(28)
    body = {
        "startDate": start_date,
        "endDate": end_date,
        "rowLimit": row_limit,
    }
    if dimensions:
        body["dimensions"] = dimensions
    encoded = quote(site_url, safe="")
    url = f"https://www.googleapis.com/webmasters/v3/sites/{encoded}/searchAnalytics/query"
    return _api_request("POST", url, json=body)


def _sum_metrics(rows: list[dict]) -> dict:
    clicks = impressions = 0
    weighted = 0.0
    for row in rows:
        clicks += int(row.get("clicks") or 0)
        imp = int(row.get("impressions") or 0)
        impressions += imp
        weighted += float(row.get("position") or 0) * imp
    ctr = (clicks / impressions * 100) if impressions else 0.0
    position = (weighted / impressions) if impressions else 0.0
    return {
        "clicks": clicks,
        "impressions": impressions,
        "ctr": round(ctr, 2),
        "position": round(position, 1),
    }


def dashboard_payload(days: int = 28) -> dict:
    sites = list_sites()
    site_url = selected_site_url(sites)
    if not site_url:
        return {
            "sites": sites,
            "site_url": None,
            "summary": None,
            "queries": [],
            "pages": [],
            "error": "Aucune propriété Search Console sélectionnée.",
        }

    start_date, end_date = _date_range(days)
    try:
        totals = search_analytics(site_url, start_date=start_date, end_date=end_date, dimensions=["date"], row_limit=1000)
        summary = _sum_metrics(totals.get("rows") or [])
        queries = search_analytics(
            site_url, start_date=start_date, end_date=end_date, dimensions=["query"], row_limit=15
        ).get("rows") or []
        pages = search_analytics(
            site_url, start_date=start_date, end_date=end_date, dimensions=["page"], row_limit=15
        ).get("rows") or []
        return {
            "sites": sites,
            "site_url": site_url,
            "start_date": start_date,
            "end_date": end_date,
            "summary": summary,
            "queries": queries,
            "pages": pages,
            "error": None,
        }
    except GscError as exc:
        return {
            "sites": sites,
            "site_url": site_url,
            "summary": None,
            "queries": [],
            "pages": [],
            "error": str(exc),
        }


def status() -> dict:
    return {
        "configured": is_configured(),
        "connected": is_connected(),
        "redirect_uri": redirect_uri(),
        "client_id": _cfg("GOOGLE_GSC_CLIENT_ID"),
        "user_email": content.get_setting(SETTING_USER_EMAIL) or "",
        "site_url": content.get_setting(SETTING_SITE_URL) or "",
        "console_url": "https://search.google.com/search-console",
        "cloud_console_url": "https://console.cloud.google.com/apis/credentials",
    }
