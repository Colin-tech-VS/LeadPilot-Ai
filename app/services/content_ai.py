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
    "Tu es un rédacteur web et concepteur marketing pour LeadPilot AI, un "
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


_SOCIAL_SYSTEM = (
    "Tu es community manager pour LeadPilot AI (standardiste téléphonique IA "
    "pour artisans). Tu écris des posts Facebook en français : accrocheurs, "
    "authentiques, avec un appel à l'action et 2 à 4 emojis pertinents. "
    "Longueur idéale : 3 à 6 phrases. Termine par quelques hashtags pertinents. "
    "Réponds uniquement avec le texte du post, sans guillemets ni préambule."
)


def generate_social_post(prompt: str, tone: str = "engageant") -> str:
    """Generate a Facebook post from a short brief. Returns plain text."""
    user = (
        f"Sujet du post : {prompt.strip()}\n"
        f"Ton : {tone}.\n"
        "Rédige le post."
    )
    text = _complete(_SOCIAL_SYSTEM, user, json_mode=False, max_tokens=500, temperature=0.75)
    return (text or "").strip().strip('"')


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
