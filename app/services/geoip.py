"""IP geolocation for traffic analytics — city-level precision with cache."""
from __future__ import annotations

import hashlib
import ipaddress
import logging
from typing import Any

import requests
from flask import current_app

from app.core.extensions import db
from app.models.ip_geo_cache import IpGeoCache

logger = logging.getLogger(__name__)

_FIELDS = "status,country,countryCode,regionName,city,zip,lat,lon"


def _hash_ip(ip: str) -> str:
    return hashlib.sha256(ip.encode()).hexdigest()


def is_public_ip(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_global
    except ValueError:
        return False


def _from_cache(ip_hash: str) -> dict[str, Any] | None:
    row = db.session.get(IpGeoCache, ip_hash)
    return row.as_dict() if row else None


def _save_cache(ip_hash: str, geo: dict[str, Any]) -> dict[str, Any]:
    row = db.session.get(IpGeoCache, ip_hash)
    if not row:
        row = IpGeoCache(ip_hash=ip_hash)
        db.session.add(row)
    row.country_code = geo.get("country_code")
    row.country = geo.get("country")
    row.region = geo.get("region")
    row.city = geo.get("city")
    row.postal_code = geo.get("postal_code")
    row.latitude = geo.get("latitude")
    row.longitude = geo.get("longitude")
    db.session.commit()
    return geo


def _fetch_remote(ip: str) -> dict[str, Any] | None:
    if not current_app.config.get("GEOIP_ENABLED", True):
        return None
    try:
        resp = requests.get(
            f"http://ip-api.com/json/{ip}",
            params={"fields": _FIELDS},
            timeout=2.5,
        )
        data = resp.json()
        if data.get("status") != "success":
            return None
        return {
            "country_code": (data.get("countryCode") or "")[:2] or None,
            "country": (data.get("country") or "")[:80] or None,
            "region": (data.get("regionName") or "")[:100] or None,
            "city": (data.get("city") or "")[:100] or None,
            "postal_code": (data.get("zip") or "")[:20] or None,
            "latitude": float(data["lat"]) if data.get("lat") is not None else None,
            "longitude": float(data["lon"]) if data.get("lon") is not None else None,
        }
    except Exception:  # pragma: no cover - network
        logger.debug("geoip lookup failed for %s", ip, exc_info=True)
        return None


def lookup_ip(ip: str) -> dict[str, Any] | None:
    """Resolve public IP to city-level geo. Returns None for private/unknown IPs."""
    ip = (ip or "").strip()
    if not ip or not is_public_ip(ip):
        return None
    ip_hash = _hash_ip(ip)
    cached = _from_cache(ip_hash)
    if cached:
        return cached
    geo = _fetch_remote(ip)
    if not geo:
        return None
    try:
        return _save_cache(ip_hash, geo)
    except Exception:
        db.session.rollback()
        return geo


def format_location(geo: dict[str, Any] | None, *, unknown: str = "Inconnu") -> str:
    if not geo:
        return unknown
    parts = []
    city = (geo.get("city") or "").strip()
    postal = (geo.get("postal_code") or "").strip()
    region = (geo.get("region") or "").strip()
    country = (geo.get("country_code") or geo.get("country") or "").strip()
    if city:
        parts.append(city + (f" {postal}" if postal else ""))
    elif postal:
        parts.append(postal)
    if region and region.lower() != (city or "").lower():
        parts.append(region)
    if country:
        parts.append(f"({country})")
    return ", ".join(parts) if parts else unknown
