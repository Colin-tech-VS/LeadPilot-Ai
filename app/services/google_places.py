"""Server-side Google Places (New) client: autocomplete, place details, geocoding.

Why the key never reaches the browser
-------------------------------------
The obvious way to wire Places is to drop the key into a ``<script>`` tag and
let the Maps JS SDK call Google directly. We do not, for one concrete reason:
a browser-delivered key is public, and a key that is not referrer-restricted
can be lifted off the page and spent against our billing account by anyone.
Routing every call through our own origin keeps the key server-side, lets us
cache (Places is billed per request), rate-limit per IP, and fall back to the
French Base Adresse Nationale when Google is down or over quota.

Cost control
------------
Autocomplete is billed per request, so a naive "one call per keystroke" wiring
gets expensive fast. Two mitigations, both here:

* an in-process TTL cache keyed on the normalised query — the same prefix typed
  by many visitors is answered once per ``_TTL_SECONDS``;
* session tokens — Google bills an autocomplete *session* (all the keystrokes
  plus the final Place Details lookup) as a single unit when every request in
  it carries the same token. The route layer mints one per visitor session.

Caching and Google's terms
--------------------------
Google's Places terms allow a place ID to be stored indefinitely but limit
caching of other Place content. ``_TTL_SECONDS`` stays well inside that window,
and nothing here writes Place content to the database — it lives in memory for
minutes, purely as a cost and latency optimisation.

Every function degrades to ``None`` / ``[]`` rather than raising, so a missing
key, a quota error or a network timeout can never take a page down.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

import requests
from flask import current_app

logger = logging.getLogger(__name__)

_PLACES_BASE = "https://places.googleapis.com/v1"
_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"

# Places is a paid API; keep answers warm briefly to blunt repeated prefixes.
_TTL_SECONDS = 600
_MAX_ENTRIES = 2000
_TIMEOUT = 6

# The site only serves France, so every lookup is region-locked. This also cuts
# the candidate set down and makes the suggestions markedly more relevant.
_REGION = "fr"

_cache: dict[str, tuple[float, Any]] = {}
_cache_lock = threading.Lock()

# Rolling counters so /health and the admin diagnostics screen can show whether
# Places is actually answering in production, not just whether a key is set.
_stats = {"calls": 0, "errors": 0, "cache_hits": 0, "last_error": None}


def api_key() -> str:
    try:
        return (current_app.config.get("GOOGLE_PLACES_API_KEY") or "").strip()
    except RuntimeError:  # outside an app context
        return ""


def is_enabled() -> bool:
    if not api_key():
        return False
    try:
        # Pytest sets a fake key and mocks HTTP — let those tests run.
        if current_app.config.get("TESTING"):
            return True
        from app.core.production import live_provider_spend_allowed

        return live_provider_spend_allowed()
    except RuntimeError:
        return False


def _cache_get(key: str):
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            _stats["cache_hits"] += 1
            return hit[1]
        if hit:
            _cache.pop(key, None)
    return None


def _cache_put(key: str, value) -> None:
    with _cache_lock:
        if len(_cache) >= _MAX_ENTRIES:
            # Cheap eviction: drop everything already expired, and if that is
            # not enough, the oldest quarter. No LRU bookkeeping needed for a
            # cache this small.
            now = time.time()
            for k in [k for k, v in _cache.items() if v[0] <= now]:
                _cache.pop(k, None)
            if len(_cache) >= _MAX_ENTRIES:
                for k in sorted(_cache, key=lambda k: _cache[k][0])[: _MAX_ENTRIES // 4]:
                    _cache.pop(k, None)
        _cache[key] = (time.time() + _TTL_SECONDS, value)


def _record_error(exc: Exception | str) -> None:
    _stats["errors"] += 1
    _stats["last_error"] = str(exc)[:200]


def _post(path: str, payload: dict, *, field_mask: str | None = None, lang: str = "fr"):
    key = api_key()
    if not key:
        return None
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": key,
        "Accept-Language": "fr" if lang == "fr" else "en",
    }
    if field_mask:
        headers["X-Goog-FieldMask"] = field_mask
    try:
        _stats["calls"] += 1
        resp = requests.post(f"{_PLACES_BASE}/{path}", json=payload, headers=headers, timeout=_TIMEOUT)
        if resp.status_code != 200:
            _record_error(f"{path} HTTP {resp.status_code}: {resp.text[:160]}")
            return None
        return resp.json()
    except Exception as exc:  # network, timeout, bad JSON
        _record_error(exc)
        logger.warning("Google Places %s failed: %s", path, exc)
        return None


def _get(url: str, params: dict | None = None, headers: dict | None = None):
    key = api_key()
    if not key:
        return None
    try:
        _stats["calls"] += 1
        resp = requests.get(url, params=params, headers=headers, timeout=_TIMEOUT)
        if resp.status_code != 200:
            _record_error(f"HTTP {resp.status_code}: {resp.text[:160]}")
            return None
        return resp.json()
    except Exception as exc:
        _record_error(exc)
        logger.warning("Google request failed for %s: %s", url, exc)
        return None


# --------------------------------------------------------------------------
# Autocomplete
# --------------------------------------------------------------------------

# A city field must end up holding the bare commune name, because our own city
# lookups (slug, department, population) match on it. An address field keeps the
# full formatted line. Hence two shapes rather than one.
_CITY_TYPES = ["locality", "postal_code"]


def autocomplete(
    query: str,
    *,
    kind: str = "address",
    lang: str = "fr",
    session_token: str | None = None,
    limit: int = 6,
) -> list[dict]:
    """Suggestions for a city or address field.

    Returns ``[{"id", "value", "label", "postcode"}]``. ``value`` is what the
    input should hold once chosen; ``postcode`` is filled in later by
    :func:`resolve` because Autocomplete does not carry it.
    """
    query = (query or "").strip()
    if len(query) < 2 or not is_enabled():
        return []

    cache_key = f"ac:{kind}:{lang}:{query.lower()}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached[:limit]

    payload: dict[str, Any] = {
        "input": query,
        "languageCode": "fr" if lang == "fr" else "en",
        "includedRegionCodes": [_REGION],
    }
    if kind == "city":
        payload["includedPrimaryTypes"] = _CITY_TYPES
    # kind "address" / "where": no type filter, so streets, postcodes and
    # communes all appear — the directory « Où » field is a place, not a
    # commune-only box.
    # The session token ties these keystrokes to the Place Details call that
    # follows, so Google bills the whole interaction once.
    if session_token:
        payload["sessionToken"] = session_token

    data = _post("places:autocomplete", payload, lang=lang)
    if not data:
        return []

    out: list[dict] = []
    for item in data.get("suggestions", []):
        pred = item.get("placePrediction")
        if not pred:
            continue  # a query prediction, not a place — not useful for a field
        full = (pred.get("text") or {}).get("text") or ""
        structured = pred.get("structuredFormat") or {}
        main = (structured.get("mainText") or {}).get("text") or full
        secondary = (structured.get("secondaryText") or {}).get("text") or ""
        if not full:
            continue
        if kind == "city":
            value, label = main, (f"{main} — {secondary}" if secondary else main)
        else:
            # Drop the trailing ", France": every address here is French and the
            # suffix only eats room in a narrow input.
            value = full[: -len(", France")] if full.endswith(", France") else full
            label = full
        out.append(
            {
                "id": pred.get("placeId") or "",
                "value": value,
                "label": label,
                "main": main,
                "secondary": secondary[: -len(", France")].rstrip(", ")
                if secondary.endswith(", France")
                else secondary,
                "postcode": "",
            }
        )

    _cache_put(cache_key, out)
    return out[:limit]


# --------------------------------------------------------------------------
# Place details
# --------------------------------------------------------------------------

_DETAILS_MASK = "id,formattedAddress,shortFormattedAddress,addressComponents,location,displayName"


def _component(components: list[dict], wanted: str) -> str:
    for comp in components or []:
        if wanted in (comp.get("types") or []):
            return comp.get("longText") or comp.get("shortText") or ""
    return ""


def resolve(place_id: str, *, lang: str = "fr", session_token: str | None = None) -> dict | None:
    """Full address for a chosen suggestion: postcode, city, coordinates.

    This is the call that lets picking a suggestion fill the postal-code field
    and stamp coordinates on the record, which the suggestion alone cannot do.
    """
    place_id = (place_id or "").strip()
    if not place_id or not is_enabled():
        return None

    cache_key = f"pd:{lang}:{place_id}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    key = api_key()
    headers = {
        "X-Goog-Api-Key": key,
        "X-Goog-FieldMask": _DETAILS_MASK,
        "Accept-Language": "fr" if lang == "fr" else "en",
    }
    params = {}
    if session_token:
        params["sessionToken"] = session_token
    data = _get(f"{_PLACES_BASE}/places/{place_id}", params=params or None, headers=headers)
    if not data:
        return None

    components = data.get("addressComponents") or []
    loc = data.get("location") or {}
    formatted = data.get("formattedAddress") or ""
    out = {
        "id": data.get("id") or place_id,
        "address": formatted[: -len(", France")] if formatted.endswith(", France") else formatted,
        "short_address": data.get("shortFormattedAddress") or "",
        "name": (data.get("displayName") or {}).get("text") or "",
        "postcode": _component(components, "postal_code"),
        "city": _component(components, "locality") or _component(components, "administrative_area_level_2"),
        "street": " ".join(
            p for p in (_component(components, "street_number"), _component(components, "route")) if p
        ).strip(),
        "latitude": loc.get("latitude"),
        "longitude": loc.get("longitude"),
    }
    _cache_put(cache_key, out)
    return out


# --------------------------------------------------------------------------
# Geocoding
# --------------------------------------------------------------------------


def geocode(address: str) -> tuple[float, float] | None:
    """(lat, lng) for a free-text address, or ``None``.

    Used as the first choice by :mod:`app.utils.geocoding`, which keeps
    Nominatim as the fallback for when no key is configured.
    """
    address = (address or "").strip()
    if not address or not is_enabled():
        return None

    cache_key = f"geo:{address.lower()}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    data = _get(
        _GEOCODE_URL,
        params={"address": address, "key": api_key(), "region": _REGION, "language": "fr"},
    )
    if not data:
        return None
    status = data.get("status")
    if status != "OK":
        # ZERO_RESULTS is a legitimate answer, not a failure worth counting.
        if status != "ZERO_RESULTS":
            _record_error(f"geocode status {status}: {data.get('error_message', '')[:120]}")
        return None
    results = data.get("results") or []
    if not results:
        return None
    loc = ((results[0].get("geometry") or {}).get("location")) or {}
    lat, lng = loc.get("lat"), loc.get("lng")
    if lat is None or lng is None:
        return None
    out = (float(lat), float(lng))
    _cache_put(cache_key, out)
    return out


# --------------------------------------------------------------------------
# Diagnostics
# --------------------------------------------------------------------------


def stats() -> dict:
    """Counters for /health/integrations and the admin diagnostics screen."""
    with _cache_lock:
        cached = len(_cache)
    return {
        "enabled": is_enabled(),
        "cached_entries": cached,
        "calls": _stats["calls"],
        "errors": _stats["errors"],
        "cache_hits": _stats["cache_hits"],
        "last_error": _stats["last_error"],
    }


def selftest(lang: str = "fr") -> dict:
    """Live round-trip against Google — used by the admin diagnostics screen.

    Deliberately not called on the health endpoint: it costs a real request.
    """
    if not is_enabled():
        return {"ok": False, "reason": "GOOGLE_PLACES_API_KEY manquante"}
    suggestions = autocomplete("Chaville", kind="city", lang=lang)
    if not suggestions:
        return {"ok": False, "reason": _stats["last_error"] or "aucune suggestion renvoyée"}
    return {"ok": True, "sample": suggestions[0]["value"], "count": len(suggestions)}


def reset_cache() -> None:
    """Test seam."""
    with _cache_lock:
        _cache.clear()
    _stats.update({"calls": 0, "errors": 0, "cache_hits": 0, "last_error": None})
