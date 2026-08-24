"""Daily auto-generated blog post — long-tail SEO capture via Mistral.

Scheduled via Scalingo cron. Picks the next unclaimed slot from a rotating
pool of (trade × intent × city) topics, calls the existing
``content_ai.generate_blog_post`` pipeline, and publishes the result as a
``BlogPost`` with status="published".

Idempotent: re-running the same day is a no-op if today's slot already ran.
Safe to run manually from the admin UI too.

Usage:
    python scripts/generate_daily_blog.py                # today's slot
    python scripts/generate_daily_blog.py --topic "..."  # explicit topic
    python scripts/generate_daily_blog.py --dry-run      # print, don't save
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("daily_blog")


# Long-tail intent templates — each `{trade}` × `{city}` combination becomes
# a highly-searched query that maps to a distinct blog post.
INTENTS: list[str] = [
    "Combien coûte un {trade} en 2026 en France : fourchette de prix et facteurs de variation",
    "Comment reconnaître un {trade} sérieux : 8 critères concrets avant d'accepter un devis",
    "Que faire quand un {trade} n'est pas disponible immédiatement : les alternatives fiables",
    "Assurance décennale d'un {trade} : ce qu'elle couvre et comment la vérifier",
    "{trade} le week-end : prix, majorations et vraies urgences vs faux besoins",
    "Devis {trade} : les 12 lignes obligatoires à vérifier avant de signer",
    "{trade} à {city} : comment trouver un artisan disponible en moins de 24 h",
    "Panne classique de {trade} : le diagnostic en 5 minutes avant d'appeler",
    "Prix moyen d'une intervention de {trade} à {city} : chiffres réels et médianes",
    "{trade} d'urgence à {city} la nuit : ce que dit la loi sur les tarifs et majorations",
    "Comment éviter les arnaques quand on appelle un {trade} pour un dépannage",
    "{trade} et rénovation énergétique : quelles aides en 2026 (MaPrimeRénov', CEE)",
    "Faut-il choisir un {trade} local ou une grande enseigne ? Comparatif objectif",
    "Petit sinistre {trade} : quand faire jouer l'assurance habitation, quand payer soi-même",
    "{trade} : les 7 questions à poser au téléphone avant de laisser venir chez soi",
]

# Trades that concentrate the highest French search volume in "prix / urgence /
# comment" queries.
FOCUS_TRADES: list[tuple[str, str]] = [
    ("plombier", "Plombier"),
    ("serrurier", "Serrurier"),
    ("electricien", "Électricien"),
    ("chauffagiste", "Chauffagiste"),
    ("climaticien", "Climaticien"),
    ("vitrier", "Vitrier"),
    ("couvreur", "Couvreur"),
    ("menuisier", "Menuisier"),
]

FOCUS_CITIES: list[str] = [
    "Paris", "Lyon", "Marseille", "Toulouse", "Bordeaux", "Nantes",
    "Lille", "Nice", "Strasbourg", "Rennes", "Montpellier", "Grenoble",
]


def _pick_slot(seed_date: date) -> str:
    """Deterministic pick: same day → same topic, so re-runs are idempotent
    and the pool rotates predictably day by day (no random state needed)."""
    day_index = seed_date.toordinal()
    intent = INTENTS[day_index % len(INTENTS)]
    _trade_key, trade_label = FOCUS_TRADES[day_index % len(FOCUS_TRADES)]
    city = FOCUS_CITIES[day_index % len(FOCUS_CITIES)]
    return intent.format(trade=trade_label, city=city)


_SLUG_STRIP_RE = re.compile(r"[^\w\s-]")
_SLUG_DASH_RE = re.compile(r"[-\s]+")


def _slugify(value: str, max_len: int = 90) -> str:
    v = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    v = _SLUG_STRIP_RE.sub("", v).strip().lower()
    v = _SLUG_DASH_RE.sub("-", v).strip("-")
    return v[:max_len].rstrip("-")


def _pick_category(fallback: str = "conseils"):
    """Return an existing BlogCategory (by slug 'conseils' or the first one)."""
    from app.models.blog_category import BlogCategory

    cat = BlogCategory.query.filter_by(slug=fallback).one_or_none()
    return cat or BlogCategory.query.order_by(BlogCategory.sort_order).first()


def run(topic: str | None = None, *, dry_run: bool = False) -> int:
    """Return exit code (0 OK, non-zero on error)."""
    from app import create_app
    from app.core.extensions import db
    from app.models.blog_post import BlogPost
    from app.services import content_ai

    app = create_app()
    with app.app_context():
        if not content_ai.is_available():
            logger.error("Mistral not available (MISTRAL_API_KEY missing). Aborting.")
            return 2

        topic = topic or _pick_slot(date.today())
        base_slug = _slugify(topic)
        if BlogPost.query.filter_by(slug=base_slug).first():
            logger.info("Post with slug %r already exists — skipping.", base_slug)
            return 0

        logger.info("Generating blog post — topic: %s", topic)
        try:
            data = content_ai.generate_blog_post(topic, tone="expert", category_hint="Conseils")
        except content_ai.ContentAIError as exc:
            logger.error("Blog generation failed: %s", exc)
            return 3

        title = (data.get("title") or topic)[:220]
        slug = _slugify(title) or base_slug
        # Deconflict — the model can produce a title that maps to an existing slug
        if BlogPost.query.filter_by(slug=slug).first():
            slug = f"{slug}-{date.today().isoformat()}"

        if dry_run:
            logger.info(
                "[dry-run] would create post: slug=%s title=%r words=%d",
                slug, title, len((data.get("body_html") or "").split()),
            )
            return 0

        cat = _pick_category()
        post = BlogPost(
            slug=slug,
            title=title,
            excerpt=data.get("excerpt") or "",
            meta_description=data.get("meta_description") or "",
            meta_keywords=data.get("meta_keywords") or "",
            body_html=data.get("body_html") or "",
            reading_time_min=data.get("reading_time_min") or 5,
            category_id=cat.id if cat else None,
            status="published",
            published_at=datetime.now(timezone.utc),
        )
        if data.get("faq"):
            post.set_faq(data["faq"])
        db.session.add(post)
        db.session.commit()
        logger.info("Published blog post: /blog/%s (%d chars)", slug, len(post.body_html or ""))
        return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--topic", help="Explicit topic to write about")
    parser.add_argument("--dry-run", action="store_true", help="Print output, don't persist")
    args = parser.parse_args()
    sys.exit(run(topic=args.topic, dry_run=args.dry_run))
