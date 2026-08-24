"""Facebook autopost queue: generate a preview, wait, then publish.

The next post is always composed and shown in admin before Graph ever sees
it. Send times snap to artisan-peak hours (Europe/Paris), not a raw +6/+12/+24h
offset that can land at 3am.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.core.extensions import db
from app.models.social_post import SocialPost
from app.services import content_studio
from app.services import social
from app.services.events import CAT_ADMIN, LEVEL_INFO, log_event
from app.services.social_schedule import is_aligned, next_publish_at, prefers_pro_topic

logger = logging.getLogger(__name__)

INTERVALS = (6, 12, 24)

TOPICS = (
    ("home", "Annuaire artisans — prendre RDV en ligne 24h/24 pour une fuite, une serrure ou une panne"),
    ("pro", "Essai gratuit 14 jours — un assistant vocal qui décroche même le dimanche soir"),
    ("artisans", "Trouver un plombier, serrurier ou électricien près de chez soi et réserver en 2 minutes"),
    ("home", "Dépannage urgent : les bons réflexes et un artisan disponible le jour même"),
    ("pro", "Ne ratez plus aucun appel client : le standard IA pour artisans du bâtiment"),
    ("artisans", "Recherche avancée par métier et ville — l'annuaire PilotCore"),
    ("pro", "Devis avant déplacement, agenda rempli, zéro appel manqué — essai 14 jours sans carte"),
    ("home", "Vitrier, climaticien, chauffagiste : un professionnel vérifié, un créneau en ligne"),
)


def utcnow():
    return datetime.now(timezone.utc)


def is_enabled() -> bool:
    return (content_studio.get_setting(social.SETTING_AUTOPOST, "") or "") == "1"


def interval_hours() -> int:
    raw = (content_studio.get_setting(social.SETTING_INTERVAL, "24") or "24").strip()
    try:
        hours = int(raw)
    except ValueError:
        hours = 24
    return hours if hours in INTERVALS else 24


def get_settings() -> dict:
    return {
        "enabled": is_enabled(),
        "interval": interval_hours(),
        "share_groups": (content_studio.get_setting(social.SETTING_SHARE_GROUPS, "") or "") == "1",
        "group_ids": social.selected_group_ids(),
    }


def save_settings(*, enabled: bool | None = None, interval: int | None = None, share_groups: bool | None = None):
    if enabled is not None:
        content_studio.set_setting(social.SETTING_AUTOPOST, "1" if enabled else "0")
    if interval is not None and interval in INTERVALS:
        content_studio.set_setting(social.SETTING_INTERVAL, str(interval))
    if share_groups is not None:
        content_studio.set_setting(social.SETTING_SHARE_GROUPS, "1" if share_groups else "0")


def queued_preview() -> SocialPost | None:
    return (
        SocialPost.query.filter_by(platform="facebook", status="queued")
        .order_by(SocialPost.scheduled_for.asc())
        .first()
    )


def last_published_at() -> datetime | None:
    row = (
        SocialPost.query.filter_by(platform="facebook", status="published")
        .order_by(SocialPost.published_at.desc())
        .first()
    )
    return row.published_at if row else None


def schedule_next(*, last_published: datetime | None = None) -> datetime:
    return next_publish_at(
        interval=interval_hours(),
        last_published=last_published if last_published is not None else last_published_at(),
    )


def _next_topic(when: datetime | None = None) -> tuple[str, str]:
    n = SocialPost.query.filter(SocialPost.status.in_(("published", "queued", "skipped"))).count()
    if when is not None and prefers_pro_topic(when):
        pro = [t for t in TOPICS if t[0] == "pro"]
        return pro[n % len(pro)]
    return TOPICS[n % len(TOPICS)]


def skip_queued(reason: str = "remplacé") -> None:
    for post in SocialPost.query.filter_by(status="queued").all():
        post.status = "skipped"
        post.error = reason[:500]
    db.session.commit()


def generate_preview(*, keep_schedule: datetime | None = None) -> SocialPost:
    """Compose the next Facebook post and store it as queued (never publishes)."""
    from app.services import content_ai, social_image
    from app.services.social_links import ensure_tracked

    skip_queued("nouvelle preview")
    when = keep_schedule or schedule_next()
    target_key, prompt = _next_topic(when)
    payload = content_ai.generate_social_post(prompt, "engageant", target_key=target_key, content_tag="autopost")
    visual = social_image.generate_for_post(
        prompt,
        "engageant",
        headline=payload.get("image_headline"),
        visual_brief=payload.get("visual_brief"),
    )
    post = SocialPost(
        platform="facebook",
        message=payload["message"],
        link=ensure_tracked(payload.get("link") or "", target_key=target_key, content="autopost"),
        image_path=visual["image_path"],
        generated_by_ai=True,
        status="queued",
        target_key=target_key,
        scheduled_for=when,
    )
    db.session.add(post)
    db.session.commit()
    log_event(
        CAT_ADMIN,
        "facebook_queue_preview",
        summary=f"Aperçu auto-post prêt pour {when.isoformat()} : {post.preview(60)}",
        level=LEVEL_INFO,
    )
    return post


def enable_autopost(interval: int) -> SocialPost:
    previous = interval_hours() if is_enabled() else None
    save_settings(enabled=True, interval=interval)
    existing = queued_preview()
    hours = interval_hours()
    if existing:
        due = existing.scheduled_for
        needs_snap = (
            previous != hours
            or not due
            or not is_aligned(due, hours)
        )
        if needs_snap:
            existing.scheduled_for = schedule_next()
            db.session.commit()
        return existing
    return generate_preview()


def disable_autopost() -> None:
    save_settings(enabled=False)
    skip_queued("autopublication désactivée")


def update_queued(message: str | None = None, target_key: str | None = None) -> SocialPost | None:
    post = queued_preview()
    if not post:
        return None
    if message is not None:
        post.message = message.strip()
    if target_key:
        from app.services.social_links import build_tracked_url_for_target

        post.target_key = target_key
        post.link = build_tracked_url_for_target(target_key, content="autopost")
    db.session.commit()
    return post


def publish_queued_now() -> SocialPost | None:
    post = queued_preview()
    if not post:
        return None
    published = social.publish_post(
        post.message,
        link=post.link,
        generated_by_ai=post.generated_by_ai,
        image_path=post.image_path,
    )
    post.status = published.status
    post.external_id = published.external_id
    post.permalink = published.permalink
    post.published_at = published.published_at
    post.error = published.error
    db.session.commit()
    # Drop the duplicate row created by publish_post (it always inserts).
    if published.id != post.id:
        db.session.delete(published)
        db.session.commit()
    if post.status == "published" and is_enabled():
        generate_preview()
    return post


def tick() -> dict:
    """Cron entry: publish a due queued preview, then compose the next one."""
    if not is_enabled():
        return {"action": "disabled"}
    if not social.is_configured():
        return {"action": "not_configured"}

    post = queued_preview()
    if not post:
        generated = generate_preview()
        return {"action": "preview_created", "id": str(generated.id), "scheduled_for": generated.scheduled_for.isoformat()}

    due = post.scheduled_for
    if due and due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    hours = interval_hours()
    if due and due > utcnow() and not is_aligned(due, hours):
        post.scheduled_for = schedule_next()
        db.session.commit()
        due = post.scheduled_for
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
    if due and due > utcnow():
        return {
            "action": "waiting",
            "id": str(post.id),
            "scheduled_for": due.isoformat(),
        }

    published = publish_queued_now()
    return {
        "action": "published" if published and published.status == "published" else "failed",
        "id": str(published.id) if published else None,
        "error": published.error if published else "none",
    }
