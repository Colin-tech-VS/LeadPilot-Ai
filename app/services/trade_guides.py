"""Trade guide generation & lookup for pillar SEO pages.

Uses Mistral to write ~800 unique words + a 12-question FAQ + a compact price
grid per (trade × language). Cached in the ``trade_guides`` table with a
90-day freshness window; falls back to ``None`` when Mistral is unreachable so
the pillar page still renders (without the enriched block).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.core.extensions import db
from app.models.trade_guide import TradeGuide

logger = logging.getLogger(__name__)


def get_guide(trade_key: str, lang: str = "fr") -> TradeGuide | None:
    """Return a fresh cached guide for the trade, if any."""
    if not trade_key:
        return None
    return (
        TradeGuide.query.filter_by(trade_key=trade_key.strip().lower(), lang=lang)
        .one_or_none()
    )


def get_or_generate(trade_key: str, lang: str = "fr", *, force: bool = False) -> TradeGuide | None:
    """Return a fresh guide, generating it via Mistral when missing/expired.

    Never raises — returns ``None`` on any error so the caller can render
    the page without the block. ``force=True`` bypasses the freshness check
    and forces a regeneration (admin action).
    """
    guide = get_guide(trade_key, lang)
    if guide and guide.is_fresh() and not force:
        return guide
    try:
        payload = _generate_via_mistral(trade_key, lang)
    except Exception:  # noqa: BLE001 — never break the page render
        logger.exception("Trade guide generation failed for %s/%s", trade_key, lang)
        return guide  # stale-if-error: return the expired one if we have it
    if not payload:
        return guide

    if guide is None:
        guide = TradeGuide(trade_key=trade_key.strip().lower(), lang=lang)
        db.session.add(guide)
    guide.intro_html = payload.get("intro_html") or ""
    guide.body_html = payload.get("body_html") or ""
    guide.price_hints = payload.get("price_hints") or ""
    guide.set_faq(payload.get("faq") or [])
    from app.models.trade_guide import utcnow

    guide.generated_at = utcnow()
    db.session.commit()
    return guide


_SYSTEM_PROMPT = (
    "Tu es un expert SEO français pour un annuaire d'artisans. Tu écris pour "
    "des particuliers qui cherchent un artisan de confiance. Français impeccable, "
    "ton concret, phrases courtes. Zéro promesse marketing creuse. Zéro « bienvenue »."
    " Zéro emoji. Zéro liste à puces dans le body (uniquement dans la FAQ)."
)


def _generate_via_mistral(trade_key: str, lang: str) -> dict[str, Any] | None:
    from app.services import content_ai
    from app.constants.trades import trade_label

    if not content_ai.is_available():
        return None
    label = trade_label(trade_key, lang)

    schema = {
        "intro_html": "1 paragraphe HTML (60-90 mots) qui accroche + situe le métier",
        "body_html": (
            "3 à 5 sections HTML avec <h3> puis <p>. Sujets à couvrir : "
            "1) quand appeler un " + label + " (signes/symptômes concrets), "
            "2) comment reconnaître un pro sérieux (certifications, assurances, devis), "
            "3) fourchette de prix indicative en France (préciser HT/TTC quand pertinent), "
            "4) déroulé type d'une intervention, "
            "5) éviter les arnaques les plus fréquentes. "
            "800 mots au total minimum, unique et concret, sans copie de sites tiers."
        ),
        "price_hints": (
            "Petit tableau HTML <table class='trade-price-grid'> avec 4-6 lignes : "
            "type d'intervention + fourchette de prix TTC en euros. Titre <caption> inclus."
        ),
        "faq": (
            "Liste de 12 objets {question, answer} — questions réelles que les "
            "particuliers tapent sur Google (« combien coûte », « urgence week-end », "
            "« devis gratuit », « intervention nuit », « assurance décennale », etc). "
            "Réponses de 30-60 mots, factuelles, sans promesses marketing."
        ),
    }
    user_prompt = (
        f"Rédige le contenu SEO complet pour la page pillar « {label} » de "
        f"l'annuaire PilotCore. Retourne un JSON valide avec exactement ces "
        f"clés : {list(schema.keys())}. Contraintes de chaque champ :\n"
        + "\n".join(f"- {k}: {v}" for k, v in schema.items())
        + "\nLangue de réponse : " + ("français" if lang == "fr" else "anglais")
        + ". IMPORTANT : rends uniquement le JSON, sans texte autour."
    )
    raw = content_ai._complete(
        _SYSTEM_PROMPT,
        user_prompt,
        json_mode=True,
        max_tokens=3500,
        temperature=0.5,
    )
    data = _safe_json(raw)
    if not isinstance(data, dict):
        return None
    # Basic sanitation: strip <script>, cap sizes, normalise FAQ shape
    data["intro_html"] = _sanitize(data.get("intro_html"), max_len=2000)
    data["body_html"] = _sanitize(data.get("body_html"), max_len=20000)
    data["price_hints"] = _sanitize(data.get("price_hints"), max_len=4000)
    faq_raw = data.get("faq") or []
    if isinstance(faq_raw, dict):
        # Some models occasionally return {"q1": "...", "a1": "..."} — flatten.
        faq_raw = list(faq_raw.values())
    faq: list[dict[str, str]] = []
    for item in faq_raw[:15]:
        if isinstance(item, dict) and item.get("question") and item.get("answer"):
            faq.append(
                {
                    "question": str(item["question"]).strip()[:220],
                    "answer": str(item["answer"]).strip()[:800],
                }
            )
    data["faq"] = faq
    return data


def _safe_json(raw: str) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Try to isolate a JSON object in the response
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None


_SCRIPT_RE = re.compile(r"<(script|style|iframe)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_EVENT_ATTR_RE = re.compile(r"\son[a-z]+\s*=\s*\"[^\"]*\"", re.IGNORECASE)


def _sanitize(html: str | None, *, max_len: int) -> str:
    if not html:
        return ""
    cleaned = _SCRIPT_RE.sub("", str(html))
    cleaned = _EVENT_ATTR_RE.sub("", cleaned)
    return cleaned[:max_len]
