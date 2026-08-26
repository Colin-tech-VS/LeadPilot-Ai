"""LinkedIn Company Page publishing (admin /admin/social).

Posts are always authored as the connected organization
(``urn:li:organization:{id}``), never as a personal profile.

That requires the Community Management API product on the LinkedIn developer
app. Development access is enough when the OAuth user is both an app developer
and a Page admin — no partner-production review, but the product must be added.
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

# Company-page posting. openid/profile identify the member; org scopes publish
# as the Page. w_member_social is intentionally omitted — that would post on
# a personal profile, which is not what PilotCore publishes.
OAUTH_SCOPES = " ".join(
    (
        "openid",
        "profile",
        "w_organization_social",
        "r_organization_social",
        "rw_organization_admin",
    )
)

SETTING_ORG_ID = "linkedin_org_id"
SETTING_ORG_NAME = "linkedin_org_name"
SETTING_ACCESS_TOKEN = "linkedin_access_token"
SETTING_REFRESH_TOKEN = "linkedin_refresh_token"
SETTING_TOKEN_EXPIRES = "linkedin_token_expires_at"
SETTING_MEMBER_NAME = "linkedin_member_name"

COMMUNITY_API_HINT = (
    "Les posts partent au nom de la page entreprise, pas du profil. "
    "Dans LinkedIn Developers → Produits, ajoutez « Community Management API ». "
    "En mode Développement, c’est immédiat si vous êtes admin de l’app et de la page "
    "— pas besoin d’attendre la review partenaire. « Share on LinkedIn » ne poste "
    "que sur un profil personnel, on ne l’utilise pas."
)

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
    return {
        "org_id": content.get_setting(SETTING_ORG_ID, "") or "",
        "org_name": content.get_setting(SETTING_ORG_NAME, "") or "",
        "member_name": content.get_setting(SETTING_MEMBER_NAME, "") or "",
        "has_refresh": bool((content.get_setting(SETTING_REFRESH_TOKEN, "") or "").strip()),
    }


def has_token() -> bool:
    return bool(
        (content.get_setting(SETTING_ACCESS_TOKEN, "") or "").strip()
        or (content.get_setting(SETTING_REFRESH_TOKEN, "") or "").strip()
    )


def is_configured() -> bool:
    cfg = get_config()
    return bool(cfg["org_id"] and has_token())


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


def disconnect() -> None:
    for key in (
        SETTING_ORG_ID,
        SETTING_ORG_NAME,
        SETTING_ACCESS_TOKEN,
        SETTING_REFRESH_TOKEN,
        SETTING_TOKEN_EXPIRES,
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


def _fetch_member_name(token: str) -> str | None:
    try:
        resp = requests.get(
            USERINFO_URL,
            headers={"Authorization": f"Bearer {token}"},
            timeout=12,
        )
        if resp.ok:
            data = resp.json() or {}
            name = (data.get("name") or "").strip()
            given = (data.get("given_name") or "").strip()
            family = (data.get("family_name") or "").strip()
            return name or f"{given} {family}".strip() or None
    except requests.RequestException:
        logger.exception("LinkedIn userinfo failed")
    return None


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
    member = _fetch_member_name(token)
    content.set_setting(SETTING_ORG_ID, org_id)
    content.set_setting(SETTING_ORG_NAME, name)
    if member:
        content.set_setting(SETTING_MEMBER_NAME, member)
    return {"ok": True, "message": name, "org_id": org_id}


def complete_oauth(code: str, redirect_uri: str) -> dict:
    payload, err = exchange_oauth_code(code, redirect_uri)
    if not payload:
        return {"ok": False, "message": err or "Connexion LinkedIn impossible."}
    _save_token_payload(payload, keep_refresh=False)
    token = payload["access_token"]
    member = _fetch_member_name(token)
    if member:
        content.set_setting(SETTING_MEMBER_NAME, member)
    orgs, list_err = list_admin_orgs(token)
    if len(orgs) == 1:
        result = connect_organization(orgs[0]["id"], known_name=orgs[0].get("name") or "")
        result["orgs"] = orgs
        return result
    if len(orgs) > 1:
        return {
            "ok": False,
            "needs_org_choice": True,
            "orgs": orgs,
            "message": (
                f"Compte LinkedIn accepté. {len(orgs)} page(s) entreprise — "
                "choisissez celle sur laquelle publier."
            ),
        }
    return {
        "ok": False,
        "needs_org_choice": False,
        "orgs": [],
        "message": list_err or (
            "Aucune page entreprise administrée n'a été trouvée. "
            f"{COMMUNITY_API_HINT}"
        ),
    }


def connect_with_token(token: str, org_id: str = "") -> dict:
    token = (token or "").strip()
    if not token:
        return {"ok": False, "message": "Jeton LinkedIn requis."}
    try:
        save_pasted_token(token)
    except LinkedInError as exc:
        return {"ok": False, "message": str(exc)}
    member = _fetch_member_name(token)
    if member:
        content.set_setting(SETTING_MEMBER_NAME, member)
    asked = normalize_org_id(org_id)
    if asked:
        return connect_organization(asked)
    orgs, list_err = list_admin_orgs(token)
    if len(orgs) == 1:
        return connect_organization(orgs[0]["id"], known_name=orgs[0].get("name") or "")
    if len(orgs) > 1:
        return {
            "ok": False,
            "needs_org_choice": True,
            "orgs": orgs,
            "message": (
                f"Jeton accepté. {len(orgs)} page(s) entreprise — "
                "choisissez celle sur laquelle publier."
            ),
        }
    return {
        "ok": False,
        "orgs": [],
        "message": list_err or (
            "Jeton accepté, mais aucune page entreprise n'apparaît. "
            "Collez l'ID numérique de la page, ou vérifiez Community Management API. "
            f"{COMMUNITY_API_HINT}"
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
            "member_name": cfg["member_name"],
            "has_refresh": cfg["has_refresh"],
            "app_ready": app_credentials_ready(),
            "has_token": has_token(),
            "message": "Aucune page LinkedIn connectée.",
            "expires_at": None,
        }
    expires = token_expires_at()
    message = "Prêt à publier."
    if expires and expires <= datetime.now(timezone.utc) and not cfg["has_refresh"]:
        message = "Le jeton LinkedIn a probablement expiré — reconnectez la page."
    elif not cfg["has_refresh"]:
        message = "Jeton sans renouvellement automatique — reconnectez avant 60 jours."
    return {
        "connected": True,
        "org_id": cfg["org_id"],
        "org_name": cfg["org_name"],
        "member_name": cfg["member_name"],
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


def _upload_image(token: str, org_id: str, image_path) -> str | None:
    owner = org_urn(org_id)
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


def _base_post(org_id: str, commentary: str) -> dict:
    return {
        "author": org_urn(org_id),
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
    """Publish as the connected company page (article+thumbnail, else image)."""
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

    cfg = get_config()
    if not (cfg["org_id"] and has_token()):
        post.status = "failed"
        post.error = "Page LinkedIn non connectée."
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
        image_urn = _upload_image(token, cfg["org_id"], resolved)
        if not image_urn:
            post.status = "failed"
            post.error = (
                "Impossible d'envoyer le visuel à LinkedIn. "
                f"{COMMUNITY_API_HINT}"
            )[:500]
            log_event(CAT_ADMIN, "linkedin_publish_failed", summary=post.error, level=LEVEL_ERROR)
            db.session.add(post)
            db.session.commit()
            return post

        title = _article_title(message, target_key)
        article_payload = _base_post(cfg["org_id"], message)
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
            media_payload = _base_post(cfg["org_id"], f"{message}\n{link}")
            media_payload["content"] = {"media": {"title": title, "id": image_urn}}
            status, data, urn = _create_post(token, media_payload)
            mode = "media"

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
                err = f"{err} {COMMUNITY_API_HINT}"
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
