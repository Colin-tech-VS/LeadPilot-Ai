"""City / address suggestions with a guaranteed answer.

Two providers sit behind one function so the browser only ever talks to our
own origin:

1. **Google Places (New)** — first choice. Best-in-class typo tolerance, and it
   resolves places the address database alone does not (a business name, a
   hamlet, a landmark).
2. **Base Adresse Nationale** (``api-adresse.data.gouv.fr``) — fallback. Free,
   authoritative for French postal addresses, and it returns the postcode
   inline, so a BAN suggestion needs no follow-up lookup to fill the postal
   code field.

The fallback is not decoration: Places is a paid API behind a quota. If the key
is missing, the billing account lapses, or Google times out, address entry has
to keep working — it sits on the signup and quote forms, which are the two
places where a failure costs a customer. ``provider`` is returned so the
caller (and the tests) can tell which path answered.
"""
from __future__ import annotations

import logging
import re

import requests

from app.services import google_places

logger = logging.getLogger(__name__)

_BAN_URL = "https://api-adresse.data.gouv.fr/search/"
_BAN_TIMEOUT = 5
_COUNTRY_SUFFIX_RE = re.compile(r",?\s*France$", re.IGNORECASE)
_BAN_CITY_LABEL_RE = re.compile(r"^(.+?)\s*\((\d{5})\)$")
_POSTAL_CITY_RE = re.compile(r"^(\d{5})\s+(.+)$")
# BAN lines have no commas: "12 Rue de la Paix 50100 Cherbourg-en-Cotentin".
_POSTCODE_THEN_CITY_RE = re.compile(r"\b(\d{5})\s+(.+)$")
_STREET_HINT_RE = re.compile(
    r"(?i)^\d+\s|\b(rue|avenue|av\.?|boulevard|bd\.?|impasse|chemin|"
    r"all[ée]e|place|cours|quai|route|square|passage|villa|cit[ée]|clos|"
    r"sentier|traverse|rond[- ]point)\b",
)


def city_from_place_query(raw: str | None) -> str:
    """Bare commune or postcode for directory filters.

    The « Où » field accepts a Google/BAN street line (« 12 Rue de la Paix,
    75002 Paris ») as well as a town name. Our listings match on ``city`` /
    ``city_slug``, so a full address would miss every row.
    """
    text = (raw or "").strip()
    if not text:
        return ""
    text = _COUNTRY_SUFFIX_RE.sub("", text).strip()
    if re.fullmatch(r"\d{5}", text):
        return text
    labelled = _BAN_CITY_LABEL_RE.fullmatch(text)
    if labelled and "," not in labelled.group(1):
        return labelled.group(1).strip()
    postal_tail = _POSTCODE_THEN_CITY_RE.search(text)
    if postal_tail:
        return postal_tail.group(2).strip().rstrip(",")
    if "," in text:
        last = [part.strip() for part in text.split(",") if part.strip()][-1]
        postal_city = _POSTAL_CITY_RE.match(last)
        if postal_city:
            return postal_city.group(2).strip()
        if re.fullmatch(r"\d{5}", last):
            return last
        return last
    # A leftover street without a postcode is not a commune — better empty
    # than ``ilike %12 rue de la paix%`` matching nothing useful.
    if _STREET_HINT_RE.search(text):
        return ""
    return text


def _ban(query: str, kind: str, limit: int) -> list[dict]:
    params = {"q": query, "limit": limit, "autocomplete": 1}
    if kind == "city":
        params["type"] = "municipality"
    try:
        resp = requests.get(_BAN_URL, params=params, timeout=_BAN_TIMEOUT)
        if resp.status_code != 200:
            return []
        features = (resp.json() or {}).get("features") or []
    except Exception as exc:
        logger.warning("BAN lookup failed: %s", exc)
        return []

    out: list[dict] = []
    for feat in features:
        props = feat.get("properties") or {}
        postcode = props.get("postcode") or ""
        city = (props.get("city") or "").strip()
        name = (props.get("name") or "").strip()
        if kind == "city":
            value = city or name or props.get("label") or ""
            label = props.get("label") or value
            if postcode:
                label = f"{label} ({postcode})"
            main, secondary = value, (postcode or "")
        else:
            # Same shape as Google Places: street on the first line, postcode
            # + commune on the second, commas in the value so city extraction
            # does not have to special-case the BAN's space-separated label.
            if name and (postcode or city):
                value = ", ".join(p for p in (name, f"{postcode} {city}".strip()) if p)
            else:
                value = props.get("label") or name
            label = value
            main = name or value
            secondary = f"{postcode} {city}".strip()
        if not value:
            continue
        coords = ((feat.get("geometry") or {}).get("coordinates")) or []
        out.append(
            {
                # No place ID: BAN suggestions are already complete, so the
                # client never needs to call /resolve for them.
                "id": "",
                "value": value,
                "label": label,
                "main": main,
                "secondary": secondary,
                "city": city,
                "postcode": postcode,
                "latitude": coords[1] if len(coords) == 2 else None,
                "longitude": coords[0] if len(coords) == 2 else None,
            }
        )
    return out


def suggest(query: str, *, kind: str = "address", lang: str = "fr", limit: int = 6,
            session_token: str | None = None) -> dict:
    """Return ``{"provider": ..., "suggestions": [...]}`` — never raises."""
    query = (query or "").strip()
    if len(query) < 2:
        return {"provider": "none", "suggestions": []}
    kind = "city" if kind == "city" else "address"
    limit = max(1, min(limit, 10))

    if google_places.is_enabled():
        results = google_places.autocomplete(
            query, kind=kind, lang=lang, limit=limit, session_token=session_token
        )
        if results:
            return {"provider": "google", "suggestions": results}

    return {"provider": "ban", "suggestions": _ban(query, kind, limit)}


def resolve(place_id: str, *, lang: str = "fr", session_token: str | None = None) -> dict | None:
    """Full detail for a Google suggestion. BAN suggestions carry no place ID."""
    return google_places.resolve(place_id, lang=lang, session_token=session_token)
