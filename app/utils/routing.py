import logging

import requests

logger = logging.getLogger(__name__)

OSRM_BASE = "https://router.project-osrm.org/route/v1/driving"
TRANSIT_FACTOR = 1.55
TRANSIT_EXTRA_MIN = 5


def fetch_driving_route(
    from_lat: float, from_lng: float, to_lat: float, to_lng: float
) -> dict | None:
    """Fetch driving route from OSRM. Returns distance_m, duration_s, coordinates."""
    url = f"{OSRM_BASE}/{from_lng},{from_lat};{to_lng},{to_lat}"
    try:
        resp = requests.get(
            url,
            params={"overview": "full", "geometries": "geojson", "steps": "false"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != "Ok" or not data.get("routes"):
            return None
        route = data["routes"][0]
        coords = route["geometry"]["coordinates"]
        latlngs = [[c[1], c[0]] for c in coords]
        duration_s = int(route["duration"])
        distance_m = int(route["distance"])
        return {
            "coordinates": latlngs,
            "duration_s": duration_s,
            "distance_m": distance_m,
            "duration_car_min": max(1, round(duration_s / 60)),
            "duration_transit_min": max(
                1, round(duration_s / 60 * TRANSIT_FACTOR) + TRANSIT_EXTRA_MIN
            ),
            "distance_km": round(distance_m / 1000, 1),
        }
    except Exception:
        logger.exception("OSRM route failed")
        return None
