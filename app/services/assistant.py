"""Nova — the admin copilot.

Nova is an AI agent embedded in the PilotCore admin console. She knows the whole
site (KPIs, content inventory, integrations, recent activity), analyses it,
proposes what to do next, and — through function calling — can actually *do* the
work: draft/publish blog articles and pages, generate & publish social posts,
search for B2B prospects, write and send outreach e-mails, and send transactional
e-mails.

Design notes
------------
* Single dependency: the same ``MISTRAL_API_KEY`` used everywhere else. When it
  is missing, :func:`available` is ``False`` and the UI degrades gracefully.
* Every tool the model can call maps 1:1 to an existing admin service, so Nova
  never bypasses business rules (slug uniqueness, deliverability checks…).
* Every action Nova performs is written to the event journal (``nova_action``)
  and returned to the UI, so there is always an audit trail.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone

from flask import current_app

from app.core.extensions import db
from app.services import content_ai
from app.services.events import CAT_ADMIN, LEVEL_INFO, LEVEL_SUCCESS, log_event

logger = logging.getLogger(__name__)

ASSISTANT_NAME = "Nova"
MAX_TOOL_ROUNDS = 6


class AssistantError(Exception):
    """Raised when Nova cannot run (no API key, transport failure…)."""


def available() -> bool:
    return bool(current_app.config.get("MISTRAL_API_KEY"))


# --------------------------------------------------------------------------- #
# Site knowledge — what Nova can "see" about the whole platform.
# --------------------------------------------------------------------------- #
def site_snapshot() -> dict:
    """A compact, JSON-serialisable picture of the entire site for Nova."""
    from app.models.blog_post import BlogPost
    from app.models.offer import Offer
    from app.models.site_page import SitePage
    from app.models.social_post import SocialPost
    from app.services import admin_email, analytics, imap_mailbox, social

    def _safe(fn, default=None):
        try:
            return fn()
        except Exception:  # noqa: BLE001 — a snapshot must never 500
            logger.debug("site_snapshot piece failed", exc_info=True)
            return default

    kpis = _safe(lambda: analytics.kpis(30), {}) or {}

    prospect_stats = {}
    try:
        from app.services import prospecting

        prospect_stats = prospecting.prospect_stats()
    except Exception:  # noqa: BLE001
        logger.debug("prospect stats snapshot failed", exc_info=True)

    content = {
        "pages_total": _safe(lambda: SitePage.query.count(), 0),
        "pages_published": _safe(
            lambda: SitePage.query.filter(SitePage.status == "published").count(), 0
        ),
        "blog_total": _safe(lambda: BlogPost.query.count(), 0),
        "blog_published": _safe(
            lambda: BlogPost.query.filter(BlogPost.status == "published").count(), 0
        ),
        "social_total": _safe(lambda: SocialPost.query.count(), 0),
        "offers": _safe(lambda: Offer.query.count(), 0),
    }

    integrations = {
        "mistral_ai": bool(current_app.config.get("MISTRAL_API_KEY")),
        "smtp_email": _safe(admin_email.is_configured, False),
        "imap_inbox": _safe(imap_mailbox.is_configured, False),
        "facebook": _safe(social.is_configured, False),
    }
    try:
        from app.services import google_gsc

        integrations["google_search_console"] = google_gsc.is_connected()
    except Exception:  # noqa: BLE001
        integrations["google_search_console"] = False

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "kpis_30d": kpis,
        "content": content,
        "prospecting": prospect_stats,
        "integrations": integrations,
    }


def _recent_titles(limit: int = 8) -> dict:
    from app.models.blog_post import BlogPost
    from app.models.site_page import SitePage

    try:
        blog = [
            {"title": p.title, "status": p.status, "slug": p.slug}
            for p in BlogPost.query.order_by(BlogPost.updated_at.desc()).limit(limit).all()
        ]
    except Exception:  # noqa: BLE001
        blog = []
    try:
        pages = [
            {"title": p.title, "status": p.status, "slug": p.slug}
            for p in SitePage.query.order_by(SitePage.updated_at.desc()).limit(limit).all()
        ]
    except Exception:  # noqa: BLE001
        pages = []
    return {"blog": blog, "pages": pages}


# --------------------------------------------------------------------------- #
# Slug helpers (mirror app.routes.admin, kept local to avoid a circular import)
# --------------------------------------------------------------------------- #
def _slugify(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9\s-]", "", value)
    value = re.sub(r"[\s-]+", "-", value).strip("-")
    return value or "page"


def _unique_slug(model, base: str) -> str:
    slug, i = base, 2
    with db.session.no_autoflush:
        while model.query.filter(model.slug == slug).first() is not None:
            slug = f"{base}-{i}"
            i += 1
    return slug


# --------------------------------------------------------------------------- #
# Tools — each returns a JSON-serialisable dict. ``_ACTIONS`` collects a
# human-facing summary of everything Nova actually changed during a turn.
# --------------------------------------------------------------------------- #
def _base_url() -> str:
    return str(current_app.config.get("PUBLIC_BASE_URL") or "https://www.pilotcore.fr").rstrip("/")


def tool_get_site_overview(_actions) -> dict:
    snap = site_snapshot()
    snap["recent_content"] = _recent_titles()
    return snap


def tool_create_blog_article(actions, *, topic, tone="expert", category_hint="", publish=False):
    from app.models.blog_post import BlogPost
    from app.services import blog as blog_svc

    if not topic or not str(topic).strip():
        return {"ok": False, "error": "Sujet de l'article manquant."}
    result = content_ai.generate_blog_post(str(topic), tone or "expert", category_hint=category_hint or "")
    post = BlogPost()
    post.title = result["title"] or str(topic)[:200]
    post.slug = _unique_slug(BlogPost, _slugify(post.title))
    post.excerpt = (result.get("excerpt") or "")[:400] or None
    post.meta_description = (result.get("meta_description") or "")[:300] or None
    post.meta_keywords = (result.get("meta_keywords") or "")[:400] or None
    post.body_html = result.get("body_html") or ""
    post.reading_time_min = result.get("reading_time_min")
    if result.get("faq"):
        post.set_faq(result["faq"])
    post.status = "published" if publish else "draft"
    db.session.add(post)
    if publish:
        blog_svc.touch_published_at(post, publishing=True)
    db.session.commit()
    log_event(
        CAT_ADMIN, "nova_action",
        summary=f"Nova a {'publié' if publish else 'rédigé'} l'article « {post.title} »",
        level=LEVEL_SUCCESS, actor=ASSISTANT_NAME,
    )
    actions.append({
        "type": "blog", "label": f"Article {'publié' if publish else 'brouillon'} : {post.title}",
        "url": f"/admin/blog/{post.id}", "status": post.status,
    })
    return {"ok": True, "id": str(post.id), "title": post.title, "slug": post.slug,
            "status": post.status, "edit_url": f"/admin/blog/{post.id}"}


def tool_create_page(actions, *, brief, tone="professionnel", publish=False):
    from app.models.site_page import SitePage

    if not brief or not str(brief).strip():
        return {"ok": False, "error": "Brief de la page manquant."}
    result = content_ai.generate_page(str(brief), tone or "professionnel")
    page = SitePage()
    page.title = result["title"] or str(brief)[:200]
    page.slug = _unique_slug(SitePage, _slugify(page.title))
    page.meta_description = (result.get("meta_description") or "")[:300]
    page.body_html = result.get("body_html") or ""
    page.status = "published" if publish else "draft"
    db.session.add(page)
    db.session.commit()
    log_event(
        CAT_ADMIN, "nova_action",
        summary=f"Nova a {'publié' if publish else 'créé'} la page « {page.title} »",
        level=LEVEL_SUCCESS, actor=ASSISTANT_NAME,
    )
    actions.append({
        "type": "page", "label": f"Page {'publiée' if publish else 'brouillon'} : {page.title}",
        "url": f"/admin/pages/{page.id}", "status": page.status,
    })
    return {"ok": True, "id": str(page.id), "title": page.title, "slug": page.slug,
            "status": page.status, "edit_url": f"/admin/pages/{page.id}",
            "public_url": f"/p/{page.slug}" if publish else None}


def tool_create_social_post(actions, *, topic, tone="engageant", target_key="home", publish=False):
    from app.services import social

    if not topic or not str(topic).strip():
        return {"ok": False, "error": "Sujet du post manquant."}
    payload = content_ai.generate_social_post(str(topic), tone or "engageant", target_key=target_key or "home")
    message = payload.get("message", "")
    if not publish:
        actions.append({"type": "social_draft", "label": "Post social rédigé (brouillon)", "url": "/admin/social"})
        return {"ok": True, "published": False, "message": message,
                "note": "Brouillon prêt. Rappelle-moi avec publish=true pour le publier sur Facebook."}
    if not social.is_configured():
        return {"ok": False, "error": "Facebook n'est pas connecté — impossible de publier."}
    try:
        from app.services import social_image

        image = social_image.generate_for_post(
            str(topic), tone or "engageant",
            headline=payload.get("image_headline"), visual_brief=payload.get("visual_brief"),
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"Visuel impossible : {exc}"}
    post = social.publish_post(message, link=payload.get("link"), generated_by_ai=True,
                               image_path=image.get("image_path"))
    ok = post.status == "published"
    log_event(
        CAT_ADMIN, "nova_action",
        summary=f"Nova a publié un post Facebook ({post.status})",
        level=LEVEL_SUCCESS if ok else LEVEL_INFO, actor=ASSISTANT_NAME,
    )
    actions.append({"type": "social", "label": "Post Facebook publié" if ok else "Post Facebook: échec",
                    "url": "/admin/social", "status": post.status})
    return {"ok": ok, "published": ok, "status": post.status, "error": getattr(post, "error", None)}


def tool_search_prospects(actions, *, trade_type="plombier", city="", max_results=10):
    from app.services import prospecting

    if not city or not str(city).strip():
        return {"ok": False, "error": "Ville ou code postal requis pour la recherche."}
    result = prospecting.run_search(trade_type=trade_type or "plombier", city=str(city),
                                    max_results=int(max_results or 10))
    log_event(
        CAT_ADMIN, "nova_action",
        summary=f"Nova a cherché des prospects {trade_type} · {city} — {result['found']} trouvé(s)",
        level=LEVEL_SUCCESS, actor=ASSISTANT_NAME,
    )
    actions.append({"type": "prospecting", "label": f"{result['found']} prospect(s) trouvé(s) à {city}",
                    "url": "/admin/prospecting"})
    return {"ok": True, "found": result["found"], "with_email": result["with_email"],
            "prospects": [
                {"id": p.get("id"), "company": p.get("company_name"), "email": p.get("email"),
                 "status": p.get("status")}
                for p in result.get("prospects", [])[:15]
            ]}


def tool_list_prospects(_actions, *, status=None, trade_type=None, limit=20):
    from app.services import prospecting

    rows = prospecting.list_prospects(status=status or None, trade_type=trade_type or None,
                                      limit=int(limit or 20))
    return {"ok": True, "count": len(rows), "prospects": [
        {"id": str(p.id), "company": p.company_name, "email": p.email, "city": p.city,
         "trade": p.trade_type, "status": p.status,
         "has_email_draft": bool(p.outreach_subject and p.outreach_body)}
        for p in rows
    ]}


def tool_prepare_outreach_email(actions, *, prospect_id, tone="professionnel", angle=""):
    from app.services import prospecting

    prospect = prospecting.generate_outreach_email(prospect_id, tone=tone or "professionnel", angle=angle or "")
    log_event(CAT_ADMIN, "nova_action",
              summary=f"Nova a rédigé l'e-mail de prospection pour {prospect.email or prospect.id}",
              level=LEVEL_SUCCESS, actor=ASSISTANT_NAME)
    actions.append({"type": "prospecting", "label": f"E-mail rédigé pour {prospect.email or 'prospect'}",
                    "url": "/admin/prospecting"})
    return {"ok": True, "prospect_id": str(prospect.id), "subject": prospect.outreach_subject,
            "body": prospect.outreach_body}


def tool_send_outreach_email(actions, *, prospect_id):
    from app.services import prospecting

    result = prospecting.send_outreach_email(prospect_id)
    status = result.get("email_status")
    ok = status not in ("failed", None)
    log_event(CAT_ADMIN, "nova_action",
              summary=f"Nova a envoyé un e-mail de prospection ({status})",
              level=LEVEL_SUCCESS if ok else LEVEL_INFO, actor=ASSISTANT_NAME)
    actions.append({"type": "email", "label": f"E-mail prospection envoyé ({status})", "url": "/admin/prospecting"})
    return {"ok": ok, "email_status": status, "error": result.get("email_error")}


def tool_send_email(actions, *, to, subject, body):
    from app.services import admin_email

    if not to or not subject:
        return {"ok": False, "error": "Destinataire et objet obligatoires."}
    row = admin_email.send_email(str(to).strip(), str(subject).strip(), body or "")
    ok = row.status in ("sent", "simulated")
    log_event(CAT_ADMIN, "nova_action", summary=f"Nova a envoyé un e-mail à {to} ({row.status})",
              level=LEVEL_INFO, actor=ASSISTANT_NAME)
    actions.append({"type": "email", "label": f"E-mail envoyé à {to} ({row.status})", "url": "/admin/emails"})
    return {"ok": ok, "status": row.status, "error": row.error}


def tool_recent_activity(_actions, *, limit=15):
    from app.models.event import Event

    events = Event.query.order_by(Event.created_at.desc()).limit(int(limit or 15)).all()
    return {"ok": True, "events": [
        {"category": e.category, "action": e.action, "level": e.level, "summary": e.summary,
         "at": e.created_at.isoformat() if e.created_at else None}
        for e in events
    ]}


_SUGGESTION_STYLES = {"primary", "accent", "ghost", "danger"}


def tool_propose_actions(ctx, *, actions=None):
    """Surface clickable next-step buttons in the chat.

    Each item is ``{label, prompt, style}``. Clicking a button in the UI sends
    ``prompt`` back to Nova, so the operator acts in a single click. This does
    **not** perform anything itself — it only proposes.
    """
    items = []
    for raw in (actions or [])[:4]:
        if not isinstance(raw, dict):
            continue
        label = str(raw.get("label") or "").strip()[:64]
        if not label:
            continue
        prompt = str(raw.get("prompt") or raw.get("label") or "").strip()[:400]
        style = str(raw.get("style") or "primary").strip().lower()
        if style not in _SUGGESTION_STYLES:
            style = "primary"
        items.append({"label": label, "prompt": prompt, "style": style})
    ctx["suggestions"].extend(items)
    return {"ok": True, "count": len(items)}


_TOOL_IMPL = {
    "get_site_overview": tool_get_site_overview,
    "create_blog_article": tool_create_blog_article,
    "create_page": tool_create_page,
    "create_social_post": tool_create_social_post,
    "search_prospects": tool_search_prospects,
    "list_prospects": tool_list_prospects,
    "prepare_outreach_email": tool_prepare_outreach_email,
    "send_outreach_email": tool_send_outreach_email,
    "send_email": tool_send_email,
    "recent_activity": tool_recent_activity,
    # propose_actions is handled specially in _run_tool (needs the full ctx).
}


def _tool_schemas() -> list[dict]:
    def fn(name, description, properties, required=None):
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required or [],
                },
            },
        }

    return [
        fn("get_site_overview",
           "Récupère un instantané complet du site : KPIs 30 jours, inventaire de contenu, "
           "statut des intégrations, stats de prospection et derniers contenus. À appeler pour analyser.",
           {}),
        fn("create_blog_article",
           "Génère (IA) puis enregistre un article de blog SEO. publish=false → brouillon (défaut, sûr).",
           {"topic": {"type": "string", "description": "Sujet / brief de l'article."},
            "tone": {"type": "string"},
            "category_hint": {"type": "string"},
            "publish": {"type": "boolean", "description": "true pour publier directement."}},
           ["topic"]),
        fn("create_page",
           "Génère (IA) puis enregistre une page marketing. publish=false → brouillon (défaut).",
           {"brief": {"type": "string"}, "tone": {"type": "string"},
            "publish": {"type": "boolean"}},
           ["brief"]),
        fn("create_social_post",
           "Rédige un post réseaux sociaux. publish=true publie sur Facebook (si connecté), sinon renvoie le brouillon.",
           {"topic": {"type": "string"}, "tone": {"type": "string"},
            "target_key": {"type": "string", "description": "Page cible du lien (ex: home)."},
            "publish": {"type": "boolean"}},
           ["topic"]),
        fn("search_prospects",
           "Recherche web des artisans (B2B) et enregistre les prospects trouvés.",
           {"trade_type": {"type": "string", "description": "Métier (ex: plombier, electricien)."},
            "city": {"type": "string"}, "max_results": {"type": "integer"}},
           ["city"]),
        fn("list_prospects",
           "Liste les prospects B2B enregistrés (filtrable par statut / métier).",
           {"status": {"type": "string"}, "trade_type": {"type": "string"}, "limit": {"type": "integer"}}),
        fn("prepare_outreach_email",
           "Rédige (IA) l'e-mail de prospection personnalisé d'un prospect. Fournir son id.",
           {"prospect_id": {"type": "string"}, "tone": {"type": "string"}, "angle": {"type": "string"}},
           ["prospect_id"]),
        fn("send_outreach_email",
           "Envoie l'e-mail de prospection déjà rédigé d'un prospect. Action externe : ne l'utilise que si demandé.",
           {"prospect_id": {"type": "string"}},
           ["prospect_id"]),
        fn("send_email",
           "Envoie un e-mail transactionnel simple. Action externe : ne l'utilise que si l'utilisateur le demande.",
           {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}},
           ["to", "subject", "body"]),
        fn("recent_activity",
           "Récupère les derniers évènements du journal (connexions, envois, erreurs…).",
           {"limit": {"type": "integer"}}),
        fn("propose_actions",
           "Affiche 1 à 4 boutons d'action cliquables sous ta réponse pour que l'admin agisse en un clic. "
           "N'exécute rien : chaque bouton renvoie son 'prompt' à toi quand on clique. "
           "Utilise-le à chaque fois que tu proposes des prochaines étapes concrètes.",
           {"actions": {
               "type": "array",
               "description": "Liste de boutons.",
               "items": {
                   "type": "object",
                   "properties": {
                       "label": {"type": "string", "description": "Texte court du bouton (avec emoji)."},
                       "prompt": {"type": "string",
                                  "description": "Instruction renvoyée à Nova au clic (ex: « Rédige l'article … »)."},
                       "style": {"type": "string", "enum": ["primary", "accent", "ghost", "danger"]},
                   },
                   "required": ["label", "prompt"],
               },
           }},
           ["actions"]),
    ]


def _system_prompt() -> str:
    snap = site_snapshot()
    titles = _recent_titles(6)
    return (
        f"Tu es {ASSISTANT_NAME}, le copilote IA de la console d'administration de PilotCore "
        "(plateforme française : annuaire d'artisans + standard téléphonique IA + prospection B2B).\n"
        "Tu connais TOUT le site et tu peux AGIR grâce à tes outils : créer/publier des articles de blog "
        "et des pages, rédiger et publier des posts sociaux, chercher des prospects, rédiger et envoyer des "
        "e-mails de prospection.\n\n"
        "MÉTHODE :\n"
        "1. Pour analyser, commence par appeler get_site_overview.\n"
        "2. Propose des actions concrètes et priorisées (créer telle page, écrire tel article, contacter tels prospects).\n"
        "3. Quand l'utilisateur te demande d'agir, utilise directement l'outil approprié — n'invente jamais de résultat.\n"
        "4. Créations de contenu : publie seulement si on te le demande explicitement ; sinon laisse en brouillon.\n"
        "5. Actions externes (envoi d'e-mails, publication) : confirme l'intention avant si l'utilisateur reste vague.\n"
        "6. Réponds en français, de façon concise et orientée action.\n\n"
        "MISE EN FORME (importante) :\n"
        "• Structure tes réponses en Markdown : **gras** pour les points clés, titres « ### », listes à puces "
        "ou numérotées courtes, `code` pour un slug/nom technique, [texte](url) pour un lien.\n"
        "• Utilise des emojis pertinents et sobres (🚀 📈 📝 🎯 ✉️ ✅ ⚠️) pour rythmer, sans excès.\n"
        "• Chaque fois que tu proposes des prochaines étapes, appelle l'outil propose_actions pour afficher des "
        "boutons cliquables (1 à 4). Rédige des labels courts avec emoji et un 'prompt' clair qui te dit quoi faire "
        "au clic. Mets 'style':'accent' pour l'action recommandée, 'ghost' pour une option secondaire.\n\n"
        f"CONTEXTE ACTUEL (JSON) : {json.dumps(snap, ensure_ascii=False)}\n"
        f"DERNIERS CONTENUS : {json.dumps(titles, ensure_ascii=False)}"
    )


# --------------------------------------------------------------------------- #
# Chat loop (function calling)
# --------------------------------------------------------------------------- #
def _client():
    api_key = current_app.config.get("MISTRAL_API_KEY")
    if not api_key:
        raise AssistantError("Clé API Mistral absente — renseignez MISTRAL_API_KEY.")
    from mistralai import Mistral

    return Mistral(api_key=api_key), current_app.config.get("MISTRAL_MODEL", "mistral-small-latest")


def _run_tool(name, arguments, ctx) -> dict:
    kwargs = arguments if isinstance(arguments, dict) else {}
    if name == "propose_actions":
        try:
            return tool_propose_actions(ctx, **kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Nova propose_actions failed")
            return {"ok": False, "error": str(exc)[:300]}
    impl = _TOOL_IMPL.get(name)
    if impl is None:
        return {"ok": False, "error": f"Outil inconnu : {name}"}
    try:
        return impl(ctx["actions"], **kwargs)
    except content_ai.ContentAIError as exc:
        return {"ok": False, "error": f"Génération IA impossible : {exc}"}
    except Exception as exc:  # noqa: BLE001 — a broken tool must not kill the chat
        db.session.rollback()
        logger.exception("Nova tool %s failed", name)
        return {"ok": False, "error": str(exc)[:300]}


def chat(user_message: str, history: list[dict] | None = None) -> dict:
    """Run one Nova turn. ``history`` is a list of {role, content} (user/assistant).

    Returns ``{"reply": str, "actions": [...]}``.
    """
    if not available():
        raise AssistantError("Nova est indisponible — configurez MISTRAL_API_KEY.")
    client, model = _client()

    messages: list[dict] = [{"role": "system", "content": _system_prompt()}]
    for turn in (history or [])[-8:]:
        role = turn.get("role")
        content = (turn.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_message.strip()})

    tools = _tool_schemas()
    actions: list[dict] = []
    suggestions: list[dict] = []
    ctx = {"actions": actions, "suggestions": suggestions}

    for _round in range(MAX_TOOL_ROUNDS):
        try:
            resp = client.chat.complete(
                model=model, messages=messages, tools=tools, tool_choice="auto",
                temperature=0.35, max_tokens=1400,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Nova completion failed")
            raise AssistantError(f"Nova a rencontré une erreur : {exc}") from exc

        msg = resp.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None) or []
        content = msg.content or ""

        if not tool_calls:
            return {"reply": content.strip() or "…", "actions": actions,
                    "suggestions": _dedupe_suggestions(suggestions)}

        messages.append({
            "role": "assistant",
            "content": content,
            "tool_calls": [
                {"id": getattr(tc, "id", None) or uuid.uuid4().hex[:12], "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in tool_calls
            ],
        })

        for tc in tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except (json.JSONDecodeError, TypeError):
                args = {}
            result = _run_tool(name, args, ctx)
            messages.append({
                "role": "tool",
                "tool_call_id": getattr(tc, "id", None) or "",
                "name": name,
                "content": json.dumps(result, ensure_ascii=False)[:6000],
            })

    # Ran out of rounds — ask the model for a final wrap-up without tools.
    try:
        resp = client.chat.complete(model=model, messages=messages, temperature=0.35, max_tokens=800)
        final = resp.choices[0].message.content or ""
    except Exception:  # noqa: BLE001
        final = "J'ai réalisé plusieurs actions — consulte le récapitulatif ci-dessous."
    return {"reply": final.strip() or "Actions réalisées.", "actions": actions,
            "suggestions": _dedupe_suggestions(suggestions)}


def _dedupe_suggestions(suggestions: list[dict]) -> list[dict]:
    """Keep at most 4 unique proposed-action buttons (by label)."""
    seen, out = set(), []
    for s in suggestions:
        key = s.get("label", "").lower()
        if key and key not in seen:
            seen.add(key)
            out.append(s)
        if len(out) >= 4:
            break
    return out


# --------------------------------------------------------------------------- #
# Proactive insights — a one-shot recommender for the dashboard card.
# --------------------------------------------------------------------------- #
_INSIGHTS_SYSTEM = (
    f"Tu es {ASSISTANT_NAME}, copilote IA de PilotCore. On te donne un instantané JSON du site. "
    "Analyse-le et renvoie UNIQUEMENT un JSON : "
    '{"headline": "synthèse en une phrase", '
    '"insights": [{"title": "...", "detail": "recommandation actionnable (1-2 phrases)", '
    '"action": "create_blog|create_page|social|prospecting|email|seo|none", "priority": "high|medium|low"}]}. '
    "3 à 5 recommandations concrètes, priorisées, orientées croissance (SEO, contenu, prospection, conversion). "
    "En français."
)


def insights() -> dict:
    if not available():
        return {"available": False, "headline": "", "insights": []}
    snap = site_snapshot()
    snap["recent_content"] = _recent_titles(6)
    try:
        raw = content_ai._complete(
            _INSIGHTS_SYSTEM,
            json.dumps(snap, ensure_ascii=False),
            json_mode=True, max_tokens=1100, temperature=0.5,
        )
        data = content_ai._loads_lenient(raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Nova insights failed: %s", exc)
        return {"available": True, "headline": "", "insights": [], "error": str(exc)[:200]}

    items = []
    for it in (data.get("insights") or [])[:5]:
        if not isinstance(it, dict):
            continue
        items.append({
            "title": str(it.get("title") or "").strip()[:120],
            "detail": str(it.get("detail") or "").strip()[:400],
            "action": str(it.get("action") or "none").strip(),
            "priority": str(it.get("priority") or "medium").strip(),
        })
    return {"available": True, "headline": str(data.get("headline") or "").strip()[:200],
            "insights": items, "generated_at": snap["generated_at"]}
