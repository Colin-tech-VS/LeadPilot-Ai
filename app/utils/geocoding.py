import logging
import time

import requests
from flask import current_app

logger = logging.getLogger(__name__)

_last_request_at = 0.0


def geocode_address(address: str) -> tuple[float, float] | None:
    """Geocode an address via Nominatim (OpenStreetMap). Returns (lat, lng) or None."""
    if not address or not address.strip():
        return None

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
