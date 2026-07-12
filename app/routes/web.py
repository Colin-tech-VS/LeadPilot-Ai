import logging
import math
import uuid
from datetime import datetime, timedelta, timezone

from flask import Blueprint, abort, current_app, g, jsonify, make_response, redirect, render_template, request, send_from_directory, session, url_for
from pathlib import Path
from sqlalchemy.orm import contains_eager, joinedload

logger = logging.getLogger(__name__)

from app.core.errors import AppError
from app.core.i18n import set_language_preference
from app.core.extensions import db
from app.core.security import check_rate, rate_limit
from app.core.web_auth import login_user_to_session, logout_user_session, web_tenant_required
from app.models.appointment import ACTIVE_STATUSES, INACTIVE_STATUSES, Appointment
from app.models.lead import Lead
from app.models.tenant import Tenant
from app.models.user import User
from app.utils.i18n import translate
from app.utils.validation import validate_email

web_bp = Blueprint("web", __name__)


@web_bp.context_processor
def inject_tenant():
    from flask import current_app

    tid = session.get("tenant_id")
    tenant = None
    if tid:
        try:
            tenant = db.session.get(Tenant, uuid.UUID(tid))
        except ValueError:
            tenant = None

    # Prefer the tenant's OWN dedicated AI number (each plumber has their own in
    # a multi-tenant setup); fall back to the shared config number for tenants
    # not yet provisioned.
    shared_e164 = current_app.config.get("TWILIO_AI_PHONE_NUMBER", "+33159169691")
    shared_display = current_app.config.get("TWILIO_AI_PHONE_DISPLAY", "+33 1 59 16 96 91")
    tenant_e164 = getattr(tenant, "ai_phone_number", None)
    ai_phone_e164 = tenant_e164 or shared_e164
    ai_phone_display = _format_phone_display(tenant_e164) if tenant_e164 else shared_display

    return {
        "current_tenant": tenant,
        "twilio_ai_phone_display": ai_phone_display,
        "twilio_ai_phone_e164": ai_phone_e164,
    }


@web_bp.app_context_processor
def inject_seo_local_links():
    """Expose curated trade/city data so pages can link into the local SEO
    landing pages (internal maillage → faster indexing, more authority)."""
    from app.constants.cities import TOP_CITIES
    from app.constants.trades import SEO_LOCAL_TRADES, trade_icon, trade_label

    lang = getattr(g, "lang", "fr")
    return {
        "seo_local_trades": [
            {"key": k, "label": trade_label(k, lang), "icon": trade_icon(k)}
            for k in SEO_LOCAL_TRADES
        ],
        "seo_top_cities": TOP_CITIES,
    }


def _format_phone_display(e164: str | None) -> str:
    """Pretty-print an E.164 number for display, best-effort for FR."""
    if not e164:
        return ""
    raw = e164.strip()
    if raw.startswith("+33") and len(raw) == 12:
        rest = raw[3:]  # 9 national digits after +33 (leading 0 dropped in E.164)
        pairs = " ".join(rest[i:i + 2] for i in range(1, 9, 2))
        return f"+33 {rest[0]} {pairs}"
    return raw


@web_bp.route("/set-language/<lang>", methods=["GET"])
def set_language(lang):
    lang = set_language_preference(lang)
    redirect_to = request.referrer or url_for("web.client_home")
    response = make_response(redirect(redirect_to))
    response.set_cookie("lang", lang, max_age=365 * 24 * 3600)
    return response


@web_bp.route("/manifest.webmanifest", methods=["GET"])
def web_manifest():
    """PWA manifest — makes the dashboard installable on mobile and desktop."""
    from flask import current_app, send_from_directory

    return send_from_directory(
        current_app.static_folder,
        "manifest.webmanifest",
        mimetype="application/manifest+json",
    )


@web_bp.route("/public.webmanifest", methods=["GET"])
def public_manifest():
    """PWA manifest for the public showcase site — installable on mobile."""
    from flask import current_app, send_from_directory

    return send_from_directory(
        current_app.static_folder,
        "public.webmanifest",
        mimetype="application/manifest+json",
    )


@web_bp.route("/sw.js", methods=["GET"])
def service_worker():
    """Serve the service worker from the root so its scope covers every page."""
    from flask import current_app, send_from_directory

    response = make_response(
        send_from_directory(
            current_app.static_folder, "sw.js", mimetype="application/javascript"
        )
    )
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-cache"
    return response


@web_bp.route("/api/notifications/feed", methods=["GET"])
@web_tenant_required
def notifications_feed():
    """Return notifications created since a cursor (for live web/mobile alerts).

    Powers static/js/notifications.js: any new lead, urgent call, booked RDV,
    accepted/refused devis surfaces as a toast + native OS notification.
    """
    from app.models.notification import Notification

    now = datetime.now(timezone.utc)
    since_raw = request.args.get("since")
    since = None
    if since_raw:
        # A literal "+" in the query string decodes to a space; the ISO cursor
        # uses "T" as its date/time separator, so the only space is the offset's
        # "+" — restore it before parsing.
        since_raw = since_raw.strip().replace(" ", "+").replace("Z", "+00:00")
        try:
            since = datetime.fromisoformat(since_raw)
        except (ValueError, AttributeError):
            since = None
    if since is None:
        since = now - timedelta(minutes=1)

    # Compare against a naive-UTC cutoff: created_at is stored as UTC, and a
    # timezone-aware bind is silently ignored by SQLite (dev), which would make
    # the same row resurface on every poll. Naive-UTC is correct on both SQLite
    # and UTC Postgres (prod).
    if since.tzinfo is not None:
        since = since.astimezone(timezone.utc).replace(tzinfo=None)

    rows = (
        Notification.query.filter(
            Notification.tenant_id == g.tenant_id,
            Notification.created_at > since,
        )
        .order_by(Notification.created_at.asc())
        .limit(30)
        .all()
    )

    unread = (
        Notification.query.filter(
            Notification.tenant_id == g.tenant_id,
            Notification.read_at.is_(None),
        ).count()
    )

    payload = {
        "now": now.isoformat(),
        "unread": unread,
        "notifications": [n.to_dict() for n in rows],
    }

    # The notification centre asks for the latest history when its panel opens.
    if request.args.get("recent"):
        recent = (
            Notification.query.filter(Notification.tenant_id == g.tenant_id)
            .order_by(Notification.created_at.desc())
            .limit(20)
            .all()
        )
        payload["recent"] = [n.to_dict() for n in recent]

    return jsonify(payload), 200


@web_bp.route("/api/notifications/read", methods=["POST"])
@web_tenant_required
def notifications_mark_read():
    """Mark all of the tenant's notifications as read (clears the badge)."""
    from app.models.notification import Notification

    Notification.query.filter(
        Notification.tenant_id == g.tenant_id,
        Notification.read_at.is_(None),
    ).update({Notification.read_at: datetime.now(timezone.utc)})
    db.session.commit()
    return jsonify({"ok": True}), 200


@web_bp.route("/api/heatmap/collect", methods=["POST"])
def heatmap_collect():
    """Ingest client-side interaction events for the admin heatmap / journey.

    Visitor & session ids come from the httpOnly ``lp_vid`` / ``lp_sid`` cookies
    (the same ids used by server-side page-view tracking) so every visitor is
    followed as one continuous journey and the client can't spoof another
    visitor. Bots are dropped. Always returns 204 — tracking must never surface
    an error to the page.
    """
    from app.core.tracking import (
        SESSION_COOKIE,
        VISITOR_COOKIE,
        _device,
        is_bot,
    )

    try:
        ua = request.headers.get("User-Agent", "")
        if is_bot(ua):
            return ("", 204)
        visitor_id = request.cookies.get(VISITOR_COOKIE)
        session_id = request.cookies.get(SESSION_COOKIE)
        if not visitor_id:
            return ("", 204)  # no cookie yet → nothing to attach the journey to
        payload = request.get_json(silent=True) or {}
        events = payload.get("events")
        from app.services import heatmap as heatmap_service

        heatmap_service.record_events(visitor_id, session_id, _device(ua), events)
    except Exception:
        current_app.logger.exception("heatmap collect failed")
        try:
            db.session.rollback()
        except Exception:
            pass
    return ("", 204)


@web_bp.route("/api/heatmap/record", methods=["POST"])
def heatmap_record():
    """Ingest a session-replay track (cursor movements / clicks / scroll) so the
    admin can watch a "film" of a visitor's page visit.

    Same trust model as :func:`heatmap_collect`: visitor & session ids come from
    the httpOnly cookies, bots are dropped, always returns 204."""
    from app.core.tracking import (
        SESSION_COOKIE,
        VISITOR_COOKIE,
        _device,
        is_bot,
    )

    try:
        ua = request.headers.get("User-Agent", "")
        if is_bot(ua):
            return ("", 204)
        visitor_id = request.cookies.get(VISITOR_COOKIE)
        session_id = request.cookies.get(SESSION_COOKIE)
        if not visitor_id:
            return ("", 204)
        payload = request.get_json(silent=True) or {}
        from app.services import heatmap as heatmap_service

        heatmap_service.record_session(visitor_id, session_id, _device(ua), payload)
    except Exception:
        current_app.logger.exception("heatmap record failed")
        try:
            db.session.rollback()
        except Exception:
            pass
    return ("", 204)


@web_bp.route("/robots.txt", methods=["GET"])
def robots_txt():
    from app.utils.llm_discovery import render_robots_txt

    return make_response(render_robots_txt(), 200, {"Content-Type": "text/plain; charset=utf-8"})


@web_bp.route("/llms.txt", methods=["GET"])
def llms_txt():
    from app.utils.llm_discovery import render_llms_txt

    return make_response(render_llms_txt(), 200, {"Content-Type": "text/plain; charset=utf-8"})


@web_bp.route("/llms-full.txt", methods=["GET"])
def llms_full_txt():
    from app.utils.llm_discovery import render_llms_full_txt

    return make_response(render_llms_full_txt(), 200, {"Content-Type": "text/plain; charset=utf-8"})


@web_bp.route("/sitemap.xml", methods=["GET"])
def sitemap_xml():
    from datetime import date

    from app.models.blog_category import BlogCategory
    from app.models.blog_post import BlogPost
    from app.models.site_page import SitePage
    from app.services.artisan_directory import list_public_artisans
    from app.utils.seo import format_lastmod, site_base_url

    base = site_base_url()
    today = date.today().isoformat()
    urls: list[tuple[str, str, str, str | None]] = [
        ("", "daily", "1.0", today),
        ("/artisans", "daily", "0.95", today),
        ("/trouver-un-artisan", "weekly", "0.9", today),
        ("/depannage-urgent", "weekly", "0.9", today),
        ("/pro", "weekly", "0.9", today),
        ("/contact", "monthly", "0.5", today),
        ("/blog", "daily", "0.85", today),
        ("/mentions-legales", "yearly", "0.3", None),
        ("/confidentialite", "yearly", "0.3", None),
        ("/cgu", "yearly", "0.3", None),
        ("/cookies", "yearly", "0.3", None),
    ]

    # Programmatic local SEO: one clean-URL landing page per trade and per
    # trade × top-city so local-intent queries (« plombier lyon ») can rank.
    from app.constants.cities import TOP_CITIES
    from app.constants.trades import SEO_LOCAL_TRADES, TRADES

    # Every trade gets an indexable pillar page; only high-intent "dépannage"
    # trades get a page per top city (focused, avoids thin trade × city combos).
    for trade in (k for k in TRADES if k != "autre"):
        urls.append((f"/artisans/metier/{trade}", "weekly", "0.85", today))
    for trade in SEO_LOCAL_TRADES:
        for city_slug, _city_name in TOP_CITIES:
            urls.append((f"/artisans/{trade}/{city_slug}", "weekly", "0.7", today))

    for tenant in list_public_artisans(limit=2000):
        if tenant.public_slug:
            urls.append(
                (
                    f"/artisans/{tenant.public_slug}",
                    "weekly",
                    "0.8",
                    format_lastmod(tenant.created_at),
                )
            )

    for page in SitePage.query.filter_by(status="published").order_by(SitePage.updated_at.desc()).limit(100).all():
        if page.slug:
            urls.append((f"/p/{page.slug}", "weekly", "0.7", format_lastmod(page.updated_at)))

    for post in BlogPost.query.filter_by(status="published").order_by(BlogPost.published_at.desc()).limit(200).all():
        if post.slug:
            urls.append((f"/blog/{post.slug}", "weekly", "0.8", format_lastmod(post.published_at or post.updated_at)))

    for cat in BlogCategory.query.order_by(BlogCategory.sort_order).all():
        urls.append((f"/blog/categorie/{cat.slug}", "weekly", "0.75", today))

    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for path, freq, priority, lastmod in urls:
        loc = base + path
        lines.append("  <url>")
        lines.append(f"    <loc>{loc}</loc>")
        if lastmod:
            lines.append(f"    <lastmod>{lastmod}</lastmod>")
        lines.append(f"    <changefreq>{freq}</changefreq>")
        lines.append(f"    <priority>{priority}</priority>")
        lines.append("  </url>")
    lines.append("</urlset>")
    return make_response("\n".join(lines), 200, {"Content-Type": "application/xml; charset=utf-8"})


@web_bp.route("/", methods=["GET"])
def client_home():
    """Public homepage for customers looking for an artisan."""
    if session.get("user_id") and session.get("tenant_id"):
        return redirect(url_for("web.dashboard"))
    from app.constants.trades import trade_choices
    from app.services.artisan_directory import list_public_artisans

    lang = getattr(g, "lang", "fr")
    return render_template(
        "public/client_home.html",
        trades=trade_choices(lang),
        featured_artisans=list_public_artisans(limit=6),
    )


@web_bp.route("/pro", methods=["GET"])
def pro_landing():
    """Homepage for artisans — AI voice receptionist, pricing, demo."""
    if session.get("user_id") and session.get("tenant_id"):
        return redirect(url_for("web.dashboard"))
    from app.services import content_studio

    offers = content_studio.get_offers(active_only=True)
    return render_template("pro/landing.html", offers=offers or [])


# Legacy alias
@web_bp.route("/landing", methods=["GET"])
def landing():
    return redirect(url_for("web.pro_landing"), code=301)


@web_bp.route("/media/social/<path:filename>", methods=["GET"])
def social_media(filename):
    """Serve generated social images (e.g. from temp storage on ephemeral hosts)."""
    from app.services.social_image import uploads_dir

    safe = Path(filename).name
    if safe != filename or not safe.endswith(".png"):
        abort(404)
    path = uploads_dir() / safe
    if not path.is_file():
        abort(404)
    return send_from_directory(path.parent, path.name, mimetype="image/png")


@web_bp.route("/blog", methods=["GET"])
def blog_index():
    from app.services import blog as blog_svc
    from app.utils.seo import blog_index_json_ld, json_ld_script

    categories = blog_svc.list_categories()
    posts = blog_svc.list_published_posts(limit=48)
    featured = blog_svc.featured_post()
    meta_desc = (
        "Conseils artisans, dépannage à la maison et téléphonie IA : articles pratiques "
        "par l'équipe PilotCore pour particuliers et professionnels du bâtiment."
    )
    keywords = (
        "blog artisan, dépannage maison, plombier conseils, standard téléphonique IA, "
        "PilotCore, RDV artisan, gestion artisan"
    )
    return render_template(
        "public/blog/index.html",
        nav_active="blog",
        categories=categories,
        posts=posts,
        featured=featured,
        active_category=None,
        meta_description=meta_desc,
        meta_keywords=keywords,
        json_ld=json_ld_script(blog_index_json_ld(posts, lang=request.args.get("lang") or "fr")),
    )


@web_bp.route("/blog/categorie/<slug>", methods=["GET"])
def blog_category(slug):
    from app.services import blog as blog_svc
    from app.utils.seo import blog_index_json_ld, json_ld_script

    category = blog_svc.get_category_by_slug(slug)
    if not category:
        abort(404)
    posts = blog_svc.list_published_posts(limit=48, category_id=category.id)
    featured = posts[0] if posts else None
    meta_desc = (category.description or category.name)[:300]
    return render_template(
        "public/blog/index.html",
        nav_active="blog",
        categories=blog_svc.list_categories(),
        posts=posts,
        featured=featured if featured and featured != blog_svc.featured_post() else None,
        active_category=category,
        meta_description=meta_desc,
        meta_keywords=f"{category.name}, blog PilotCore, artisan, dépannage",
        json_ld=json_ld_script(blog_index_json_ld(posts)),
    )


@web_bp.route("/blog/<slug>", methods=["GET"])
def blog_article(slug):
    from app.services import blog as blog_svc
    from app.utils.seo import blog_posting_json_ld, json_ld_script

    post = blog_svc.get_published_post(slug)
    if not post:
        abort(404)
    related = blog_svc.related_posts(post, limit=3)
    body_html, toc = blog_svc.prepare_article_body(post.body_html or "")
    from app.utils.seo import blog_posting_json_ld, json_ld_script, logo_url

    return render_template(
        "public/blog/article.html",
        nav_active="blog",
        post=post,
        body_html=body_html,
        toc=toc,
        related=related,
        og_image=logo_url(),
        json_ld=json_ld_script(blog_posting_json_ld(post)),
    )


@web_bp.route("/p/<slug>", methods=["GET"])
def site_page(slug):
    """Serve a published custom page authored in the admin studio."""
    from flask import abort

    from app.models.site_page import SitePage

    page = SitePage.query.filter(
        SitePage.slug == slug, SitePage.status == "published"
    ).first()
    if not page:
        abort(404)
    return render_template("public/site_page.html", page=page, preview=False)


# --- Legal / RGPD pages -----------------------------------------------------
@web_bp.route("/mentions-legales", methods=["GET"])
def legal_notice():
    return render_template("public/legal/mentions.html", updated="6 juillet 2026")


@web_bp.route("/confidentialite", methods=["GET"])
def privacy():
    return render_template("public/legal/confidentialite.html", updated="6 juillet 2026")


@web_bp.route("/cgu", methods=["GET"])
def cgu():
    return render_template("public/legal/cgu.html", updated="6 juillet 2026")


@web_bp.route("/cookies", methods=["GET"])
def cookies_policy():
    return render_template("public/legal/cookies.html", updated="6 juillet 2026")


@web_bp.route("/suppression-donnees", methods=["GET"])
def data_deletion():
    return render_template("public/legal/suppression_donnees.html", updated="7 juillet 2026")


@web_bp.route("/contact", methods=["GET", "POST"])
def contact():
    from app.services.contact_form import submit_contact

    success = request.args.get("sent") == "1"
    error = None
    form = {"name": "", "email": "", "subject": "", "message": ""}

    if request.method == "POST":
        if request.form.get("website"):
            return redirect(url_for("web.contact", sent=1))

        if not check_rate("contact_form", limit=5, window=3600):
            error = translate("contact.error.rate_limited")
        else:
            form["name"] = (request.form.get("name") or "").strip()
            form["email"] = (request.form.get("email") or "").strip()
            form["subject"] = (request.form.get("subject") or "").strip()
            form["message"] = (request.form.get("message") or "").strip()

            if not form["name"]:
                error = translate("contact.error.name_required")
            elif not form["email"]:
                error = translate("contact.error.email_required")
            elif not form["message"]:
                error = translate("contact.error.message_required")
            elif len(form["message"]) > 5000:
                error = translate("contact.error.message_too_long")
            else:
                try:
                    validate_email(form["email"])
                except AppError:
                    error = translate("login.error.invalid_email")

            if not error:
                submit_contact(
                    form["name"],
                    form["email"],
                    form["subject"],
                    form["message"],
                )
                return redirect(url_for("web.contact", sent=1))

    return render_template("public/contact.html", error=error, success=success, form=form)


@web_bp.route("/trouver-un-artisan", methods=["GET"])
def find_artisan():
    """SEO/CRO pillar page — how to find and book a trusted artisan."""
    from app.constants.trades import trade_choices

    lang = getattr(g, "lang", "fr")
    return render_template("public/find_artisan.html", trades=trade_choices(lang))


@web_bp.route("/depannage-urgent", methods=["GET"])
def depannage_urgent():
    """SEO/CRO landing page for high-intent emergency ("dépannage urgent") search."""
    from app.constants.trades import trade_choices

    lang = getattr(g, "lang", "fr")
    return render_template("public/depannage_urgent.html", trades=trade_choices(lang))


@web_bp.route("/artisans", methods=["GET"])
def artisan_directory():
    """Public marketplace — find artisans and book online (Doctolib-style)."""
    from app.constants.trades import TRADES, trade_choices, trade_label
    from app.services.artisan_directory import list_public_artisans
    from app.utils.seo import directory_seo

    trade = (request.args.get("metier") or "").strip() or None
    city = (request.args.get("ville") or "").strip() or None
    q = (request.args.get("q") or request.args.get("ai") or "").strip() or None
    artisans = list_public_artisans(trade=trade, city=city, q=q)
    lang = getattr(g, "lang", "fr")
    trades = trade_choices(lang)
    seo = directory_seo(
        trade_key=trade if trade in TRADES else None,
        trade_label=trade_label(trade, lang) if trade in TRADES else None,
        city=city,
        q=q,
        lang=lang,
        result_count=len(artisans),
    )
    return render_template(
        "public/annuaire.html",
        artisans=artisans,
        trades=trades,
        seo=seo,
        filters={"metier": trade or "", "ville": city or "", "q": q or ""},
    )


@web_bp.route("/artisans/metier/<trade>", methods=["GET"])
def artisan_trade_landing(trade):
    """Clean-URL pillar page for a trade (e.g. /artisans/metier/plombier)."""
    from flask import abort

    from app.constants.cities import TOP_CITIES
    from app.constants.trades import TRADES, trade_choices, trade_label
    from app.services.artisan_directory import list_public_artisans
    from app.utils.seo import local_landing_seo

    trade = (trade or "").strip().lower()
    if trade not in TRADES:
        abort(404)
    lang = getattr(g, "lang", "fr")
    label = trade_label(trade, lang)
    artisans = list_public_artisans(trade=trade)
    seo = local_landing_seo(
        trade_key=trade,
        trade_label=label,
        canonical_path=f"/artisans/metier/{trade}",
        lang=lang,
    )
    return render_template(
        "public/annuaire.html",
        artisans=artisans,
        trades=trade_choices(lang),
        seo=seo,
        filters={"metier": trade, "ville": "", "q": ""},
        local_ctx={"trade_key": trade, "trade_label": label, "city": None},
        top_cities=TOP_CITIES[:12],
    )


@web_bp.route("/artisans/<trade>/<city>", methods=["GET"])
def artisan_trade_city_landing(trade, city):
    """Clean-URL local landing page (e.g. /artisans/plombier/lyon).

    Programmatic local SEO: one self-canonical, keyword-rich, indexable page per
    trade × city so high-intent queries like « plombier lyon » can rank.
    """
    from flask import abort

    from app.constants.cities import TOP_CITIES, city_display_name, city_slugify
    from app.constants.trades import TRADES, trade_choices, trade_label
    from app.services.artisan_directory import list_public_artisans
    from app.utils.seo import local_landing_seo

    trade = (trade or "").strip().lower()
    if trade not in TRADES:
        abort(404)
    city_slug = city_slugify(city)
    if not city_slug:
        abort(404)
    # Redirect messy inputs (accents, spaces, casing) to the canonical slug URL.
    if city_slug != city:
        return redirect(url_for("web.artisan_trade_city_landing", trade=trade, city=city_slug), code=301)

    lang = getattr(g, "lang", "fr")
    label = trade_label(trade, lang)
    city_name = city_display_name(city_slug)
    artisans = list_public_artisans(trade=trade, city=city_name)
    seo = local_landing_seo(
        trade_key=trade,
        trade_label=label,
        city=city_name,
        canonical_path=f"/artisans/{trade}/{city_slug}",
        lang=lang,
    )
    return render_template(
        "public/annuaire.html",
        artisans=artisans,
        trades=trade_choices(lang),
        seo=seo,
        filters={"metier": trade, "ville": city_name, "q": ""},
        local_ctx={"trade_key": trade, "trade_label": label, "city": city_name},
        top_cities=TOP_CITIES[:12],
    )


@web_bp.route("/api/public/artisans/search", methods=["GET"])
def artisan_directory_search():
    from app.constants.trades import trade_choices
    from app.services.artisan_directory import search_public_artisans

    trade = (request.args.get("metier") or "").strip() or None
    city = (request.args.get("ville") or "").strip() or None
    q = (request.args.get("q") or "").strip() or None
    lang = getattr(g, "lang", "fr")
    payload = search_public_artisans(trade=trade, city=city, q=q, lang=lang)
    payload["trades"] = trade_choices(lang)
    return jsonify(payload)


@web_bp.route("/api/public/artisans/ai-search", methods=["GET", "POST"])
@rate_limit(limit=20, window=60, scope="ai_search")
def artisan_directory_ai_search():
    """Natural-language search — « j'ai une fuite à Lyon » → plombier + Lyon."""
    from app.services.ai_search import ai_search

    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        query = (data.get("q") or data.get("query") or "").strip()
    else:
        query = (request.args.get("q") or "").strip()
    if not query:
        return jsonify({"error": "query required"}), 422
    lang = getattr(g, "lang", "fr")
    return jsonify(ai_search(query, lang=lang))


@web_bp.route("/artisans/<slug>", methods=["GET"])
def artisan_profile(slug):
    from flask import abort
    from urllib.parse import quote

    from app.constants.trades import trade_icon, trade_label, trade_schema_type
    from app.services.artisan_directory import get_public_artisan_by_slug
    from app.services.availability import list_available_slots
    from app.services.plan_features import has_feature

    def _tel_href(phone):
        if not phone:
            return None
        cleaned = "".join(c for c in phone.strip() if c.isdigit() or c == "+")
        return f"tel:{cleaned}" if cleaned else None

    tenant = get_public_artisan_by_slug(slug)
    if not tenant:
        abort(404)
    lang = getattr(g, "lang", "fr")
    slots = list_available_slots(tenant.id, limit=12)
    slot_items = [
        {
            "iso": s.isoformat(),
            "label": s.astimezone(__import__("zoneinfo").ZoneInfo("Europe/Paris")).strftime(
                "%a %d/%m · %H:%M"
            ),
        }
        for s in slots
    ]
    from app.routes.customer import customer_session_payload

    customer_profile = customer_session_payload()
    pending_booking = session.get("pending_booking")

    primary_phone = tenant.ai_phone_number or tenant.phone_number
    show_direct = bool(getattr(tenant, "show_direct_phone_public", False))
    secondary_phone = (
        tenant.phone_number
        if show_direct
        and tenant.ai_phone_number
        and tenant.phone_number
        and tenant.ai_phone_number.strip() != tenant.phone_number.strip()
        else None
    )
    has_map = bool(tenant.latitude and tenant.longitude)
    full_address = tenant.full_address or None
    has_address = bool(tenant.address or tenant.city or tenant.postal_code)
    maps_url = None
    if has_map:
        maps_url = f"https://www.google.com/maps?q={tenant.latitude},{tenant.longitude}"
    elif full_address:
        maps_url = f"https://www.google.com/maps/search/?api=1&query={quote(full_address)}"

    return render_template(
        "public/artisan_profile.html",
        tenant=tenant,
        online_booking_enabled=has_feature(tenant, "auto_booking"),
        trade_label=trade_label(tenant.trade_type, lang),
        trade_icon=trade_icon(tenant.trade_type),
        trade_schema_type=trade_schema_type(tenant.trade_type),
        slot_items=slot_items,
        customer_profile=customer_profile,
        pending_booking=pending_booking,
        primary_phone=primary_phone,
        primary_is_ai=bool(tenant.ai_phone_number),
        secondary_phone=secondary_phone,
        tel_primary=_tel_href(primary_phone),
        tel_secondary=_tel_href(secondary_phone),
        has_map=has_map,
        has_address=has_address,
        full_address=full_address,
        maps_url=maps_url,
    )


@web_bp.route("/api/public/artisans/<slug>/slots", methods=["GET"])
def artisan_public_slots(slug):
    from app.services.artisan_directory import get_public_artisan_by_slug
    from app.services.availability import list_available_slots

    tenant = get_public_artisan_by_slug(slug)
    if not tenant:
        return jsonify({"error": "not found"}), 404
    slots = list_available_slots(tenant.id, limit=12)
    return jsonify(
        {
            "slots": [
                {
                    "iso": s.isoformat(),
                    "label": s.astimezone(__import__("zoneinfo").ZoneInfo("Europe/Paris")).strftime(
                        "%a %d/%m · %H:%M"
                    ),
                }
                for s in slots
            ]
        }
    )


@web_bp.route("/demo/simulate", methods=["POST"])
@rate_limit(limit=15, window=60, scope="demo_simulate")
def demo_simulate():
    from app.services.demo_simulate import simulate_inbound_demo

    data = request.get_json(silent=True) or {}
    transcript = (data.get("transcript") or "").strip()
    phone = (data.get("phone") or "+33600000000").strip()
    if not transcript:
        return jsonify({"error": "transcript required"}), 422
    try:
        result = simulate_inbound_demo(transcript, phone)
        return jsonify(result), 200
    except Exception:
        return jsonify({"error": "demo failed", "demo": True}), 503


def _post_register_redirect(tenant, plan_key):
    """Where to send a freshly registered artisan.

    When they came from a paid pricing card (``?plan=starter|pro|premium``) we
    take them straight to Stripe Checkout for that plan so the subscription is
    set up in one flow. Falls back to the billing page (Stripe not configured or
    checkout failed) or the dashboard (free trial / no plan chosen)."""
    from app.services import billing

    if plan_key and plan_key in billing.available_plans():
        if billing.is_configured():
            try:
                success_url = url_for("billing.billing_page", status="success", _external=True)
                cancel_url = url_for("billing.billing_page", status="cancel", _external=True)
                checkout_url = billing.create_checkout_session(tenant, plan_key, success_url, cancel_url)
                return redirect(checkout_url, code=303)
            except Exception:
                current_app.logger.exception(
                    "Post-register checkout failed tenant=%s plan=%s", tenant.id, plan_key
                )
                return redirect(url_for("billing.billing_page", status="error"))
        # Stripe not configured — land on billing so they can subscribe later.
        return redirect(url_for("billing.billing_page", status="plan_selected"))
    return redirect(url_for("web.dashboard"))


@web_bp.route("/register", methods=["GET", "POST"])
def register():
    if session.get("user_id") and session.get("tenant_id"):
        return redirect(url_for("web.dashboard"))

    from app.services import billing

    # Plan pre-selected from the pricing grid (?plan=starter|pro|premium), carried
    # through the two-step form via a hidden field so it survives the POST.
    selected_plan = (request.values.get("plan") or "").strip().lower()
    if selected_plan not in billing.available_plans():
        selected_plan = ""

    error = None
    form = {}

    if request.method == "POST":
        from app.core.errors import ConflictError
        from app.services.signup_service import register_plumber
        from app.utils.validation import validate_password

        if not check_rate("web_register", limit=5, window=3600):
            error = translate("login.error.rate_limited")
        else:
            form = {
                "company_name": (request.form.get("company_name") or "").strip(),
                "first_name": (request.form.get("first_name") or "").strip(),
                "last_name": (request.form.get("last_name") or "").strip(),
                "email": (request.form.get("email") or "").strip().lower(),
                "phone": (request.form.get("phone") or "").strip(),
                "city": (request.form.get("city") or "").strip(),
                "trade_type": (request.form.get("trade_type") or "plombier").strip(),
            }
            password = request.form.get("password") or ""
            confirm = request.form.get("confirm_password") or ""

            if not form["company_name"] or not form["email"] or not password:
                error = translate("register.error.required")
            elif password != confirm:
                error = translate("register.error.password_mismatch")
            else:
                try:
                    validate_email(form["email"])
                    validate_password(password)
                    user, tenant = register_plumber(
                        email=form["email"],
                        password=password,
                        company_name=form["company_name"],
                        phone=form["phone"] or None,
                        city=form["city"] or None,
                        first_name=form["first_name"] or None,
                        last_name=form["last_name"] or None,
                        trade_type=form["trade_type"],
                    )
                    login_user_to_session(user)
                    return _post_register_redirect(tenant, selected_plan)
                except ConflictError:
                    error = translate("register.error.email_taken")
                except AppError as e:
                    if "email" in str(e.message).lower():
                        error = translate("login.error.invalid_email")
                    elif "password" in str(e.message).lower():
                        error = translate("register.error.password_short")
                    else:
                        error = str(e.message)
                except Exception:
                    current_app.logger.exception("Registration failed for %s", form.get("email"))
                    db.session.rollback()
                    error = translate("register.error.generic")

    from app.constants.trades import trade_choices

    lang = getattr(g, "lang", "fr")
    selected_plan_name = billing.available_plans().get(selected_plan, {}).get("name", "") if selected_plan else ""
    return render_template(
        "pro/register.html",
        error=error,
        form=form,
        trades=trade_choices(lang),
        selected_plan=selected_plan,
        selected_plan_name=selected_plan_name,
    )


@web_bp.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id") and session.get("tenant_id"):
        return redirect(url_for("web.dashboard"))

    error_key = session.pop("flash_error_key", None)
    error = translate(error_key) if error_key else None

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        if not check_rate("web_login", limit=10, window=300):
            error = translate("login.error.rate_limited")
        elif not email or not password:
            error = translate("login.error.required")
        else:
            try:
                email = validate_email(email)
            except AppError:
                error = translate("login.error.invalid_email")
            else:
                user = User.query.filter_by(email=email).first()
                if not user or not user.check_password(password):
                    error = translate("login.error.invalid_credentials")
                elif not user.tenant_id:
                    error = translate("login.error.no_tenant")
                else:
                    login_user_to_session(user)
                    return redirect(url_for("web.dashboard"))

    return render_template("pro/login.html", error=error)


@web_bp.route("/logout", methods=["GET"])
def logout():
    lang = session.get("lang")
    logout_user_session()
    if lang:
        session["lang"] = lang
    return redirect(url_for("web.client_home"))


def _login_url_for(user) -> str:
    """Send the user back to the login that matches their account type."""
    if user is not None and getattr(user, "role", None) == "customer":
        return url_for("customer.login")
    return url_for("web.login")


@web_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    """Request a reset link. Works for artisan and customer accounts alike."""
    sent = False
    error = None
    email = ""
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        if not check_rate("forgot_password", limit=5, window=900):
            error = translate("login.error.rate_limited")
        elif not email:
            error = translate("forgot.error.email_required")
        else:
            # Always report success — never reveal whether an email exists.
            sent = True
            try:
                validate_email(email)
                user = User.query.filter_by(email=email).first()
                if user:
                    from app.services.password_reset import generate_reset_token
                    from app.services.transactional_email import send_password_reset

                    token = generate_reset_token(user)
                    reset_url = url_for("web.reset_password", token=token, _external=True)
                    send_password_reset(user, reset_url)
            except AppError:
                pass  # invalid email format — still show generic success
            except Exception:
                current_app.logger.exception("Password reset request failed for %s", email)

    return render_template("pro/forgot_password.html", sent=sent, error=error, email=email)


@web_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    from app.services.password_reset import verify_reset_token

    user = verify_reset_token(token)
    if not user:
        return render_template("pro/reset_password.html", invalid=True)

    error = None
    if request.method == "POST":
        from app.utils.validation import validate_password

        new_password = request.form.get("new_password") or ""
        confirm = request.form.get("confirm_password") or ""
        if len(new_password) < 8:
            error = translate("settings.error.password_short")
        elif new_password != confirm:
            error = translate("settings.error.password_mismatch")
        else:
            try:
                validate_password(new_password)
            except AppError:
                error = translate("settings.error.password_short")
            else:
                user.set_password(new_password)
                db.session.commit()
                try:
                    from app.services.transactional_email import send_password_changed

                    send_password_changed(user)
                except Exception:
                    current_app.logger.exception("Password-changed email failed user=%s", user.id)
                return redirect(_login_url_for(user) + "?reset=1")

    return render_template("pro/reset_password.html", invalid=False, error=error, token=token)


@web_bp.route("/dashboard", methods=["GET"])
@web_tenant_required
def dashboard():
    tenant = db.session.get(Tenant, g.tenant_id)
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow = today_start + timedelta(days=1)

    all_leads = Lead.query.filter_by(tenant_id=g.tenant_id).filter(Lead.archived_at.is_(None)).all()
    calls_today = Lead.query.filter(
        Lead.tenant_id == g.tenant_id,
        Lead.created_at >= today_start,
        Lead.archived_at.is_(None),
    ).count()
    appointments_today = (
        Appointment.active_query(g.tenant_id)
        .filter(Appointment.date_time >= today_start, Appointment.date_time < tomorrow)
        .count()
    )
    from app.services import quote_engine

    pending_quotes = quote_engine.pending_quote_count(g.tenant_id)
    quote_followups = quote_engine.followup_count(g.tenant_id)
    urgencies = Lead.query.filter(
        Lead.tenant_id == g.tenant_id,
        Lead.urgency_level == "high",
        Lead.archived_at.is_(None),
    ).count()

    # Step 2 of the workflow: "prospects à traiter" — leads the plumber hasn't
    # turned into a booking yet (status "new"). These are the ones needing action.
    new_leads_count = Lead.query.filter(
        Lead.tenant_id == g.tenant_id,
        Lead.status == "new",
        Lead.archived_at.is_(None),
    ).count()

    recent_leads = (
        Lead.query.filter_by(tenant_id=g.tenant_id)
        .filter(Lead.archived_at.is_(None))
        .order_by(Lead.created_at.desc())
        .limit(8)
        .all()
    )
    today_appointments = (
        Appointment.active_query(g.tenant_id)
        .filter(Appointment.date_time >= today_start, Appointment.date_time < tomorrow)
        .options(joinedload(Appointment.lead))
        .order_by(Appointment.date_time.asc())
        .all()
    )
    upcoming_appointments = (
        Appointment.active_query(g.tenant_id)
        .filter(Appointment.date_time >= today_start)
        .options(joinedload(Appointment.lead))
        .order_by(Appointment.date_time.asc())
        .limit(5)
        .all()
    )
    # Step 4 of the workflow: RDV still to come (today included) — drives the
    # "Rendez-vous" pipeline step count.
    upcoming_count = (
        Appointment.active_query(g.tenant_id)
        .filter(Appointment.date_time >= today_start)
        .count()
    )
    total_leads = Lead.query.filter_by(tenant_id=g.tenant_id).filter(Lead.archived_at.is_(None)).count()
    next_appointment = (
        Appointment.active_query(g.tenant_id)
        .filter(Appointment.date_time >= datetime.now(timezone.utc))
        .options(joinedload(Appointment.lead))
        .order_by(Appointment.date_time.asc())
        .first()
    )

    return render_template(
        "artisan/dashboard.html",
        tenant=tenant,
        calls_today=calls_today,
        appointments_today=appointments_today,
        pending_quotes=pending_quotes,
        quote_followups=quote_followups,
        urgencies=urgencies,
        new_leads_count=new_leads_count,
        recent_leads=recent_leads,
        today_appointments=today_appointments,
        upcoming_appointments=upcoming_appointments,
        upcoming_count=upcoming_count,
        total_leads=total_leads,
        next_appointment=next_appointment,
    )


@web_bp.route("/leads", methods=["GET"])
@web_tenant_required
def leads_page():
    show_archived = request.args.get("view") == "archived"
    query = Lead.query.filter_by(tenant_id=g.tenant_id)
    if show_archived:
        query = query.filter(Lead.archived_at.isnot(None))
    else:
        query = query.filter(Lead.archived_at.is_(None))
    leads = query.order_by(Lead.created_at.desc()).all()
    return render_template(
        "artisan/leads.html",
        leads=leads,
        show_archived=show_archived,
    )


@web_bp.route("/leads/<lead_id>/archive", methods=["POST"])
@web_tenant_required
def archive_lead(lead_id):
    try:
        lid = uuid.UUID(lead_id)
    except ValueError:
        return redirect(url_for("web.leads_page"))

    lead = Lead.query.filter_by(id=lid, tenant_id=g.tenant_id).first()
    if lead:
        lead.archived_at = datetime.now(timezone.utc)
        for appt in lead.appointments.filter(
            Appointment.status.in_(ACTIVE_STATUSES)
        ).all():
            appt.status = "completed"
        db.session.commit()

    return redirect(request.referrer or url_for("web.leads_page"))


@web_bp.route("/leads/<lead_id>/cancel", methods=["POST"])
@web_tenant_required
def cancel_lead(lead_id):
    """Cancel a booked job from the prospect card.

    Records the reason, cancels any upcoming appointment and — unless the plumber
    unticks the box — tells the client (SMS/email) that the intervention is
    cancelled, with the reason. A notification is pushed so it shows in the feed.
    """
    from app.services import notifications
    from app.services.sms import send_sms

    try:
        lid = uuid.UUID(lead_id)
    except ValueError:
        return redirect(url_for("web.leads_page"))

    lead = Lead.query.filter_by(id=lid, tenant_id=g.tenant_id).first()
    if not lead:
        return redirect(url_for("web.leads_page"))

    reason = (request.form.get("reason") or "").strip()
    notify_client = request.form.get("notify_client") == "1"
    tenant = db.session.get(Tenant, g.tenant_id)

    lead.cancelled_at = datetime.now(timezone.utc)
    lead.cancel_reason = reason or None
    lead.status = "lost"

    # Cancel still-active appointments tied to this lead so they leave the agenda.
    for appt in lead.appointments.filter(
        Appointment.status.in_(("scheduled", "confirmed"))
    ).all():
        appt.status = "cancelled"
    db.session.commit()

    if notify_client and lead.phone:
        company = (tenant.name or "votre artisan").strip()
        body = (
            f"Bonjour, votre rendez-vous avec {company} a été annulé."
            + (f" Motif : {reason}." if reason else "")
            + " Contactez-nous pour reprogrammer."
        )
        send_sms(lead.phone, body)

    notifications.push_notification(
        g.tenant_id,
        "lead_cancelled",
        f"🚫 Intervention annulée — {lead.name}",
        reason or "",
        icon="🚫",
        url="/leads",
    )

    return redirect(request.referrer or url_for("web.leads_page"))


@web_bp.route("/marketing", methods=["GET"])
@web_tenant_required
def marketing_page():
    """Segmentation marketing / SAV — completed clients grouped into segments
    the plumber can run an SMS / e-mail campaign against."""
    from app.services import marketing
    from app.services.plan_features import has_feature

    tenant = db.session.get(Tenant, g.tenant_id)
    if not has_feature(tenant, "crm_marketing"):
        return render_template(
            "artisan/plan_upgrade.html",
            feature="crm_marketing",
            required_plan="Premium",
        )

    segments = marketing.build_segments(g.tenant_id)
    result = session.pop("marketing_result", None)
    return render_template(
        "artisan/marketing.html",
        segments=segments,
        total_clients=segments[0]["count"] if segments else 0,
        result=result,
    )


@web_bp.route("/marketing/send", methods=["POST"])
@web_tenant_required
def marketing_send():
    """Send a one-off campaign to a segment of completed clients."""
    from app.services import marketing, notifications
    from app.services.plan_features import has_feature

    tenant = db.session.get(Tenant, g.tenant_id)
    if not has_feature(tenant, "crm_marketing"):
        session["marketing_result"] = {"error": "plan"}
        return redirect(url_for("web.marketing_page"))

    segment_key = (request.form.get("segment") or "").strip()
    channel = (request.form.get("channel") or "sms").strip()
    subject = (request.form.get("subject") or "").strip()
    message = (request.form.get("message") or "").strip()

    if channel not in ("sms", "email", "both"):
        channel = "sms"

    if not segment_key or not message:
        session["marketing_result"] = {"error": "empty"}
        return redirect(url_for("web.marketing_page"))

    result = marketing.send_campaign(g.tenant_id, segment_key, channel, subject, message)
    session["marketing_result"] = result

    if result.get("recipients"):
        notifications.push_notification(
            g.tenant_id,
            "marketing_campaign",
            f"📣 Campagne envoyée — {result['recipients']} client(s)",
            f"SMS : {result['sms_sent']}/{result['sms_attempted']} · "
            f"E-mail : {result['email_sent']}/{result['email_attempted']}",
            icon="📣",
            url="/marketing",
        )

    return redirect(url_for("web.marketing_page"))


@web_bp.route("/leads/<lead_id>/unarchive", methods=["POST"])
@web_tenant_required
def unarchive_lead(lead_id):
    try:
        lid = uuid.UUID(lead_id)
    except ValueError:
        return redirect(url_for("web.leads_page", view="archived"))

    lead = Lead.query.filter_by(id=lid, tenant_id=g.tenant_id).first()
    if lead:
        lead.archived_at = None
        db.session.commit()

    return redirect(url_for("web.leads_page", view="archived"))


def _appointment_sort_key(appt) -> float:
    """Timezone-safe sort key for PostgreSQL (aware) and legacy (naive) rows."""
    dt = appt.date_time
    if dt is None:
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _map_coord(value):
    """JSON-safe latitude/longitude for Leaflet markers."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


@web_bp.route("/appointments", methods=["GET"])
@web_tenant_required
def appointments_page():
    from collections import defaultdict

    from app.utils.geocoding import geocode_address

    DAYS_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
    # Nominatim is rate-limited (~1 req/s). Cap per page load so Gunicorn does not time out.
    max_geocode_per_request = 3

    appointments = (
        Appointment.active_query(g.tenant_id)
        .options(contains_eager(Appointment.lead))
        .order_by(Appointment.date_time.asc())
        .all()
    )

    geocoded = False
    geocode_attempts = 0
    for appt in appointments:
        if geocode_attempts >= max_geocode_per_request:
            break
        lead = appt.lead
        if not lead or not lead.address:
            continue
        if lead.latitude is None or lead.longitude is None:
            geocode_attempts += 1
            try:
                coords = geocode_address(lead.address)
            except Exception:
                logger.exception("Geocoding failed for lead %s", lead.id)
                coords = None
            if coords:
                lead.latitude, lead.longitude = coords
                geocoded = True

    if geocoded:
        try:
            db.session.commit()
        except Exception:
            logger.exception("Could not persist geocoded lead coordinates")
            db.session.rollback()

    agenda_by_day = defaultdict(list)
    for appt in appointments:
        if appt.date_time:
            day_key = appt.date_time.strftime("%Y-%m-%d")
            agenda_by_day[day_key].append(appt)

    agenda_days = []
    for day_key in sorted(agenda_by_day.keys()):
        day_appts = agenda_by_day[day_key]
        dt = day_appts[0].date_time
        agenda_days.append(
            {
                "day_name": DAYS_FR[dt.weekday()],
                "date_display": dt.strftime("%d/%m/%Y"),
                "count": len(day_appts),
                "appointments": day_appts,
            }
        )

    tenant = db.session.get(Tenant, g.tenant_id)
    depot = None
    depot_lat = _map_coord(getattr(tenant, "latitude", None)) if tenant else None
    depot_lng = _map_coord(getattr(tenant, "longitude", None)) if tenant else None
    if depot_lat is not None and depot_lng is not None:
        depot = {
            "lat": depot_lat,
            "lng": depot_lng,
            "name": tenant.name,
            "label": tenant.city or tenant.name,
        }

    map_markers = []
    if depot:
        map_markers.append(
            {
                "id": "depot",
                "lat": depot["lat"],
                "lng": depot["lng"],
                "name": depot["label"],
                "phone": "",
                "address": tenant.full_address or "",
                "issue": "",
                "time": "Base",
                "date": "",
                "status": "depot",
                "is_depot": True,
            }
        )

    for appt in appointments:
        lead = appt.lead
        if not lead:
            continue
        lat = _map_coord(lead.latitude)
        lng = _map_coord(lead.longitude)
        if lat is None or lng is None:
            continue
        map_markers.append(
            {
                "id": str(appt.id),
                "lat": lat,
                "lng": lng,
                "name": lead.name or "",
                "phone": lead.phone or "",
                "address": lead.address or "",
                "issue": lead.issue_type or "",
                "time": appt.date_time.strftime("%H:%M") if appt.date_time else "",
                "date": appt.date_time.strftime("%d/%m/%Y") if appt.date_time else "",
                "status": appt.status or "scheduled",
                "is_depot": False,
            }
        )

    route_days = []
    for day_key in sorted(agenda_by_day.keys()):
        day_appts = sorted(agenda_by_day[day_key], key=_appointment_sort_key)
        stops = []
        if depot:
            stops.append(
                {
                    "id": f"depot-{day_key}",
                    "lat": depot["lat"],
                    "lng": depot["lng"],
                    "time": "—",
                    "name": depot["label"],
                    "address": tenant.full_address or "",
                    "is_depot": True,
                }
            )
        for appt in day_appts:
            lead = appt.lead
            if not lead:
                continue
            lat = _map_coord(lead.latitude)
            lng = _map_coord(lead.longitude)
            if lat is None or lng is None:
                continue
            if appt.status in INACTIVE_STATUSES:
                continue
            stops.append(
                {
                    "id": str(appt.id),
                    "lat": lat,
                    "lng": lng,
                    "name": lead.name or "",
                    "phone": lead.phone or "",
                    "address": lead.address or "",
                    "issue": lead.issue_type or "",
                    "time": appt.date_time.strftime("%H:%M") if appt.date_time else "",
                    "date": appt.date_time.strftime("%d/%m/%Y") if appt.date_time else "",
                    "status": appt.status or "scheduled",
                    "is_depot": False,
                }
            )

        if len(stops) >= 2:
            dt = day_appts[0].date_time
            route_days.append(
                {
                    "day_key": day_key,
                    "label": f"{DAYS_FR[dt.weekday()]} {dt.strftime('%d/%m/%Y')}",
                    "stops": stops,
                }
            )

    return render_template(
        "artisan/appointments.html",
        appointments=appointments,
        agenda_days=agenda_days,
        map_markers=map_markers,
        route_days=route_days,
    )


@web_bp.route("/test-call", methods=["GET"])
@web_tenant_required
def test_call_page():
    # The former "Test appel" page has been replaced by the commercial chatbot.
    # Keep the old URL working for any bookmarks by redirecting to it.
    return redirect(url_for("chatbot.chatbot_console"))


def _normalize_phone(value):
    if not value:
        return None
    cleaned = "".join(c for c in value.strip() if c.isdigit() or c == "+")
    return cleaned or None


def _normalize_siret(value):
    if not value:
        return None
    digits = "".join(c for c in value if c.isdigit())
    return digits if len(digits) == 14 else None


# Signature pad output is a PNG data URL. Accept only that, and cap the size so
# an oversized paste can't bloat the row (a normal signature is a few KB).
_SIGNATURE_MAX_LEN = 300_000


def _normalize_signature(value):
    value = (value or "").strip()
    if not value:
        return None
    if not value.startswith("data:image/") or len(value) > _SIGNATURE_MAX_LEN:
        return None
    return value


@web_bp.route("/settings/toggle-direct-phone", methods=["POST"])
@web_tenant_required
def toggle_direct_phone_public():
    """Quick toggle from the dashboard — show/hide personal phone on public profile."""
    tenant = db.session.get(Tenant, g.tenant_id)
    if not tenant:
        return jsonify({"error": "not found"}), 404
    data = request.get_json(silent=True) or {}
    if "enabled" in data:
        tenant.show_direct_phone_public = bool(data.get("enabled"))
    else:
        tenant.show_direct_phone_public = not bool(tenant.show_direct_phone_public)
    db.session.commit()
    return jsonify({"ok": True, "show_direct_phone_public": tenant.show_direct_phone_public})


@web_bp.route("/settings/stripe-connect", methods=["POST"])
@web_tenant_required
def stripe_connect_start():
    """Start or resume Stripe Connect Express onboarding for card deposits."""
    from app.services import stripe_connect

    tenant = db.session.get(Tenant, g.tenant_id)
    if not tenant:
        return redirect(url_for("web.settings_page"))

    return_url = url_for("web.stripe_connect_return", _external=True)
    refresh_url = url_for("web.stripe_connect_refresh", _external=True)
    try:
        url = stripe_connect.create_onboarding_link(tenant, return_url, refresh_url)
        db.session.commit()
    except Exception:
        logger.exception("Stripe Connect onboarding failed tenant=%s", g.tenant_id)
        return redirect(url_for("web.settings_page", connect="error") + "#paiements")
    return redirect(url, code=303)


@web_bp.route("/settings/stripe-connect/return", methods=["GET"])
@web_tenant_required
def stripe_connect_return():
    from app.services import stripe_connect

    tenant = db.session.get(Tenant, g.tenant_id)
    if tenant:
        stripe_connect.sync_connect_status(tenant)
        db.session.commit()
    status = "success" if tenant and tenant.stripe_connect_ready else "pending"
    return redirect(url_for("web.settings_page", connect=status) + "#paiements")


@web_bp.route("/settings/stripe-connect/refresh", methods=["GET"])
@web_tenant_required
def stripe_connect_refresh():
    """Stripe redirects here when the onboarding link expires — issue a fresh one."""
    return stripe_connect_start()


@web_bp.route("/settings", methods=["GET", "POST"])
@web_tenant_required
def settings_page():
    from app.utils.geocoding import geocode_address

    tenant = db.session.get(Tenant, g.tenant_id)
    user = g.current_user
    success = None
    error = None

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        first_name = (request.form.get("first_name") or "").strip() or None
        last_name = (request.form.get("last_name") or "").strip() or None
        ai_assistant_name = (request.form.get("ai_assistant_name") or "").strip() or None
        siret_raw = (request.form.get("siret") or "").strip()
        phone_number = _normalize_phone(request.form.get("phone_number"))
        address = (request.form.get("address") or "").strip() or None
        postal_code = (request.form.get("postal_code") or "").strip() or None
        city = (request.form.get("city") or "").strip() or None
        service_radius_raw = (request.form.get("service_radius_km") or "").strip()
        trade_type = (request.form.get("trade_type") or "plombier").strip()
        is_public = request.form.get("is_public") == "on"
        show_direct_phone_public = request.form.get("show_direct_phone_public") == "on"
        public_blurb = (request.form.get("public_blurb") or "").strip() or None
        public_slug_raw = (request.form.get("public_slug") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        new_password = request.form.get("new_password") or ""
        confirm_password = request.form.get("confirm_password") or ""
        signature = _normalize_signature(request.form.get("signature"))
        bank_holder = (request.form.get("bank_holder") or "").strip() or None
        # Store the IBAN/BIC uppercased and without spaces for a clean display.
        iban = ((request.form.get("iban") or "").replace(" ", "").upper()) or None
        bic = ((request.form.get("bic") or "").replace(" ", "").upper()) or None

        if not name:
            error = translate("settings.error.company_required")
        elif siret_raw and not _normalize_siret(siret_raw):
            error = translate("settings.error.siret_invalid")
        elif service_radius_raw:
            try:
                radius_val = int(service_radius_raw)
                if radius_val < 5 or radius_val > 200:
                    error = translate("settings.error.radius_invalid")
            except ValueError:
                error = translate("settings.error.radius_invalid")
        else:
            try:
                validate_email(email)
            except AppError:
                error = translate("login.error.invalid_email")

        if not error and email != user.email:
            existing = User.query.filter(User.email == email, User.id != user.id).first()
            if existing:
                error = translate("settings.error.email_taken")

        if not error and is_public and not city:
            error = translate("settings.error.city_required_public")

        if not error and new_password:
            if len(new_password) < 8:
                error = translate("settings.error.password_short")
            elif new_password != confirm_password:
                error = translate("settings.error.password_mismatch")

        if not error:
            from app.constants.trades import TRADES
            from app.services.plan_features import has_feature
            from app.utils.slug import slugify, unique_public_slug

            tenant.name = name
            tenant.first_name = first_name
            tenant.last_name = last_name
            if has_feature(tenant, "ai_customization"):
                tenant.ai_assistant_name = ai_assistant_name
            tenant.trade_type = trade_type if trade_type in TRADES else tenant.trade_type
            tenant.is_public = is_public
            tenant.show_direct_phone_public = show_direct_phone_public
            tenant.public_blurb = public_blurb
            if is_public:
                base_slug = slugify(public_slug_raw) or slugify(name) or "artisan"
                tenant.public_slug = unique_public_slug(base_slug, tenant.id)
            elif public_slug_raw:
                tenant.public_slug = unique_public_slug(slugify(public_slug_raw), tenant.id)
            tenant.siret = _normalize_siret(siret_raw) if siret_raw else None
            tenant.phone_number = phone_number
            # tenant.ai_phone_number is managed by automatic Twilio provisioning
            # (see app.services.twilio_provisioning) — never overwritten from the
            # settings form, which would clobber the dedicated number or persist
            # the shared fallback onto the tenant and break call routing.
            tenant.address = address
            tenant.postal_code = postal_code
            tenant.city = city
            if service_radius_raw:
                tenant.service_radius_km = int(service_radius_raw)
            elif tenant.service_radius_km is None:
                tenant.service_radius_km = 30
            tenant.signature = signature
            tenant.bank_holder = bank_holder
            tenant.iban = iban
            tenant.bic = bic

            full_address = tenant.full_address
            if full_address:
                coords = geocode_address(full_address)
                if coords:
                    tenant.latitude, tenant.longitude = coords
                else:
                    tenant.latitude = None
                    tenant.longitude = None
            else:
                tenant.latitude = None
                tenant.longitude = None

            user.email = email
            password_changed = bool(new_password)
            if new_password:
                user.set_password(new_password)

            db.session.commit()
            success = translate("settings.success")

            if password_changed:
                try:
                    from app.services.transactional_email import send_password_changed

                    send_password_changed(user)
                except Exception:
                    current_app.logger.exception("Password-changed email failed user=%s", user.id)

    from app.constants.trades import trade_choices

    public_profile_url = None
    if tenant.is_public and tenant.public_slug:
        public_profile_url = url_for("web.artisan_profile", slug=tenant.public_slug, _external=True)

    lang = getattr(g, "lang", "fr")
    from app.services import stripe_connect

    if tenant and tenant.stripe_connect_account_id and stripe_connect.connect_available():
        stripe_connect.sync_connect_status(tenant)
        db.session.commit()

    return render_template(
        "artisan/settings.html",
        tenant=tenant,
        user=user,
        success=success,
        error=error,
        trades=trade_choices(lang),
        public_profile_url=public_profile_url,
        stripe_connect_available=stripe_connect.connect_available(),
        stripe_connect_ready=stripe_connect.connect_ready(tenant) if tenant else False,
        connect_status=request.args.get("connect"),
    )


@web_bp.route("/api/route-leg", methods=["GET"])
@web_tenant_required
def route_leg():
    from app.utils.routing import fetch_driving_route

    try:
        from_lat = float(request.args.get("from_lat", ""))
        from_lng = float(request.args.get("from_lng", ""))
        to_lat = float(request.args.get("to_lat", ""))
        to_lng = float(request.args.get("to_lng", ""))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid coordinates"}), 422

    route = fetch_driving_route(from_lat, from_lng, to_lat, to_lng)
    if not route:
        return jsonify({"error": "Route not found"}), 404
    return jsonify(route)
