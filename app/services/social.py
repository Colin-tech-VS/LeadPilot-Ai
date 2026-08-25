"""Facebook Page publishing via the Graph API.

Credentials (Page ID + Page access token) are stored as site settings so the
owner can connect a Page from the admin console without a redeploy. When they
are absent, publishing is disabled and the UI shows a "connect" prompt — nothing
breaks.

A Facebook *user* token always expires (~1–2h, or ~60 days after exchange).
Only a *page* token derived from a long-lived user token never expires. We
never store a short-lived page token as if it were unlimited.
"""
import json
import logging
from datetime import datetime, timezone
from urllib.parse import urlencode

import requests
from flask import current_app

from app.core.extensions import db
from app.models.social_post import SocialPost
from app.services import content_studio as content
from app.services.events import CAT_ADMIN, LEVEL_ERROR, LEVEL_SUCCESS, log_event

logger = logging.getLogger(__name__)

GRAPH_VERSION = "v19.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_VERSION}"

SETTING_PAGE_ID = "facebook_page_id"
SETTING_TOKEN = "facebook_page_token"
SETTING_PAGE_NAME = "facebook_page_name"
SETTING_USER_TOKEN = "facebook_user_token"
SETTING_TOKEN_EXPIRES = "facebook_token_expires"
SETTING_AUTOPOST = "facebook_autopost_enabled"
SETTING_INTERVAL = "facebook_autopost_interval"
SETTING_SHARE_GROUPS = "facebook_share_groups"
SETTING_GROUP_IDS = "facebook_group_ids"

DEFAULT_PAGE_ID = "1246135508572421"

REQUIRED_PAGE_PERMISSIONS = (
    "pages_manage_posts",
    "pages_read_engagement",
    "pages_show_list",
)

OAUTH_SCOPES = ",".join(
    (
        "pages_show_list",
        "pages_manage_posts",
        "pages_read_engagement",
        "pages_manage_metadata",
        "business_management",
    )
)

PERMISSION_ERROR_HINT = (
    "Collez un token UTILISATEUR (Graph Explorer, User Token) généré avec l'app "
    "PilotCore, permissions pages_manage_posts, pages_read_engagement et "
    "pages_show_list. Un token de page collé tel quel expire ; seul l'échange "
    "via FACEBOOK_APP_ID / FACEBOOK_APP_SECRET produit un token de page illimité."
)


def get_config():
    return {
        "page_id": content.get_setting(SETTING_PAGE_ID, "") or "",
        "page_name": content.get_setting(SETTING_PAGE_NAME, "") or "",
        "token": content.get_setting(SETTING_TOKEN, "") or "",
    }


def is_configured() -> bool:
    cfg = get_config()
    return bool(cfg["page_id"] and cfg["token"])


def save_connection(page_id, token, page_name=""):
    content.set_setting(SETTING_PAGE_ID, (page_id or "").strip())
    content.set_setting(SETTING_TOKEN, (token or "").strip())
    content.set_setting(SETTING_PAGE_NAME, (page_name or "").strip())


def disconnect():
    content.set_setting(SETTING_PAGE_ID, "")
    content.set_setting(SETTING_TOKEN, "")
    content.set_setting(SETTING_PAGE_NAME, "")
    content.set_setting(SETTING_USER_TOKEN, "")
    content.set_setting(SETTING_TOKEN_EXPIRES, "")


def token_identity(token: str) -> tuple[str | None, str | None]:
    """Return Graph API ``/me`` id and name for this access token."""
    token = (token or "").strip()
    if not token:
        return None, None
    try:
        resp = requests.get(
            f"{GRAPH_BASE}/me",
            params={"fields": "id,name", "access_token": token},
            timeout=12,
        )
        data = resp.json()
        if resp.ok:
            return str(data.get("id") or "") or None, data.get("name")
    except requests.RequestException:
        logger.exception("Facebook token identity check failed")
    return None, None


def is_page_access_token(token: str, page_id: str) -> bool:
    """True when the token already belongs to the target Facebook Page."""
    identity_id, _ = token_identity(token)
    return bool(identity_id and identity_id == str(page_id or "").strip())


def prepare_page_token(page_id: str, token: str) -> tuple[str, str | None]:
    """Pick the token to store — never replace a valid page token with a user token."""
    token = (token or "").strip()
    page_id = str(page_id or "").strip()
    if not token or not page_id:
        return token, None

    identity_id, identity_name = token_identity(token)
    if identity_id == page_id:
        return token, identity_name

    resolved, page_name = resolve_page_access_token(token, page_id)
    if resolved:
        return resolved, page_name

    return token, identity_name


def ensure_publish_config():
    """Return Facebook config using a page token suitable for publishing."""
    cfg = get_config()
    if not (cfg["page_id"] and cfg["token"]):
        return cfg
    if is_page_access_token(cfg["token"], cfg["page_id"]):
        return cfg
    token, page_name = prepare_page_token(cfg["page_id"], cfg["token"])
    if token != cfg["token"] or (page_name and page_name != cfg["page_name"]):
        save_connection(cfg["page_id"], token, page_name or cfg["page_name"])
        return get_config()
    return cfg


def resolve_page_access_token(token: str, page_id: str) -> tuple[str | None, str | None]:
    """Exchange a user token for the matching page access token when possible."""
    token = (token or "").strip()
    page_id = str(page_id or "").strip()
    if not token or not page_id:
        return None, None
    if is_page_access_token(token, page_id):
        _, page_name = token_identity(token)
        return token, page_name
    try:
        resp = requests.get(
            f"{GRAPH_BASE}/me/accounts",
            params={
                "access_token": token,
                "fields": "id,name,access_token",
                "limit": 100,
            },
            timeout=12,
        )
        data = resp.json()
        if not resp.ok:
            return None, None
        for page in data.get("data", []):
            if str(page.get("id")) == page_id:
                return page.get("access_token"), page.get("name")
    except requests.RequestException:
        logger.exception("Facebook page token resolution failed")
    return None, None


def _is_permission_error(data) -> bool:
    err = _graph_error(data)
    if err.get("code") == 10:
        return True
    msg = (err.get("message") or "").lower()
    return "does not have permission" in msg or "permission" in msg and "action" in msg


def _permission_error_message(data) -> str:
    base = _graph_error(data).get("message", "Permission Facebook refusée.")
    if _is_permission_error(data):
        return f"{base} {PERMISSION_ERROR_HINT}"[:500]
    return base[:500]


def _probe_publish_permission(cfg) -> tuple[bool, str]:
    """Create then delete an unpublished post to confirm pages_manage_posts."""
    try:
        resp = requests.post(
            f"{GRAPH_BASE}/{cfg['page_id']}/feed",
            data={
                "message": "PilotCore — test permission publication.",
                "published": "false",
                "access_token": cfg["token"],
            },
            timeout=15,
        )
        data = resp.json()
        if not resp.ok:
            return False, _permission_error_message(data)
        post_id = data.get("id")
        if post_id:
            requests.delete(
                f"{GRAPH_BASE}/{post_id}",
                params={"access_token": cfg["token"]},
                timeout=10,
            )
        return True, "Publication autorisée."
    except requests.RequestException as exc:
        return False, str(exc)


def verify_connection(*, check_publish: bool = False):
    """Best-effort check that the stored token can access (and optionally publish to) the Page.

    Returns (ok, message).
    """
    cfg = get_config()
    if not (cfg["page_id"] and cfg["token"]):
        return False, "Aucune page connectée."

    try:
        resp = requests.get(
            f"{GRAPH_BASE}/{cfg['page_id']}",
            params={"fields": "name", "access_token": cfg["token"]},
            timeout=12,
        )
        data = resp.json()
        if not resp.ok or not data.get("name"):
            err = _graph_error(data).get("message", "Erreur inconnue")
            if _is_permission_error(data):
                err = _permission_error_message(data)
            return False, err

        name = data["name"]
        if name != cfg["page_name"]:
            content.set_setting(SETTING_PAGE_NAME, name)

        if check_publish:
            ok_pub, pub_msg = _probe_publish_permission(cfg)
            if not ok_pub:
                return False, pub_msg

        return True, name
    except requests.RequestException as exc:
        return False, str(exc)


def _graph_error(data) -> dict:
    return data.get("error") or {}


def _is_custom_link_preview_error(data) -> bool:
    """Meta only allows custom thumbnail/picture when the link domain is verified."""
    err = _graph_error(data)
    if err.get("code") != 100:
        return False
    msg = (err.get("message") or "").lower()
    return "only owners of the url" in msg


def _cta_payload(link: str) -> str:
    return json.dumps({"type": "LEARN_MORE", "value": {"link": link}})


def _publish_photo_only(cfg, message, resolved):
    """Branded image post without link parameters — works with most page tokens."""
    endpoint = f"{GRAPH_BASE}/{cfg['page_id']}/photos"
    with open(resolved, "rb") as image_file:
        return requests.post(
            endpoint,
            data={"message": message, "access_token": cfg["token"]},
            files={"source": (resolved.name, image_file, "image/png")},
            timeout=60,
        )


def _attempt_publish(cfg, message, link, resolved) -> tuple[requests.Response | None, dict, str | None]:
    """Try several Graph API strategies until one succeeds."""
    strategies = (
        ("link_thumbnail", lambda: _publish_link_post(cfg, message, link, resolved)),
        ("photo", lambda: _publish_photo_only(cfg, message, resolved)),
        ("photo_cta", lambda: _publish_photo_with_cta(cfg, message, link, resolved)),
        ("link_og", lambda: _publish_link_only(cfg, message, link)),
    )
    last_resp = None
    last_data: dict = {}
    for mode, builder in strategies:
        resp = builder()
        data = resp.json()
        last_resp, last_data = resp, data
        if resp.ok and _external_id_from_response(data):
            return resp, data, mode
        err = _graph_error(data).get("message", "")
        logger.warning("Facebook publish mode %s failed: %s", mode, err)
    return last_resp, last_data, None


def connection_status() -> dict:
    """Diagnostics for the admin UI — token type and publish readiness."""
    cfg = get_config()
    if not (cfg["page_id"] and cfg["token"]):
        return {
            "connected": False,
            "page_id": "",
            "page_name": "",
            "token_kind": "missing",
            "can_read": False,
            "can_publish": False,
            "message": "Aucune page connectée.",
            "token_health": {"is_valid": False, "never_expires": False, "expires_at": None, "error": "Aucune page connectée."},
            "app_ready": app_credentials_ready(),
            "has_user_token": False,
        }

    identity_id, identity_name = token_identity(cfg["token"])
    token_kind = "page" if is_page_access_token(cfg["token"], cfg["page_id"]) else "user"
    can_read, read_msg = False, ""
    try:
        resp = requests.get(
            f"{GRAPH_BASE}/{cfg['page_id']}",
            params={"fields": "name", "access_token": cfg["token"]},
            timeout=12,
        )
        data = resp.json()
        can_read = resp.ok and bool(data.get("name"))
        read_msg = data.get("name") if can_read else _graph_error(data).get("message", "Lecture page impossible.")
    except requests.RequestException as exc:
        read_msg = str(exc)

    can_publish = False
    pub_msg = ""
    if can_read and token_kind == "page":
        can_publish = True
        pub_msg = "Token page détecté — publication activée."
    elif can_read:
        pub_msg = "Token utilisateur : reconnectez avec le token de page depuis /me/accounts."

    if can_publish:
        message = "Prêt à publier."
    elif can_read and token_kind == "page":
        message = pub_msg
    elif can_read:
        message = pub_msg
    else:
        message = read_msg or "Connexion invalide."

    return {
        "connected": True,
        "page_id": cfg["page_id"],
        "page_name": cfg["page_name"] or identity_name or read_msg,
        "token_kind": token_kind,
        "can_read": can_read,
        "can_publish": can_publish,
        "message": message[:500],
        "token_health": inspect_stored_token(),
        "app_ready": app_credentials_ready(),
        "has_user_token": bool((content.get_setting(SETTING_USER_TOKEN, "") or "").strip()),
    }


def check_publish_ready() -> tuple[bool, str]:
    cfg = get_config()
    if not (cfg["page_id"] and cfg["token"]):
        return False, "Aucune page connectée."
    return _probe_publish_permission(cfg)


def _publish_link_post(cfg, message, link, resolved):
    """Link post with custom thumbnail — requires verified link domain in Meta Business."""
    endpoint = f"{GRAPH_BASE}/{cfg['page_id']}/feed"
    base_data = {"message": message, "link": link, "access_token": cfg["token"]}

    with open(resolved, "rb") as image_file:
        return requests.post(
            endpoint,
            data=base_data,
            files={"thumbnail": (resolved.name, image_file, "image/png")},
            timeout=60,
        )


def _publish_photo_with_cta(cfg, message, link, resolved):
    """Branded photo + CTA button when custom link previews are blocked."""
    endpoint = f"{GRAPH_BASE}/{cfg['page_id']}/photos"
    with open(resolved, "rb") as image_file:
        return requests.post(
            endpoint,
            data={
                "message": message,
                "access_token": cfg["token"],
                "call_to_action": _cta_payload(link),
            },
            files={"source": (resolved.name, image_file, "image/png")},
            timeout=60,
        )


def _publish_link_only(cfg, message, link):
    """Standard link post — Facebook scrapes Open Graph tags (no custom thumbnail)."""
    return requests.post(
        f"{GRAPH_BASE}/{cfg['page_id']}/feed",
        data={"message": message, "link": link, "access_token": cfg["token"]},
        timeout=60,
    )


def _external_id_from_response(data) -> str | None:
    return data.get("post_id") or data.get("id")


def publish_post(message, link=None, generated_by_ai=False, image_path=None) -> SocialPost:
    """Publish a link post with custom thumbnail — image opens the tracked landing URL."""
    from app.services.social_image import resolve_image_path

    message = (message or "").strip()
    link = (link or "").strip() or None
    image_path = (image_path or "").strip() or None
    resolved = resolve_image_path(image_path)
    post = SocialPost(
        platform="facebook",
        message=message,
        link=link,
        image_path=image_path,
        generated_by_ai=generated_by_ai,
        status="draft",
    )

    cfg = ensure_publish_config()
    if not (cfg["page_id"] and cfg["token"]):
        post.status = "failed"
        post.error = "Page Facebook non connectée."
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
        _, data, publish_mode = _attempt_publish(cfg, message, link, resolved)

        external_id = _external_id_from_response(data) if publish_mode else None
        if publish_mode and external_id:
            post.status = "published"
            post.external_id = external_id
            post.published_at = datetime.now(timezone.utc)
            post.permalink = f"https://www.facebook.com/{external_id}"
            mode_labels = {
                "link_thumbnail": "lien + visuel cliquable",
                "photo": "photo avec visuel IA",
                "photo_cta": "photo + bouton En savoir plus",
                "link_og": "lien (aperçu Open Graph du site)",
            }
            log_event(
                CAT_ADMIN,
                "facebook_publish",
                summary=(
                    f"Post Facebook publié ({mode_labels.get(publish_mode, publish_mode)}): "
                    f"{post.preview(60)}"
                ),
                level=LEVEL_SUCCESS,
            )
            if (content.get_setting(SETTING_SHARE_GROUPS, "") or "") == "1":
                group_notes = share_to_selected_groups(message, link or post.permalink)
                if group_notes:
                    extra = " · groupes : " + " ; ".join(group_notes)
                    post.error = ((post.error or "") + extra)[:500]
        else:
            post.status = "failed"
            post.error = _permission_error_message(data) if _is_permission_error(data) else (
                _graph_error(data).get("message", "Réponse Facebook invalide.")[:500]
            )
            log_event(
                CAT_ADMIN,
                "facebook_publish_failed",
                summary=f"Échec publication Facebook: {post.error}",
                level=LEVEL_ERROR,
            )
    except requests.RequestException as exc:
        post.status = "failed"
        post.error = str(exc)[:500]
        logger.exception("Facebook publish failed")

    db.session.add(post)
    db.session.commit()
    return post


def recent_posts(limit=30):
    return (
        SocialPost.query.filter(SocialPost.status != "queued")
        .order_by(SocialPost.created_at.desc())
        .limit(limit)
        .all()
    )


def _app_credentials() -> tuple[str, str]:
    try:
        return (
            (current_app.config.get("FACEBOOK_APP_ID") or "").strip(),
            (current_app.config.get("FACEBOOK_APP_SECRET") or "").strip(),
        )
    except RuntimeError:
        return "", ""


def app_credentials_ready() -> bool:
    app_id, app_secret = _app_credentials()
    return bool(app_id and app_secret)


def facebook_oauth_redirect_uri() -> str:
    from app.utils.seo import site_base_url

    return f"{site_base_url()}/admin/social/facebook/callback"


def facebook_oauth_url(state: str, redirect_uri: str | None = None) -> str:
    app_id, _ = _app_credentials()
    uri = redirect_uri or facebook_oauth_redirect_uri()
    query = urlencode(
        {
            "client_id": app_id,
            "redirect_uri": uri,
            "state": state,
            "scope": OAUTH_SCOPES,
            "response_type": "code",
        }
    )
    return f"https://www.facebook.com/{GRAPH_VERSION}/dialog/oauth?{query}"


def exchange_oauth_code(code: str, redirect_uri: str) -> tuple[str | None, str | None]:
    """Exchange a Facebook Login code for a short-lived user token."""
    code = (code or "").strip()
    app_id, app_secret = _app_credentials()
    if not code:
        return None, "Code d'autorisation Facebook manquant."
    if not app_id or not app_secret:
        return None, "FACEBOOK_APP_ID et FACEBOOK_APP_SECRET sont requis."
    try:
        resp = requests.get(
            f"{GRAPH_BASE}/oauth/access_token",
            params={
                "client_id": app_id,
                "redirect_uri": redirect_uri,
                "client_secret": app_secret,
                "code": code,
            },
            timeout=15,
        )
        data = resp.json() or {}
        token = (data.get("access_token") or "").strip()
        if resp.ok and token:
            return token, None
        err = _graph_error(data).get("message") or "Échange du code Facebook impossible."
        logger.warning("Facebook OAuth code exchange failed: %s", data)
        return None, err
    except requests.RequestException as exc:
        logger.exception("Facebook OAuth code exchange failed")
        return None, str(exc)


def list_user_pages(user_token: str) -> list[dict]:
    """Pages the user can manage (id + name only)."""
    token = (user_token or "").strip()
    if not token:
        return []
    try:
        resp = requests.get(
            f"{GRAPH_BASE}/me/accounts",
            params={
                "access_token": token,
                "fields": "id,name",
                "limit": 100,
            },
            timeout=12,
        )
        data = resp.json() or {}
        if not resp.ok:
            return []
        return [
            {"id": str(page.get("id")), "name": page.get("name") or str(page.get("id"))}
            for page in data.get("data") or []
            if page.get("id")
        ]
    except requests.RequestException:
        logger.exception("Facebook /me/accounts listing failed")
        return []


def _pages_summary(pages: list[dict]) -> str:
    if not pages:
        return "aucune page"
    return ", ".join(f"{p['name']} ({p['id']})" for p in pages[:8])


def inspect_token(token: str) -> dict:
    """Inspect a Graph token. expires_at == 0 means it never expires."""
    token = (token or "").strip()
    empty = {
        "is_valid": False,
        "type": "",
        "expires_at": None,
        "never_expires": False,
        "scopes": [],
        "error": "Token vide.",
    }
    if not token:
        return empty
    app_id, app_secret = _app_credentials()
    access = f"{app_id}|{app_secret}" if app_id and app_secret else token
    try:
        resp = requests.get(
            f"{GRAPH_BASE}/debug_token",
            params={"input_token": token, "access_token": access},
            timeout=12,
        )
        payload = resp.json() or {}
        data = payload.get("data") or {}
        if not resp.ok and not data:
            err = _graph_error(payload).get("message") or payload.get("error", {}).get("message")
            empty["error"] = err or "Inspection du token impossible."
            return empty
        expires_at = int(data.get("expires_at") or 0)
        never = bool(data.get("is_valid")) and expires_at == 0
        return {
            "is_valid": bool(data.get("is_valid")),
            "type": str(data.get("type") or "").upper(),
            "expires_at": expires_at,
            "never_expires": never,
            "scopes": data.get("scopes") or [],
            "error": None if data.get("is_valid") else (
                (data.get("error") or {}).get("message") or "Token invalide."
            ),
        }
    except requests.RequestException as exc:
        empty["error"] = str(exc)
        return empty


def inspect_stored_token() -> dict:
    cfg = get_config()
    info = inspect_token(cfg.get("token") or "")
    stored = (content.get_setting(SETTING_TOKEN_EXPIRES, "") or "").strip()
    if info.get("expires_at") is not None:
        content.set_setting(SETTING_TOKEN_EXPIRES, str(info.get("expires_at") or 0))
    elif stored.isdigit():
        info["expires_at"] = int(stored)
        info["never_expires"] = info.get("is_valid") and int(stored) == 0
    return info


def refresh_never_expiring_token() -> dict:
    """Extend the 60-day user token and re-derive the never-expiring page token.

    A page token that never expires still needs a living user token for groups
    and for a later re-derivation if Meta invalidates the page token.
    """
    user_token = (content.get_setting(SETTING_USER_TOKEN, "") or "").strip()
    cfg = get_config()
    if user_token and app_credentials_ready():
        long_lived = exchange_long_lived_user_token(user_token)
        if long_lived:
            content.set_setting(SETTING_USER_TOKEN, long_lived)
            user_token = long_lived
        page_id = (cfg.get("page_id") or "").strip()
        if page_id:
            resolved, page_name = resolve_page_access_token(user_token, page_id)
            if resolved:
                save_connection(page_id, resolved, page_name or cfg.get("page_name") or "")
    return inspect_stored_token()


def exchange_long_lived_user_token(short_token: str) -> str | None:
    """Exchange a short-lived user token for a ~60-day token (needs app secret)."""
    short_token = (short_token or "").strip()
    app_id, app_secret = _app_credentials()
    if not short_token or not app_id or not app_secret:
        return None
    try:
        resp = requests.get(
            f"{GRAPH_BASE}/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": app_id,
                "client_secret": app_secret,
                "fb_exchange_token": short_token,
            },
            timeout=12,
        )
        data = resp.json() or {}
        if resp.ok and data.get("access_token"):
            return data["access_token"]
        logger.warning("Facebook long-lived exchange failed: %s", data)
    except requests.RequestException:
        logger.exception("Facebook long-lived exchange failed")
    return None


def connect_page(page_id: str, pasted_token: str) -> dict:
    """Store a never-expiring page token derived from a user token.

    User tokens always expire. We exchange them for a ~60-day user token, then
    call /me/accounts: that page token does not expire. If the long-lived
    exchange fails we refuse to save — a short-lived page token must not be
    stored as « illimité ».
    """
    page_id = str(page_id or "").strip()
    pasted = (pasted_token or "").strip()
    empty_health = {"is_valid": False, "never_expires": False, "expires_at": None, "error": None}
    if not page_id or not pasted:
        return {
            "ok": False,
            "message": "Identifiant de page et token requis.",
            "detail": "",
            "health": empty_health,
        }

    identity_id, identity_name = token_identity(pasted)
    if not identity_id:
        return {
            "ok": False,
            "message": "Token Facebook invalide ou déjà expiré. Générez-en un nouveau (User Token) dans Graph Explorer avec l'app PilotCore.",
            "detail": "",
            "health": empty_health,
        }

    if identity_id == page_id:
        health = inspect_token(pasted)
        if health.get("never_expires"):
            save_connection(page_id, pasted, identity_name or "")
            ok, message = verify_connection(check_publish=False)
            stored = inspect_stored_token()
            return {
                "ok": ok,
                "message": message,
                "detail": "Token de page illimité conservé (n'expire pas).",
                "health": stored,
            }
        return {
            "ok": False,
            "message": (
                "Ce jeton est un token de PAGE (il expire). Dans Graph Explorer, "
                "le menu à droite du token doit indiquer « User Token », pas le nom "
                "de la page. Copiez la longue chaîne en haut de l'écran — jamais "
                "l'access_token dans le JSON de /me/accounts."
            ),
            "detail": "",
            "health": health,
        }

    pasted_health = inspect_token(pasted)
    if (pasted_health.get("type") or "").upper() == "PAGE":
        return {
            "ok": False,
            "message": (
                f"Ce jeton est un token de PAGE (« {identity_name or identity_id} »), "
                f"pas un token utilisateur. Graph Explorer → menu du token → "
                "« User Token » (pas le nom de la page), puis copiez la barre du haut."
            ),
            "detail": "",
            "health": pasted_health,
        }

    if not app_credentials_ready():
        return {
            "ok": False,
            "message": (
                "FACEBOOK_APP_ID et FACEBOOK_APP_SECRET sont requis pour convertir "
                "un token utilisateur en token de page illimité. Ajoutez-les dans les "
                "variables d'environnement (app Meta → Paramètres → Général), puis réessayez."
            ),
            "detail": "",
            "health": empty_health,
        }

    long_lived = exchange_long_lived_user_token(pasted)
    if not long_lived:
        return {
            "ok": False,
            "message": (
                "Impossible d'échanger le token utilisateur vers un jeton 60 jours. "
                "Vérifiez qu'il a été généré avec LA MÊME app Meta que FACEBOOK_APP_ID "
                "(Graph Explorer → Application en haut à droite), et que ce n'est pas un token de page."
            ),
            "detail": "",
            "health": empty_health,
        }

    content.set_setting(SETTING_USER_TOKEN, long_lived)
    resolved, page_name = resolve_page_access_token(long_lived, page_id)
    if not resolved:
        pages = list_user_pages(long_lived)
        return {
            "ok": False,
            "message": (
                f"Token utilisateur valide, mais la page {page_id} n'apparaît pas dans /me/accounts. "
                f"Pages trouvées : {_pages_summary(pages)}. "
                "Permissions requises : pages_show_list, pages_manage_posts, pages_read_engagement."
            ),
            "detail": "",
            "health": empty_health,
        }

    save_connection(page_id, resolved, page_name or identity_name or "")
    ok, message = verify_connection(check_publish=False)
    health = inspect_stored_token()
    if health.get("never_expires"):
        detail = "Token de page illimité (n'expire pas). Le token utilisateur est conservé 60 jours et renouvelé automatiquement."
    else:
        detail = (
            "Page connectée, mais Graph indique encore une expiration. "
            "Recollez un token utilisateur fraîchement généré avec l'app PilotCore."
        )
    return {
        "ok": ok,
        "message": message,
        "detail": detail,
        "health": health,
    }


def list_member_groups() -> tuple[list[dict], str | None]:
    """Groups the admin user belongs to (needed to share a page post).

    Facebook Pages cannot follow groups; Graph only lists groups the *user*
    token is a member of (``groups_show_list`` + ``publish_to_groups``).
    """
    user_token = (content.get_setting(SETTING_USER_TOKEN, "") or "").strip()
    if not user_token:
        return [], (
            "Reconnectez la page avec un token utilisateur (pas seulement le token de page) "
            "pour lister les groupes. Permissions Graph : groups_show_list et publish_to_groups."
        )
    token = user_token
    try:
        resp = requests.get(
            f"{GRAPH_BASE}/me/groups",
            params={"fields": "id,name,administrator", "limit": 50, "access_token": token},
            timeout=15,
        )
        data = resp.json() or {}
        if not resp.ok:
            return [], _graph_error(data).get("message") or "Impossible de lister les groupes."
        groups = [
            {
                "id": str(g.get("id")),
                "name": g.get("name") or str(g.get("id")),
                "administrator": bool(g.get("administrator")),
            }
            for g in data.get("data") or []
            if g.get("id")
        ]
        return groups, None
    except requests.RequestException as exc:
        return [], str(exc)


def selected_group_ids() -> list[str]:
    raw = content.get_setting(SETTING_GROUP_IDS, "") or ""
    try:
        data = json.loads(raw) if raw else []
    except json.JSONDecodeError:
        data = [x.strip() for x in raw.split(",") if x.strip()]
    return [str(x) for x in data if x]


def save_group_ids(ids: list[str]):
    content.set_setting(SETTING_GROUP_IDS, json.dumps(ids))


def share_to_selected_groups(message: str, link: str | None) -> list[str]:
    """Share the published post into selected groups. Returns notes (errors or ok)."""
    ids = selected_group_ids()
    if not ids:
        return []
    user_token = (content.get_setting(SETTING_USER_TOKEN, "") or "").strip()
    if not user_token:
        return ["Token utilisateur manquant pour poster dans les groupes."]
    notes = []
    for gid in ids:
        try:
            payload = {"message": message, "access_token": user_token}
            if link:
                payload["link"] = link
            resp = requests.post(f"{GRAPH_BASE}/{gid}/feed", data=payload, timeout=20)
            data = resp.json() or {}
            if resp.ok and data.get("id"):
                notes.append(f"{gid} ok")
            else:
                notes.append(f"{gid}: {_graph_error(data).get('message', 'échec')[:160]}")
        except requests.RequestException as exc:
            notes.append(f"{gid}: {exc}")
    return notes
