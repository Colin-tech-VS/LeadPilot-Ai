import logging
import time

import requests
from flask import current_app

logger = logging.getLogger(__name__)

_last_request_at = 0.0


def geocode_address(address: str) -> tuple[float, float] | None:
    """Geocode an address. Returns (lat, lng) or None.

    Google Geocoding first when a Places key is configured: it is markedly more
    accurate on French addresses and carries no hand-rolled throttle. Nominatim
    stays as the fallback for deployments without a key — its usage policy caps
    us at one request per second, which the sleep below honours and which is far
    too slow for anything but incidental lookups.
    """
    if not address or not address.strip():
        return None

    from app.services import google_places

    if google_places.is_enabled():
        hit = google_places.geocode(address)
        if hit is not None:
            return hit

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
