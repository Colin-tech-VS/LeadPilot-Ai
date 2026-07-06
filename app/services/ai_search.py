"""Natural-language ("AI") search for the public artisan directory.

A visitor can type a plain sentence like « j'ai une fuite d'eau sous mon évier à
Lyon » and get matching artisans. We turn that sentence into structured filters
(trade + city) and reuse the normal directory query.

Design mirrors :mod:`app.services.lead_extractor`: use Mistral when a key is
configured, otherwise fall back to a keyword/synonym parser so the feature works
everywhere (and in tests) without an API key. Never raises — a parsing failure
degrades to a plain full-text search on the raw query.
"""

import json
import logging
import os
import re

from flask import current_app

from app.constants.trades import TRADES
from app.services.artisan_directory import search_public_artisans

logger = logging.getLogger(__name__)

# Synonyms / problem words mapped to a trade key. Kept intentionally broad so a
# customer describing a *problem* (not a job title) still resolves to a trade.
TRADE_SYNONYMS = {
    "plombier": [
        "plomb", "fuite", "fuite d'eau", "évier", "evier", "robinet", "canalisation",
        "tuyau", "wc", "toilette", "chasse d'eau", "chauffe-eau", "ballon d'eau",
        "bouché", "bouche", "dégorgement", "sanitaire", "lavabo", "douche", "siphon",
    ],
    "serrurier": [
        "serrure", "clé", "cle", "porte claquée", "porte fermée", "verrou", "cylindre",
        "enfermé dehors", "cambriolage", "blindée", "clef",
    ],
    "electricien": [
        "électr", "electr", "courant", "disjoncteur", "tableau électrique", "prise",
        "court-circuit", "panne de courant", "compteur", "câblage", "cablage", "lumière",
    ],
    "chauffagiste": [
        "chauffage", "chaudière", "chaudiere", "radiateur", "chauffe", "gaz",
        "pompe à chaleur", "pac", "plancher chauffant",
    ],
    "climaticien": [
        "clim", "climatisation", "climatiseur", "air conditionné", "ventilation", "vmc",
        "rafraîchiss", "froid",
    ],
    "vitrier": [
        "vitre", "vitrage", "fenêtre cassée", "fenetre cassee", "double vitrage",
        "miroir", "verre", "baie vitrée",
    ],
    "menuisier": [
        "menuis", "porte", "placard", "meuble sur mesure", "parquet", "escalier",
        "volet", "bois",
    ],
    "peintre": ["peintre", "peinture", "repeindre", "enduit", "papier peint", "mur"],
    "macon": ["maçon", "macon", "maçonnerie", "mur porteur", "dalle", "béton", "beton", "fondation"],
    "couvreur": ["couvreur", "toiture", "toit", "tuile", "zinguerie", "gouttière", "gouttiere"],
    "carreleur": ["carrel", "carrelage", "faïence", "faience", "joint", "sol"],
    "charpentier": ["charpent", "poutre", "ossature bois", "combles"],
    "paysagiste": ["paysag", "jardin", "élagage", "elagage", "tonte", "haie", "gazon", "terrasse bois"],
}

_POSTAL_RE = re.compile(r"\b(\d{5})\b")
# Capture a locality after a location preposition: "à Lyon", "sur Paris 15", "vers Bordeaux".
_CITY_PREP_RE = re.compile(
    r"\b(?:à|a|au|aux|sur|vers|dans|proche de|près de|pres de|autour de|region|région)\s+"
    r"([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'’\-]+(?:[ \-][A-Za-zÀ-ÿ'’\-]+){0,2})",
    re.IGNORECASE,
)

# Words that follow a preposition but are not places — avoid false "city" hits.
_CITY_STOPWORDS = {
    "la", "le", "les", "mon", "ma", "mes", "un", "une", "des", "cause", "cote",
    "côté", "coté", "maison", "domicile", "chez", "moi", "cause de", "urgence",
    "propos", "aide", "secours",
}

_SYSTEM_PROMPT_FR = (
    "Tu analyses la demande d'un particulier cherchant un artisan sur un annuaire. "
    "Retourne UNIQUEMENT du JSON valide avec ces clés : "
    "trade (une de ces valeurs exactes ou null : "
    + ", ".join(TRADES.keys())
    + "), city (nom de ville/commune française ou code postal, ou null), "
    "keywords (courte chaîne de mots-clés utiles, ou null). "
    "Déduis le métier à partir du PROBLÈME décrit (ex : « fuite sous l'évier » → plombier, "
    "« porte claquée » → serrurier). Ne devine pas une ville absente : mets null. "
    "Pas d'explications, pas de markdown."
)


def _clean_city(raw: str | None) -> str | None:
    if not raw:
        return None
    city = raw.strip().strip(".,;:!?").strip()
    if not city:
        return None
    if city.lower() in _CITY_STOPWORDS:
        return None
    return city


def _fallback_parse(query: str) -> dict:
    lower = query.lower()

    trade = None
    for key, synonyms in TRADE_SYNONYMS.items():
        if any(s in lower for s in synonyms):
            trade = key
            break
    if trade is None:
        for key in TRADES:
            if key in lower:
                trade = key
                break

    city = None
    m = _POSTAL_RE.search(query)
    if m:
        city = m.group(1)
    else:
        m = _CITY_PREP_RE.search(query)
        if m:
            city = _clean_city(m.group(1))

    return {"trade": trade, "city": city, "keywords": None}


def _parse_with_mistral(query: str, api_key: str) -> dict:
    from mistralai import Mistral

    model = current_app.config.get("MISTRAL_MODEL", "mistral-small-latest")
    client = Mistral(api_key=api_key)
    response = client.chat.complete(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT_FR},
            {"role": "user", "content": query.strip()[:500]},
        ],
        temperature=0.1,
    )
    data = json.loads(response.choices[0].message.content)
    trade = data.get("trade")
    if trade not in TRADES:
        trade = None
    return {
        "trade": trade,
        "city": _clean_city(data.get("city")),
        "keywords": (data.get("keywords") or None),
    }


def parse_query(query: str) -> dict:
    """Extract {trade, city, keywords} from a free-text query. Never raises."""
    query = (query or "").strip()
    if not query:
        return {"trade": None, "city": None, "keywords": None}

    api_key = current_app.config.get("MISTRAL_API_KEY") or os.environ.get("MISTRAL_API_KEY")
    if api_key:
        try:
            parsed = _parse_with_mistral(query, api_key)
            # If the model found nothing usable, back it up with the rule parser.
            if not parsed.get("trade") and not parsed.get("city"):
                fb = _fallback_parse(query)
                parsed["trade"] = parsed.get("trade") or fb["trade"]
                parsed["city"] = parsed.get("city") or fb["city"]
            return parsed
        except Exception:
            logger.exception("Mistral AI search parse failed — using fallback parser")
    return _fallback_parse(query)


def ai_search(query: str, lang: str = "fr", limit: int = 48) -> dict:
    """Natural-language directory search. Returns filters + matching artisans."""
    parsed = parse_query(query)
    trade = parsed.get("trade")
    city = parsed.get("city")

    payload = search_public_artisans(trade=trade, city=city, q=None, limit=limit, lang=lang)

    # If a trade+city combo returns nothing, relax to trade-only (still useful).
    if payload["count"] == 0 and trade and city:
        relaxed = search_public_artisans(trade=trade, city=None, q=None, limit=limit, lang=lang)
        if relaxed["count"]:
            relaxed["relaxed"] = True
            payload = relaxed
    # Last resort: plain full-text on the raw query so the user always sees something.
    if payload["count"] == 0 and not trade and not city:
        payload = search_public_artisans(trade=None, city=None, q=query.strip(), limit=limit, lang=lang)

    payload["understood"] = {
        "trade": trade,
        "trade_label": TRADES[trade]["label_en" if lang == "en" else "label_fr"] if trade else None,
        "city": city,
        "query": query.strip(),
    }
    return payload
