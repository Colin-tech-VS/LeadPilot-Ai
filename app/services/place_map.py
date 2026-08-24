"""Resolve a lat/lng for public place maps (Leaflet + OSM tiles)."""
from __future__ import annotations

import logging

from app.core.extensions import db
from app.utils.geocoding import geocode_address

logger = logging.getLogger(__name__)


def _query(*parts: str | None) -> str:
    return " ".join(p.strip() for p in parts if p and str(p).strip())


def _city_fallback(city_slug: str | None, city_name: str | None) -> tuple[float, float] | None:
    from app.constants.cities import city_info, city_slugify

    slug = (city_slug or "").strip() or (city_slugify(city_name) if city_name else "")
    info = city_info(slug) if slug else None
    if info:
        return float(info["lat"]), float(info["lon"])
    return None


def resolve_coords(
    *,
    latitude: float | None,
    longitude: float | None,
    address: str | None = None,
    postal_code: str | None = None,
    city: str | None = None,
    city_slug: str | None = None,
) -> tuple[float, float] | None:
    """Return (lat, lng) from stored coords, geocoded address, or city centroid."""
    if latitude is not None and longitude is not None:
        try:
            return float(latitude), float(longitude)
        except (TypeError, ValueError):
            pass

    query = _query(address, postal_code, city, "France")
    has_street = bool((address or "").strip())
    if has_street and query and query != "France":
        hit = geocode_address(query)
        if hit:
            return hit

    fallback = _city_fallback(city_slug, city)
    if fallback:
        return fallback
    city_query = _query(postal_code, city, "France")
    if city_query and city_query != "France":
        return geocode_address(city_query)
    return None


def ensure_listing_coords(listing) -> tuple[float, float] | None:
    coords = resolve_coords(
        latitude=listing.latitude,
        longitude=listing.longitude,
        address=listing.address,
        postal_code=listing.postal_code,
        city=listing.city,
        city_slug=listing.city_slug,
    )
    if coords and (listing.latitude is None or listing.longitude is None):
        listing.latitude, listing.longitude = coords
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            logger.exception("Could not persist listing coordinates")
    return coords


def ensure_tenant_coords(tenant) -> tuple[float, float] | None:
    coords = resolve_coords(
        latitude=tenant.latitude,
        longitude=tenant.longitude,
        address=tenant.address,
        postal_code=tenant.postal_code,
        city=tenant.city,
    )
    if coords and (tenant.latitude is None or tenant.longitude is None):
        tenant.latitude, tenant.longitude = coords
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            logger.exception("Could not persist tenant coordinates")
    return coords
