"""Admin-editable site content: pricing offers and generic key/value settings.

The landing page reads its pricing from the ``offers`` table (seeded once from
the French i18n strings). After seeding, the admin console is the single source
of truth, so the site owner can change prices/wording without a deploy.
"""
import logging

from app.core.extensions import db
from app.models.offer import Offer
from app.models.setting import SiteSetting

logger = logging.getLogger(__name__)

# Landing display only: rewrite known over-claims if an admin-edited offer still
# lists a capability that the product does not actually ship.
_HONEST_FEATURE_REWRITE = {
    "Synchronisation Google Agenda": "Agenda des rendez-vous dans votre espace",
    "Google Calendar sync": "Appointments in your workspace",
    "Intégration CRM & rapports avancés": "Outils marketing et suivi des demandes",
    "CRM integration & advanced reports": "Marketing tools and request tracking",
    "Personnalisation complète de l'IA": "Personnalisation de l'assistant (prénom, consignes)",
    "Full AI customization": "Assistant personalisation (name, instructions)",
}


def honest_offer_features(offer):
    """Feature lines shown on /pro — never invent capabilities."""
    raw = offer.feature_list() if offer else []
    items = [_HONEST_FEATURE_REWRITE.get(item, item) for item in raw]
    key = (getattr(offer, "key", "") or "").lower()
    if key == "starter" and not any(
        "rendez-vous automatique" in i.lower() or "automatic booking" in i.lower()
        for i in items
    ):
        items.append("Sans prise de rendez-vous automatique (disponible en Pro)")
    return items

# Order + which plan card is highlighted on the landing grid.
_OFFER_KEYS = ("starter", "pro", "premium")
_FEATURED = "pro"


def _seed_offers():
    """Create the three default plans from the i18n defaults. Idempotent — only
    runs when the offers table is empty."""
    from app.utils.i18n import translate

    def t(key):
        return translate(f"landing.pricing_{key}", "fr")

    feature_counts = {"starter": 4, "pro": 5, "premium": 5}
    for order, key in enumerate(_OFFER_KEYS):
        feats = [
            t(f"{key}_feat_{i}")
            for i in range(1, feature_counts[key] + 1)
        ]
        offer = Offer(
            key=key,
            name=t(f"{key}_name"),
            badge=t(f"{key}_badge"),
            price=t(f"{key}_price"),
            period=t(f"{key}_period"),
            calls=t(f"{key}_calls"),
            description=t(f"{key}_desc"),
            cta=t(f"{key}_cta"),
            featured=(key == _FEATURED),
            active=True,
            sort_order=order,
        )
        offer.set_features(feats)
        db.session.add(offer)
    db.session.commit()


def get_offers(active_only=False):
    """Return the pricing offers ordered for display, seeding defaults on first
    use. Never raises to the caller (landing page must always render)."""
    try:
        query = Offer.query
        if active_only:
            query = query.filter(Offer.active.is_(True))
        offers = query.order_by(Offer.sort_order.asc()).all()
        if not offers:
            _seed_offers()
            offers = query.order_by(Offer.sort_order.asc()).all()
        return offers
    except Exception:
        logger.exception("get_offers failed")
        db.session.rollback()
        return []


def get_offer(offer_id):
    return db.session.get(Offer, offer_id)


# ------------------------------------------------------------------ settings
def get_setting(key, default=None):
    row = db.session.get(SiteSetting, key)
    return row.value if row and row.value is not None else default


def set_setting(key, value):
    row = db.session.get(SiteSetting, key)
    if row is None:
        row = SiteSetting(key=key)
        db.session.add(row)
    row.value = value
    db.session.commit()
    return row
