"""LinkedIn publishing (admin /admin/social).

Default products on a new LinkedIn developer app are Sign In with LinkedIn
and Share on LinkedIn. Those scopes post as the authenticated member
(``urn:li:person:{id}``). Company-page posting
(``urn:li:organization:{id}``) needs Community Management API, which LinkedIn
often leaves behind « Request access » — we never request those scopes unless
the app actually has them, or OAuth fails with LinkedIn's generic
« Bummer, something went wrong » page.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode

import requests
from flask import current_app

from app.core.extensions import db
from app.models.social_post import SocialPost
from app.services import content_studio as content
from app.services.events import CAT_ADMIN, LEVEL_ERROR, LEVEL_SUCCESS, log_event

logger = logging.getLogger(__name__)

AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
REST_BASE = "https://api.linkedin.com/rest"
V2_BASE = "https://api.linkedin.com/v2"
USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
LINKEDIN_VERSION = "202605"

# Products actually granted on the PilotCore app: Sign In with LinkedIn (OpenID)
# + Share on LinkedIn. Requesting w_organization_social without Community
# Management API makes LinkedIn abort OAuth (« Bummer… ») and send the user
# back to the app website URL instead of the callback.
OAUTH_SCOPES = "openid profile w_member_social"

SETTING_ORG_ID = "linkedin_org_id"
SETTING_ORG_NAME = "linkedin_org_name"
SETTING_ACCESS_TOKEN = "linkedin_access_token"
SETTING_REFRESH_TOKEN = "linkedin_refresh_token"
SETTING_TOKEN_EXPIRES = "linkedin_token_expires_at"
SETTING_MEMBER_ID = "linkedin_member_id"
SETTING_MEMBER_NAME = "linkedin_member_name"

APP_VERIFICATION_URL = (
    "https://www.linkedin.com/developers/apps/verification/"
    "a7910099-0f14-415c-b7a4-9850c46a4380"
)

SHARE_ON_LINKEDIN_HINT = (
    "L'app LinkedIn a Sign In with LinkedIn et Share on LinkedIn — pas "
    "Community Management API (Request access est bloqué). Les posts partent "
    "donc sur le profil du compte qui autorise, pas au nom de la page entreprise."
)

COMMUNITY_API_HINT = SHARE_ON_LINKEDIN_HINT

_ORG_ID_RE = re.compile(
    r"(?:urn:li:organization:|/company/|organization[:/])(\d+)",
    re.IGNORECASE,
)


class LinkedInError(Exception):
    """LinkedIn API or OAuth failure with a user-facing message."""


def _cfg(key: str, default: str = "") -> str:
    try:
        return (current_app.config.get(key) or default).strip()
    except RuntimeError:
        return default


def app_credentials_ready() -> bool:
    return bool(_cfg("LINKEDIN_CLIENT_ID") and _cfg("LINKEDIN_CLIENT_SECRET"))


def linkedin_oauth_redirect_uri() -> str:
    from app.utils.seo import site_base_url

    return f"{site_base_url()}/admin/social/linkedin/callback"


def oauth_url(state: str, redirect_uri: str | None = None) -> str:
    uri = redirect_uri or linkedin_oauth_redirect_uri()
    query = urlencode(
        {
            "response_type": "code",
            "client_id": _cfg("LINKEDIN_CLIENT_ID"),
            "redirect_uri": uri,
            "state": state,
            "scope": OAUTH_SCOPES,
        }
    )
    return f"{AUTH_URL}?{query}"


def get_config() -> dict:
    org_id = content.get_setting(SETTING_ORG_ID, "") or ""
    member_id = content.get_setting(SETTING_MEMBER_ID, "") or ""
    return {
        "org_id": org_id,
        "org_name": content.get_setting(SETTING_ORG_NAME, "") or "",
        "member_id": member_id,
        "member_name": content.get_setting(SETTING_MEMBER_NAME, "") or "",
        "has_refresh": bool((content.get_setting(SETTING_REFRESH_TOKEN, "") or "").strip()),
        "publish_as": "organization" if org_id else ("member" if member_id else ""),
    }


def has_token() -> bool:
    return bool(
        (content.get_setting(SETTING_ACCESS_TOKEN, "") or "").strip()
        or (content.get_setting(SETTING_REFRESH_TOKEN, "") or "").strip()
    )


def is_configured() -> bool:
    cfg = get_config()
    return bool(has_token() and (cfg["org_id"] or cfg["member_id"]))


def normalize_org_id(raw: str) -> str:
    """Numeric organization id from a URN, admin URL, or digits."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    match = _ORG_ID_RE.search(raw)
    if match:
        return match.group(1)
    if raw.isdigit():
        return raw
    return ""


def org_urn(org_id: str) -> str:
    return f"urn:li:organization:{normalize_org_id(org_id) or org_id}"


def person_urn(member_id: str) -> str:
    return f"urn:li:person:{(member_id or '').strip()}"


def author_urn() -> str | None:
    cfg = get_config()
    if cfg["org_id"]:
        return org_urn(cfg["org_id"])
    if cfg["member_id"]:
        return person_urn(cfg["member_id"])
    return None


def disconnect() -> None:
    for key in (
        SETTING_ORG_ID,
        SETTING_ORG_NAME,
        SETTING_ACCESS_TOKEN,
        SETTING_REFRESH_TOKEN,
        SETTING_TOKEN_EXPIRES,
        SETTING_MEMBER_ID,
        SETTING_MEMBER_NAME,
    ):
        content.set_setting(key, "")


def _safe_json(resp) -> dict:
    try:
        data = resp.json() if getattr(resp, "content", None) else {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _api_error(data, fallback: str = "Erreur LinkedIn") -> str:
    if not isinstance(data, dict):
        return fallback
    msg = (
        data.get("error_description")
        or data.get("message")
        or data.get("error")
        or fallback
    )
    return str(msg)[:500]


def _is_community_denied(status: int, data) -> bool:
    if status not in (401, 403):
        return False
    text = _api_error(data, "").lower()
    needles = (
        "community management",
        "not enough permissions",
        "access denied",
        "unauthorized",
        "partner",
        "product",
        "w_organization_social",
    )
    return any(n in text for n in needles) or status == 403


def _rest_headers(token: str, *, json_body: bool = False) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "Linkedin-Version": LINKEDIN_VERSION,
        "X-Restli-Protocol-Version": "2.0.0",
    }
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers


def _save_token_payload(data: dict, *, keep_refresh: bool = True) -> None:
    access = (data.get("access_token") or "").strip()
    if not access:
        raise LinkedInError("Réponse LinkedIn sans jeton d'accès.")
    content.set_setting(SETTING_ACCESS_TOKEN, access)
    expires_in = int(data.get("expires_in") or 5184000)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=max(120, expires_in - 120))
    content.set_setting(SETTING_TOKEN_EXPIRES, expires_at.isoformat())
    refresh = (data.get("refresh_token") or "").strip()
    if refresh:
        content.set_setting(SETTING_REFRESH_TOKEN, refresh)
    elif not keep_refresh:
        content.set_setting(SETTING_REFRESH_TOKEN, "")


def exchange_oauth_code(code: str, redirect_uri: str) -> tuple[dict | None, str | None]:
    code = (code or "").strip()
    if not code:
        return None, "Code d'autorisation LinkedIn manquant."
    if not app_credentials_ready():
        return None, "LINKEDIN_CLIENT_ID et LINKEDIN_CLIENT_SECRET sont requis."
    try:
        resp = requests.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": _cfg("LINKEDIN_CLIENT_ID"),
                "client_secret": _cfg("LINKEDIN_CLIENT_SECRET"),
            },
            timeout=20,
        )
        data = _safe_json(resp)
        if resp.ok and data.get("access_token"):
            return data, None
        err = _api_error(data, "Échange du code LinkedIn impossible.")
        logger.warning("LinkedIn OAuth code exchange failed: %s", data)
        return None, err
    except requests.RequestException as exc:
        logger.exception("LinkedIn OAuth code exchange failed")
        return None, str(exc)


def _refresh_access_token() -> str | None:
    refresh = (content.get_setting(SETTING_REFRESH_TOKEN, "") or "").strip()
    if not refresh or not app_credentials_ready():
        return None
    try:
        resp = requests.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh,
                "client_id": _cfg("LINKEDIN_CLIENT_ID"),
                "client_secret": _cfg("LINKEDIN_CLIENT_SECRET"),
            },
            timeout=20,
        )
        data = _safe_json(resp)
        if resp.ok and data.get("access_token"):
            _save_token_payload(data)
            return data["access_token"]
        logger.warning("LinkedIn token refresh failed: %s", data)
    except requests.RequestException:
        logger.exception("LinkedIn token refresh failed")
    return None


def _token_expired() -> bool:
    raw = (content.get_setting(SETTING_TOKEN_EXPIRES, "") or "").strip()
    if not raw:
        return False
    try:
        expires = datetime.fromisoformat(raw)
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= expires
    except ValueError:
        return False


def ensure_access_token() -> str:
    token = (content.get_setting(SETTING_ACCESS_TOKEN, "") or "").strip()
    if token and not _token_expired():
        return token
    refreshed = _refresh_access_token()
    if refreshed:
        return refreshed
    if token:
        return token
    raise LinkedInError("Aucun jeton LinkedIn. Reconnectez LinkedIn.")


def save_pasted_token(token: str) -> None:
    token = (token or "").strip()
    if not token:
        raise LinkedInError("Jeton LinkedIn requis.")
    _save_token_payload(
        {"access_token": token, "expires_in": 5184000},
        keep_refresh=True,
    )


def fetch_member_profile(token: str) -> dict:
    """Person id + display name. OpenID userinfo first, then GET /v2/me."""
    member_id = ""
    name = ""
    try:
        resp = requests.get(
            USERINFO_URL,
            headers={"Authorization": f"Bearer {token}"},
            timeout=12,
        )
        if resp.ok:
            data = resp.json() or {}
            member_id = str(data.get("sub") or "").strip()
            name = (data.get("name") or "").strip()
            if not name:
                given = (data.get("given_name") or "").strip()
                family = (data.get("family_name") or "").strip()
                name = f"{given} {family}".strip()
    except requests.RequestException:
        logger.exception("LinkedIn userinfo failed")
    if not member_id:
        try:
            resp = requests.get(
                f"{V2_BASE}/me",
                headers={"Authorization": f"Bearer {token}"},
                timeout=12,
            )
            data = _safe_json(resp)
            if resp.ok:
                member_id = str(data.get("id") or "").strip()
                if not name:
                    given = (data.get("localizedFirstName") or "").strip()
                    family = (data.get("localizedLastName") or "").strip()
                    name = f"{given} {family}".strip()
        except requests.RequestException:
            logger.exception("LinkedIn /me failed")
    return {"id": member_id, "name": name}


def _fetch_member_name(token: str) -> str | None:
    profile = fetch_member_profile(token)
    return profile.get("name") or None


def fetch_org_name(token: str, org_id: str) -> str | None:
    org_id = normalize_org_id(org_id)
    if not token or not org_id:
        return None
    try:
        resp = requests.get(
            f"{REST_BASE}/organizations/{org_id}",
            headers=_rest_headers(token),
            timeout=15,
        )
        data = _safe_json(resp)
        if resp.ok:
            name = (
                data.get("localizedName")
                or (data.get("name") or {}).get("localized")
                or data.get("vanityName")
            )
            if name:
                return str(name)
        resp = requests.get(
            f"{V2_BASE}/organizations/{org_id}",
            params={"projection": "(id,localizedName,vanityName)"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        data = _safe_json(resp)
        if resp.ok:
            return (data.get("localizedName") or data.get("vanityName") or "") or None
    except requests.RequestException:
        logger.exception("LinkedIn organization lookup failed")
    return None


def _elements_to_orgs(elements: list, token: str) -> list[dict]:
    orgs: list[dict] = []
    seen: set[str] = set()
    for element in elements or []:
        org = element.get("organization") or element.get("organizationalTarget") or ""
        if isinstance(org, dict):
            org_id = str(org.get("id") or "").strip()
            name = org.get("localizedName") or org.get("vanityName") or org_id
        else:
            org_id = normalize_org_id(str(org))
            name = ""
        if not org_id or org_id in seen:
            continue
        seen.add(org_id)
        if not name:
            name = fetch_org_name(token, org_id) or org_id
        orgs.append({"id": org_id, "name": name})
    return orgs


def list_admin_orgs(token: str | None = None) -> tuple[list[dict], str | None]:
    """Organizations the member can administer (company pages)."""
    try:
        token = (token or "").strip() or ensure_access_token()
    except LinkedInError as exc:
        return [], str(exc)

    try:
        resp = requests.get(
            f"{REST_BASE}/organizationAcls",
            params={
                "q": "roleAssignee",
                "role": "ADMINISTRATOR",
                "state": "APPROVED",
                "count": 50,
            },
            headers=_rest_headers(token),
            timeout=20,
        )
        data = _safe_json(resp)
        if resp.ok:
            orgs = _elements_to_orgs(data.get("elements") or [], token)
            if orgs:
                return orgs, None
        resp = requests.get(
            f"{V2_BASE}/organizationAcls",
            params={
                "q": "roleAssignee",
                "role": "ADMINISTRATOR",
                "state": "APPROVED",
                "projection": "(elements*(organization~(id,localizedName,vanityName)))",
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=20,
        )
        data = _safe_json(resp)
        if resp.ok:
            orgs = _elements_to_orgs(data.get("elements") or [], token)
            if orgs:
                return orgs, None
        err = _api_error(data, "Impossible de lister les pages entreprise.")
        if _is_community_denied(resp.status_code, data):
            err = f"{err} {COMMUNITY_API_HINT}"
        return [], err[:500]
    except requests.RequestException as exc:
        logger.exception("LinkedIn organization listing failed")
        return [], str(exc)


def connect_organization(org_id: str, *, known_name: str = "") -> dict:
    org_id = normalize_org_id(org_id)
    if not org_id:
        return {
            "ok": False,
            "message": (
                "Identifiant de page entreprise invalide. Collez l'ID numérique "
                "(URL d'admin LinkedIn : /company/123456/admin/), pas le nom de la page."
            ),
        }
    try:
        token = ensure_access_token()
    except LinkedInError as exc:
        return {"ok": False, "message": str(exc)}
    name = (known_name or "").strip() or fetch_org_name(token, org_id) or f"Page {org_id}"
    profile = fetch_member_profile(token)
    content.set_setting(SETTING_ORG_ID, org_id)
    content.set_setting(SETTING_ORG_NAME, name)
    if profile.get("id"):
        content.set_setting(SETTING_MEMBER_ID, profile["id"])
    if profile.get("name"):
        content.set_setting(SETTING_MEMBER_NAME, profile["name"])
    return {
        "ok": True,
        "message": name,
        "org_id": org_id,
        "publish_as": "organization",
    }


def connect_member(token: str | None = None) -> dict:
    try:
        token = (token or "").strip() or ensure_access_token()
    except LinkedInError as exc:
        return {"ok": False, "message": str(exc)}
    profile = fetch_member_profile(token)
    if not profile.get("id"):
        return {
            "ok": False,
            "message": (
                "Impossible d'identifier le profil LinkedIn. "
                "Vérifiez Sign In with LinkedIn (OpenID) sur l'app Developers."
            ),
        }
    content.set_setting(SETTING_MEMBER_ID, profile["id"])
    if profile.get("name"):
        content.set_setting(SETTING_MEMBER_NAME, profile["name"])
    name = profile.get("name") or profile["id"]
    return {
        "ok": True,
        "message": name,
        "member_id": profile["id"],
        "publish_as": "member",
    }


def complete_oauth(code: str, redirect_uri: str) -> dict:
    payload, err = exchange_oauth_code(code, redirect_uri)
    if not payload:
        return {"ok": False, "message": err or "Connexion LinkedIn impossible."}
    _save_token_payload(payload, keep_refresh=False)
    token = payload["access_token"]
    orgs, list_err = list_admin_orgs(token)
    if len(orgs) == 1:
        result = connect_organization(orgs[0]["id"], known_name=orgs[0].get("name") or "")
        result["orgs"] = orgs
        return result
    if len(orgs) > 1:
        connect_member(token)
        return {
            "ok": False,
            "needs_org_choice": True,
            "orgs": orgs,
            "message": (
                f"Compte LinkedIn accepté. {len(orgs)} page(s) entreprise — "
                "choisissez celle sur laquelle publier."
            ),
        }
    member = connect_member(token)
    if member.get("ok"):
        member["message"] = (
            f"Profil LinkedIn « {member['message']} » connecté. "
            f"{SHARE_ON_LINKEDIN_HINT}"
        )
        member["orgs"] = []
        return member
    return {
        "ok": False,
        "needs_org_choice": False,
        "orgs": [],
        "message": member.get("message") or list_err or "Connexion LinkedIn impossible.",
    }


def connect_with_token(token: str, org_id: str = "") -> dict:
    token = (token or "").strip()
    if not token:
        return {"ok": False, "message": "Jeton LinkedIn requis."}
    try:
        save_pasted_token(token)
    except LinkedInError as exc:
        return {"ok": False, "message": str(exc)}
    asked = normalize_org_id(org_id)
    if asked:
        return connect_organization(asked)
    orgs, list_err = list_admin_orgs(token)
    if len(orgs) == 1:
        return connect_organization(orgs[0]["id"], known_name=orgs[0].get("name") or "")
    if len(orgs) > 1:
        connect_member(token)
        return {
            "ok": False,
            "needs_org_choice": True,
            "orgs": orgs,
            "message": (
                f"Jeton accepté. {len(orgs)} page(s) entreprise — "
                "choisissez celle sur laquelle publier."
            ),
        }
    member = connect_member(token)
    if member.get("ok"):
        member["message"] = (
            f"Profil LinkedIn « {member['message']} » connecté. "
            f"{SHARE_ON_LINKEDIN_HINT}"
        )
        member["orgs"] = []
        return member
    return {
        "ok": False,
        "orgs": [],
        "message": member.get("message") or list_err or (
            "Jeton accepté, mais le profil n'a pas pu être identifié."
        ),
    }


def token_expires_at() -> datetime | None:
    raw = (content.get_setting(SETTING_TOKEN_EXPIRES, "") or "").strip()
    if not raw:
        return None
    try:
        expires = datetime.fromisoformat(raw)
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return expires
    except ValueError:
        return None


def connection_status() -> dict:
    cfg = get_config()
    if not is_configured():
        return {
            "connected": False,
            "org_id": cfg["org_id"],
            "org_name": cfg["org_name"],
            "member_id": cfg["member_id"],
            "member_name": cfg["member_name"],
            "publish_as": cfg["publish_as"],
            "has_refresh": cfg["has_refresh"],
            "app_ready": app_credentials_ready(),
            "has_token": has_token(),
            "message": "LinkedIn non connecté.",
            "expires_at": None,
        }
    expires = token_expires_at()
    if cfg["org_id"]:
        message = f"Prêt à publier au nom de la page {cfg['org_name'] or cfg['org_id']}."
    else:
        who = cfg["member_name"] or cfg["member_id"]
        message = f"Prêt à publier sur le profil {who}. {SHARE_ON_LINKEDIN_HINT}"
    if expires and expires <= datetime.now(timezone.utc) and not cfg["has_refresh"]:
        message = "Le jeton LinkedIn a probablement expiré — reconnectez LinkedIn."
    elif not cfg["has_refresh"]:
        message = "Jeton sans renouvellement automatique — reconnectez avant 60 jours."
    return {
        "connected": True,
        "org_id": cfg["org_id"],
        "org_name": cfg["org_name"],
        "member_id": cfg["member_id"],
        "member_name": cfg["member_name"],
        "publish_as": cfg["publish_as"],
        "has_refresh": cfg["has_refresh"],
        "app_ready": app_credentials_ready(),
        "has_token": True,
        "message": message,
        "expires_at": int(expires.timestamp()) if expires else None,
    }


def _post_permalink(urn: str) -> str:
    urn = (urn or "").strip()
    if not urn:
        return ""
    return f"https://www.linkedin.com/feed/update/{quote(urn, safe='')}"


def _article_title(message: str, target_key: str | None) -> str:
    from app.services.social_links import get_target

    target = get_target(target_key)
    if target and target.get("cta"):
        return str(target["cta"])[:70]
    first = (message or "").strip().split("\n")[0].strip()
    return (first or "PilotCore")[:70]


def _upload_image(token: str, owner: str, image_path) -> str | None:
    owner = (owner or "").strip()
    if not owner:
        return None
    try:
        init = requests.post(
            f"{REST_BASE}/images?action=initializeUpload",
            headers=_rest_headers(token, json_body=True),
            json={"initializeUploadRequest": {"owner": owner}},
            timeout=20,
        )
        data = _safe_json(init)
        if not init.ok:
            logger.warning("LinkedIn initializeUpload failed: %s", data)
            return None
        value = data.get("value") or {}
        upload_url = (value.get("uploadUrl") or "").strip()
        image_urn = (value.get("image") or "").strip()
        if not upload_url or not image_urn:
            return None
        raw = image_path.read_bytes()
        put = requests.put(
            upload_url,
            data=raw,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/octet-stream",
            },
            timeout=60,
        )
        if put.status_code >= 400:
            logger.warning("LinkedIn image PUT failed: %s %s", put.status_code, put.text[:300])
            return None
        return image_urn
    except requests.RequestException:
        logger.exception("LinkedIn image upload failed")
        return None


def _create_post(token: str, payload: dict) -> tuple[int, dict, str | None]:
    resp = requests.post(
        f"{REST_BASE}/posts",
        headers=_rest_headers(token, json_body=True),
        json=payload,
        timeout=30,
    )
    data = _safe_json(resp)
    urn = (resp.headers.get("x-restli-id") or resp.headers.get("X-RestLi-Id") or "").strip()
    if not urn and isinstance(data, dict):
        urn = (data.get("id") or "").strip() or None
    return resp.status_code, data if isinstance(data, dict) else {}, urn or None


def _create_ugc_share(
    token: str, author: str, message: str, link: str, title: str
) -> tuple[int, dict, str | None]:
    """Share on LinkedIn (ugcPosts) — works with w_member_social, no Images API."""
    share: dict = {
        "shareCommentary": {"text": (message or "")[:2600]},
        "shareMediaCategory": "NONE",
    }
    if link:
        share["shareMediaCategory"] = "ARTICLE"
        share["media"] = [
            {
                "status": "READY",
                "originalUrl": link,
                "title": {"text": (title or "PilotCore")[:400]},
                "description": {"text": (message or "")[:200]},
            }
        ]
    payload = {
        "author": author,
        "lifecycleState": "PUBLISHED",
        "specificContent": {"com.linkedin.ugc.ShareContent": share},
        "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
    }
    resp = requests.post(
        f"{V2_BASE}/ugcPosts",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Restli-Protocol-Version": "2.0.0",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )
    data = _safe_json(resp)
    urn = (resp.headers.get("x-restli-id") or resp.headers.get("X-RestLi-Id") or "").strip()
    if not urn and isinstance(data, dict):
        urn = (data.get("id") or "").strip()
    return resp.status_code, data if isinstance(data, dict) else {}, urn or None


def _base_post(author: str, commentary: str) -> dict:
    return {
        "author": author,
        "commentary": commentary[:2900],
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }


def publish_post(
    message,
    link=None,
    generated_by_ai=False,
    image_path=None,
    image_blob=None,
    target_key=None,
) -> SocialPost:
    """Publish as the connected org Page if set, otherwise as the member profile."""
    from app.services.social_image import resolve_image_path, write_image_bytes

    message = (message or "").strip()
    link = (link or "").strip() or None
    image_path = (image_path or "").strip() or None
    blob = bytes(image_blob) if image_blob else None
    resolved = resolve_image_path(image_path)
    if resolved is None and blob:
        resolved = write_image_bytes(image_path, blob)

    post = SocialPost(
        platform="linkedin",
        message=message,
        link=link,
        image_path=image_path,
        image_blob=blob,
        generated_by_ai=generated_by_ai,
        target_key=target_key,
        status="draft",
    )

    author = author_urn()
    if not (author and has_token()):
        post.status = "failed"
        post.error = "LinkedIn non connectée."
        db.session.add(post)
        db.session.commit()
        return post

    if not resolved:
        post.status = "failed"
        post.error = "Image requise — générez le post avec l'IA ou attendez la création du visuel."
        db.session.add(post)
        db.session.commit()
        return post

    if not link:
        post.status = "failed"
        post.error = "Sélectionnez une page cible pour rendre le visuel cliquable."
        db.session.add(post)
        db.session.commit()
        return post

    try:
        token = ensure_access_token()
        title = _article_title(message, target_key)
        image_urn = _upload_image(token, author, resolved)
        status, data, urn = 0, {}, None
        mode = "ugc"

        if image_urn:
            article_payload = _base_post(author, message)
            article_payload["content"] = {
                "article": {
                    "source": link,
                    "thumbnail": image_urn,
                    "title": title,
                    "description": message[:200],
                }
            }
            status, data, urn = _create_post(token, article_payload)
            mode = "article"
            if not (status < 300 and urn):
                media_payload = _base_post(author, f"{message}\n{link}")
                media_payload["content"] = {"media": {"title": title, "id": image_urn}}
                status, data, urn = _create_post(token, media_payload)
                mode = "media"

        if not (status < 300 and urn):
            status, data, urn = _create_ugc_share(token, author, message, link, title)
            mode = "ugc"

        if status < 300 and urn:
            post.status = "published"
            post.external_id = urn
            post.published_at = datetime.now(timezone.utc)
            post.permalink = _post_permalink(urn)
            log_event(
                CAT_ADMIN,
                "linkedin_publish",
                summary=f"Post LinkedIn publié ({mode}): {post.preview(60)}",
                level=LEVEL_SUCCESS,
            )
        else:
            post.status = "failed"
            err = _api_error(data, "Réponse LinkedIn invalide.")
            if _is_community_denied(status, data):
                err = f"{err} {SHARE_ON_LINKEDIN_HINT}"
            post.error = err[:500]
            log_event(
                CAT_ADMIN,
                "linkedin_publish_failed",
                summary=f"Échec publication LinkedIn: {post.error}",
                level=LEVEL_ERROR,
            )
    except LinkedInError as exc:
        post.status = "failed"
        post.error = str(exc)[:500]
    except requests.RequestException as exc:
        post.status = "failed"
        post.error = str(exc)[:500]
        logger.exception("LinkedIn publish failed")

    db.session.add(post)
    db.session.commit()
    return post
