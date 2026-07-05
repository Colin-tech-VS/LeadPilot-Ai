"""Facebook Page publishing via the Graph API.

Credentials (Page ID + Page access token) are stored as site settings so the
owner can connect a Page from the admin console without a redeploy. When they
are absent, publishing is disabled and the UI shows a "connect" prompt — nothing
breaks.
"""
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


def verify_connection():
    """Best-effort check that the stored token can read the Page. Returns
    (ok, message)."""
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
        if resp.ok and data.get("name"):
            if data["name"] != cfg["page_name"]:
                content.set_setting(SETTING_PAGE_NAME, data["name"])
            return True, data["name"]
        err = (data.get("error") or {}).get("message", "Erreur inconnue")
        return False, err
    except requests.RequestException as exc:
        return False, str(exc)


def publish_post(message, link=None, generated_by_ai=False) -> SocialPost:
    """Publish a text (optionally with a link) to the connected Facebook Page and
    record it as a SocialPost. Always returns a persisted SocialPost, whose
    ``status`` reflects success/failure so the caller can surface it."""
    message = (message or "").strip()
    link = (link or "").strip() or None
    post = SocialPost(
        platform="facebook",
        message=message,
        link=link,
        generated_by_ai=generated_by_ai,
        status="draft",
    )

    cfg = get_config()
    if not (cfg["page_id"] and cfg["token"]):
        post.status = "failed"
        post.error = "Page Facebook non connectée."
        db.session.add(post)
        db.session.commit()
        return post

    payload = {"message": message, "access_token": cfg["token"]}
    if link:
        payload["link"] = link
    try:
        resp = requests.post(f"{GRAPH_BASE}/{cfg['page_id']}/feed", data=payload, timeout=15)
        data = resp.json()
        if resp.ok and data.get("id"):
            post.status = "published"
            post.external_id = data["id"]
            post.published_at = datetime.now(timezone.utc)
            post.permalink = f"https://www.facebook.com/{data['id']}"
            log_event(CAT_ADMIN, "facebook_publish",
                      summary=f"Post Facebook publié: {post.preview(60)}", level=LEVEL_SUCCESS)
        else:
            post.status = "failed"
            post.error = (data.get("error") or {}).get("message", "Réponse Facebook invalide.")[:500]
            log_event(CAT_ADMIN, "facebook_publish_failed",
                      summary=f"Échec publication Facebook: {post.error}", level=LEVEL_ERROR)
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
