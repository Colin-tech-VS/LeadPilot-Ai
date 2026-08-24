import logging
import time

import requests
from flask import current_app

logger = logging.getLogger(__name__)

_last_request_at = 0.0


def _ban_geocode(address: str) -> tuple[float, float] | None:
    """Base Adresse Nationale — free, accurate for French street addresses."""
    try:
        resp = requests.get(
            "https://api-adresse.data.gouv.fr/search/",
            params={"q": address.strip(), "limit": 1},
            timeout=5,
        )
        resp.raise_for_status()
        features = (resp.json() or {}).get("features") or []
        if not features:
            return None
        lon, lat = features[0]["geometry"]["coordinates"]
        return float(lat), float(lon)
    except Exception:
        logger.exception("BAN geocoding failed for address: %s", address[:80])
        return None


def geocode_address(address: str) -> tuple[float, float] | None:
    """Geocode an address. Returns (lat, lng) or None.

    Google Geocoding first when a Places key is configured. Otherwise the free
    Base Adresse Nationale, then Nominatim.
    """
    if not address or not address.strip():
        return None

    from app.services import google_places

    if google_places.is_enabled():
        hit = google_places.geocode(address)
        if hit is not None:
            return hit

    ban = _ban_geocode(address)
    if ban is not None:
        return ban

    global _last_request_at
    elapsed = time.time() - _last_request_at
    if elapsed < 1.1:
        time.sleep(1.1 - elapsed)

    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": address.strip(), "format": "json", "limit": 1},
            headers={"User-Agent": "PilotCoreAI/1.0 (plumber-saas)"},
            timeout=8,
        )
        _last_request_at = time.time()
        resp.raise_for_status()
        results = resp.json()
        if not results:
            return None
        return float(results[0]["lat"]), float(results[0]["lon"])
    except Exception:
        logger.exception("Geocoding failed for address: %s", address[:80])
        return None
