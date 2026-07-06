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
        like = f"%{city.strip()}%"
        query = query.filter(Tenant.city.ilike(like))
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            db.or_(
                Tenant.name.ilike(like),
                Tenant.city.ilike(like),
                Tenant.public_blurb.ilike(like),
            )
        )
    return query.order_by(Tenant.city.asc(), Tenant.name.asc())


def list_public_artisans(trade=None, city=None, q=None, limit=48):
    rows = public_artisans_query(trade, city, q).limit(limit * 2).all()
    return [t for t in rows if t.subscription_active][:limit]


def get_public_artisan_by_slug(slug: str) -> Tenant | None:
    if not slug:
        return None
    tenant = Tenant.query.filter_by(public_slug=slug, is_public=True).first()
    if not tenant or not tenant.subscription_active:
        return None
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
    }
