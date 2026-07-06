"""Public artisan directory — search and profile resolution."""

from app.constants.trades import TRADES, trade_icon, trade_label
from app.core.extensions import db
from app.models.tenant import Tenant


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
            db.or_(
                Tenant.city.ilike(like),
                Tenant.postal_code.ilike(like),
                Tenant.address.ilike(like),
            )
        )
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            db.or_(
                Tenant.name.ilike(like),
                Tenant.city.ilike(like),
                Tenant.postal_code.ilike(like),
                Tenant.public_blurb.ilike(like),
            )
        )
    return query.order_by(Tenant.city.asc().nullslast(), Tenant.name.asc())


def list_public_artisans(trade=None, city=None, q=None, limit=48):
    """Return tenants visible in the public directory (no subscription gate)."""
    return public_artisans_query(trade, city, q).limit(limit).all()


def get_public_artisan_by_slug(slug: str) -> Tenant | None:
    if not slug:
        return None
    tenant = Tenant.query.filter_by(public_slug=slug, is_public=True).first()
    return tenant


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
        "profile_url": f"/artisans/{tenant.public_slug}",
    }


def search_public_artisans(trade=None, city=None, q=None, limit=48, lang: str = "fr") -> dict:
    rows = list_public_artisans(trade=trade, city=city, q=q, limit=limit)
    return {
        "count": len(rows),
        "artisans": [artisan_card_dict(t, lang) for t in rows],
    }


def backfill_directory_visibility() -> int:
    """Publish existing tenants who completed their profile but were never listed."""
    rows = Tenant.query.filter(
        Tenant.is_public.is_(False),
        Tenant.public_slug.isnot(None),
        Tenant.city.isnot(None),
        Tenant.name.isnot(None),
    ).all()
    for tenant in rows:
        tenant.is_public = True
    if rows:
        db.session.commit()
    return len(rows)
