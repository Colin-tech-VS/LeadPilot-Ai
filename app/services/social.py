"""Facebook Page publishing via the Graph API.

Credentials (Page ID + Page access token) are stored as site settings so the
owner can connect a Page from the admin console without a redeploy. When they
are absent, publishing is disabled and the UI shows a "connect" prompt — nothing
breaks.
"""
import json
import logging
from datetime import datetime, timezone

import requests

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

REQUIRED_PAGE_PERMISSIONS = (
    "pages_manage_posts",
    "pages_read_engagement",
    "pages_show_list",
)

PERMISSION_ERROR_HINT = (
    "Le token doit être un token d'accès de page (pas un token utilisateur) avec les "
    "permissions pages_manage_posts, pages_read_engagement et pages_show_list. "
    "Dans Graph API Explorer : générez un token utilisateur avec ces permissions, "
    "puis appelez GET /me/accounts?fields=id,name,access_token et copiez le token "
    "de votre page PilotCore."
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
        SocialPost.query.order_by(SocialPost.created_at.desc()).limit(limit).all()
    )
