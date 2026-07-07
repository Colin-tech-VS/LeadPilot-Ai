"""Mistral-powered content generation for the admin studio: marketing pages and
social-media posts. Reuses the same ``MISTRAL_API_KEY`` the rest of the app
relies on. Every function degrades gracefully — when the key is missing or the
call fails it raises ``ContentAIError`` with a human message the UI can show.
"""
import json
import logging
import re

from flask import current_app

logger = logging.getLogger(__name__)


class ContentAIError(Exception):
    """Raised when generation is unavailable or fails."""


def is_available() -> bool:
    return bool(current_app.config.get("MISTRAL_API_KEY"))


def _client():
    api_key = current_app.config.get("MISTRAL_API_KEY")
    if not api_key:
        raise ContentAIError("Clé API Mistral absente — renseignez MISTRAL_API_KEY.")
    from mistralai import Mistral

    return Mistral(api_key=api_key), current_app.config.get("MISTRAL_MODEL", "mistral-small-latest")


def _complete(system, user, *, json_mode=False, max_tokens=1500, temperature=0.6):
    client, model = _client()
    kwargs = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    try:
        resp = client.chat.complete(**kwargs)
        return resp.choices[0].message.content
    except Exception as exc:  # noqa: BLE001 - surface a friendly message
        logger.exception("Mistral generation failed")
        raise ContentAIError(f"Génération IA impossible : {exc}") from exc


_PAGE_SYSTEM = (
    "Tu es un rédacteur web et concepteur marketing pour PilotCore, un "
    "standardiste téléphonique IA pour artisans (plombiers, électriciens, etc.). "
    "Tu produis des pages web en français, claires, persuasives et bien "
    "structurées. Réponds UNIQUEMENT en JSON avec les clés: "
    '"title" (titre de la page, court), '
    '"meta_description" (150 caractères max, pour le SEO), '
    '"body_html" (le contenu HTML du corps de la page). '
    "Le body_html ne doit contenir que des balises de contenu "
    "(h1, h2, h3, p, ul, li, a, strong, em, section, blockquote) — "
    "PAS de <html>, <head>, <body>, <style> ni <script>. "
    "Utilise plusieurs sections avec des titres. Sois concret et orienté bénéfices."
)


def generate_page(prompt: str, tone: str = "professionnel") -> dict:
    """Generate a marketing page from a free-text brief. Returns a dict with
    title / meta_description / body_html."""
    user = (
        f"Brief de la page à créer : {prompt.strip()}\n"
        f"Ton souhaité : {tone}.\n"
        "Génère une page complète et prête à publier."
    )
    raw = _complete(_PAGE_SYSTEM, user, json_mode=True, max_tokens=2200, temperature=0.6)
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ContentAIError("La réponse de l'IA n'était pas exploitable.") from exc
    return {
        "title": (data.get("title") or "").strip(),
        "meta_description": (data.get("meta_description") or "").strip()[:300],
        "body_html": _sanitize_html(data.get("body_html") or ""),
    }


_BLOG_SYSTEM = (
    "Tu es rédacteur SEO senior et journaliste spécialisé bâtiment / artisans pour PilotCore "
    "(plateforme française : annuaire artisans + standard téléphonique IA).\n"
    "Tu rédiges des articles de blog en français, ultra optimisés SEO (intention de recherche, "
    "mots-clés naturels, structure H2/H3, listes, FAQ).\n"
    "Réponds UNIQUEMENT en JSON avec les clés :\n"
    '- "title" : titre H1 accrocheur (60 caractères max),\n'
    '- "meta_description" : 140-155 caractères, incitatif, avec mot-clé principal,\n'
    '- "meta_keywords" : 8 à 12 mots-clés séparés par des virgules,\n'
    '- "excerpt" : chapô 2 phrases (max 280 caractères),\n'
    '- "reading_time_min" : entier (minutes de lecture estimées),\n'
    '- "body_html" : article complet (1500-2200 mots) — balises h2, h3, p, ul, li, strong, em, '
    "blockquote, section uniquement. PAS de h1 (ajouté par le template). "
    "Inclure une intro, 4-6 sections, conclusion avec CTA doux vers PilotCore (sans URL brute).\n"
    '- "faq" : tableau de 3 à 5 objets {"question": "...", "answer": "..."} pour rich snippets.'
)


def generate_blog_post(prompt: str, tone: str = "expert", *, category_hint: str = "") -> dict:
    """Generate a full SEO blog article. Returns fields for the admin editor."""
    user = (
        f"Sujet de l'article : {prompt.strip()}\n"
        f"Ton : {tone}.\n"
        f"Catégorie suggérée : {category_hint or 'au choix'}.\n"
        "Rédige l'article de blog complet."
    )
    raw = _complete(_BLOG_SYSTEM, user, json_mode=True, max_tokens=4500, temperature=0.58)
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ContentAIError("La réponse de l'IA n'était pas exploitable.") from exc

    faq_raw = data.get("faq") or []
    faq = []
    if isinstance(faq_raw, list):
        for item in faq_raw:
            if isinstance(item, dict) and item.get("question"):
                faq.append(
                    {
                        "question": str(item.get("question", "")).strip(),
                        "answer": str(item.get("answer", "")).strip(),
                    }
                )

    try:
        reading = int(data.get("reading_time_min") or 5)
    except (TypeError, ValueError):
        reading = 5

    return {
        "title": (data.get("title") or "").strip(),
        "meta_description": (data.get("meta_description") or "").strip()[:300],
        "meta_keywords": (data.get("meta_keywords") or "").strip()[:400],
        "excerpt": (data.get("excerpt") or "").strip()[:400],
        "reading_time_min": max(2, min(reading, 30)),
        "body_html": _sanitize_html(data.get("body_html") or ""),
        "faq": faq,
    }


_SOCIAL_BRAND = (
    "Charte PilotCore (direction artistique du site) :\n"
    "- Couleurs : bleu #1B57E0, cyan #06B6D4, vert #10B981, fond clair moderne.\n"
    "- Ton : humain, rassurant, pro mais accessible — jamais corporate froid.\n"
    "- Vocabulaire site : artisan, RDV en ligne, assistant vocal IA, dépannage, "
    "standard téléphonique, ne ratez plus aucun appel, essai gratuit 14 jours.\n"
    "- Structure : accroche courte → bénéfice concret → preuve/confiance → CTA doux.\n"
    "- 2 à 4 emojis max, pertinents (🔧 📞 ✅ 🛠️), pas de spam.\n"
    "- Hashtags en fin de post : #PilotCore + 2 à 4 hashtags métier (#Plombier #Artisan…).\n"
    "- N'inclus JAMAIS d'URL brute dans le texte (le lien est ajouté séparément par la plateforme).\n"
    "- Termine par un appel à l'action aligné sur la page cible (sans écrire l'URL)."
)

_SOCIAL_SYSTEM = (
    "Tu es community manager senior pour PilotCore, plateforme française qui met en "
    "relation particuliers et artisans (RDV en ligne) et propose un standard téléphonique "
    "IA aux professionnels du bâtiment.\n"
    f"{_SOCIAL_BRAND}\n"
    "Réponds UNIQUEMENT en JSON avec :\n"
    '- "message" : texte du post Facebook (sans guillemets),\n'
    '- "image_headline" : accroche visuelle courte en français (6 mots max),\n'
    '- "visual_brief" : description du visuel SANS texte (icônes, artisan, téléphone…).'
)


def _parse_json_response(raw: str) -> dict:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def generate_social_post(
    prompt: str,
    tone: str = "engageant",
    *,
    target_key: str = "home",
    content_tag: str = "ai_post",
) -> dict:
    """Generate a Facebook post + image brief in one IA call (fast, single round-trip)."""
    from app.services.social_links import build_tracked_url_for_target, display_url, get_target

    target = get_target(target_key) or get_target("home")
    tracked = build_tracked_url_for_target(target["key"], content=content_tag) if target else None
    user = (
        f"Sujet du post : {prompt.strip()}\n"
        f"Ton : {tone}.\n"
        f"Page cible : {target['label']} — {target['audience']}.\n"
        f"CTA suggéré (sans URL) : {target['cta']}.\n"
        "Rédige le post et le brief visuel."
    )
    raw = _complete(_SOCIAL_SYSTEM, user, json_mode=True, max_tokens=700, temperature=0.72)
    try:
        data = _parse_json_response(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ContentAIError("La réponse de l'IA n'était pas exploitable.") from exc
    message = (data.get("message") or "").strip().strip('"')
    if not message:
        raise ContentAIError("L'IA n'a pas généré de texte de post.")
    headline = (data.get("image_headline") or prompt.strip()[:40] or "PilotCore").strip()
    visual = (data.get("visual_brief") or prompt.strip()).strip()
    return {
        "message": message,
        "link": tracked,
        "display_link": display_url(tracked) if tracked else "",
        "target_key": target["key"],
        "image_headline": headline[:80],
        "visual_brief": visual[:500],
    }


# Very small allow-list scrub: strip script/style/iframe blocks that the model
# should never emit anyway. Content is authored by the trusted admin, so this is
# defence-in-depth rather than untrusted-input sanitisation.
_FORBIDDEN = re.compile(r"<\s*(script|style|iframe|object|embed)\b.*?<\s*/\s*\1\s*>",
                        re.IGNORECASE | re.DOTALL)
_FORBIDDEN_SELF = re.compile(r"<\s*(script|style|iframe|object|embed)\b[^>]*/?>",
                             re.IGNORECASE)


def _sanitize_html(html: str) -> str:
    html = _FORBIDDEN.sub("", html or "")
    html = _FORBIDDEN_SELF.sub("", html)
    return html.strip()
