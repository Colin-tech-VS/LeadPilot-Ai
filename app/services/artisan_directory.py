"""Public artisan directory — search and profile resolution."""

from sqlalchemy import or_

from app.constants.trades import DEFAULT_TRADE, TRADES, trade_icon, trade_label
from app.core.extensions import db
from app.models.tenant import Tenant
from app.utils.slug import unique_public_slug


def public_artisans_query(trade=None, city=None, q=None):
    query = Tenant.query.filter(
        Tenant.is_public.is_(True),
        Tenant.public_slug.isnot(None),
    )
    if trade and trade in TRADES:
        query = query.filter(Tenant.trade_type == trade)
    if city:
        term = city.strip()
        like = f"%{term}%"
        query = query.filter(
            or_(
                Tenant.city.ilike(like),
                Tenant.postal_code.ilike(like),
                Tenant.address.ilike(like),
            )
        )
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            or_(
                Tenant.name.ilike(like),
                Tenant.city.ilike(like),
                Tenant.postal_code.ilike(like),
                Tenant.public_blurb.ilike(like),
            )
        )
    if db.engine.dialect.name == "postgresql":
        query = query.order_by(Tenant.city.asc().nullslast(), Tenant.name.asc())
    else:
        query = query.order_by(Tenant.city.asc(), Tenant.name.asc())
    return query


def list_public_artisans(trade=None, city=None, q=None, limit=48):
    """Return tenants visible in the public directory (no subscription gate)."""
    return public_artisans_query(trade, city, q).limit(limit).all()


def get_public_artisan_by_slug(slug: str) -> Tenant | None:
    if not slug:
        return None
    return Tenant.query.filter_by(public_slug=slug, is_public=True).first()


def artisan_card_dict(tenant: Tenant, lang: str = "fr") -> dict:
    return {
        "id": str(tenant.id),
        "slug": tenant.public_slug,
        "name": tenant.name,
        "trade": tenant.trade_type,
        "trade_label": trade_label(tenant.trade_type, lang),
        "trade_icon": trade_icon(tenant.trade_type),
        "city": tenant.city,
        "postal_code": tenant.postal_code,
        "blurb": tenant.public_blurb,
        "radius_km": tenant.service_radius_km,
        "ai_phone_number": tenant.ai_phone_number,
        "profile_url": f"/artisans/{tenant.public_slug}",
    }


def registry_card_dict(listing, lang: str = "fr") -> dict:
    """Card payload for an unclaimed registry listing.

    Shaped like an artisan card so the front end can render both in one list,
    but flagged ``registry`` and carrying no phone, no radius and no booking
    URL — because we hold none of that. Presenting it as bookable would promise
    an appointment nobody can honour.
    """
    from app.utils.naming import display_city, display_name

    return {
        "id": listing.siren,
        "slug": None,
        "name": display_name(listing.name),
        "trade": listing.trade_key,
        "trade_label": trade_label(listing.trade_key, lang),
        "trade_icon": trade_icon(listing.trade_key),
        "city": display_city(listing.city),
        "postal_code": listing.postal_code,
        "blurb": None,
        "radius_km": None,
        "ai_phone_number": None,
        "profile_url": f"/artisans/entreprise/{listing.siren}",
        "registry": True,
        "years_active": listing.years_active,
    }


def search_public_artisans(trade=None, city=None, q=None, limit=48, lang: str = "fr") -> dict:
    """Registered artisans first, then businesses known only to the register.

    The directory page already shows registry listings server-side; leaving
    them out of the search endpoint meant typing the very same trade and town
    into the search box returned "0 artisan" over a page listing twelve of them.
    """
    rows = list_public_artisans(trade=trade, city=city, q=q, limit=limit)
    cards = [artisan_card_dict(t, lang) for t in rows]

    listings: list[dict] = []
    remaining = max(0, limit - len(cards))
    if remaining:
        try:
            from app.services.registry_import import search_listings

            listings = [
                registry_card_dict(row, lang)
                for row in search_listings(
                    trade_key=trade if trade in TRADES else None,
                    city=city,
                    limit=min(remaining, 12),
                )
            ]
        except Exception:  # noqa: BLE001 — search must not fail over the extras
            listings = []

    return {
        "count": len(cards),
        "registry_count": len(listings),
        "artisans": cards,
        "registry": listings,
    }


def backfill_directory_visibility() -> int:
    """Ensure every tenant with a name is listed with a unique public slug."""
    rows = Tenant.query.filter(Tenant.name.isnot(None)).all()
    updated = 0
    for tenant in rows:
        changed = False
        if not tenant.public_slug:
            base = tenant.name
            if tenant.city:
                base = f"{tenant.name}-{tenant.city}"
            tenant.public_slug = unique_public_slug(base, tenant.id)
            changed = True
        if not tenant.trade_type or tenant.trade_type not in TRADES:
            tenant.trade_type = DEFAULT_TRADE
            changed = True
        if tenant.is_public is not True:
            tenant.is_public = True
            changed = True
        if changed:
            updated += 1
    if updated:
        db.session.commit()
    return updated
