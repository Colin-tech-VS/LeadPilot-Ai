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
    "Synchronisation Google Agenda": "Les rendez-vous posés apparaissent dans votre agenda",
    "Google Calendar sync": "Booked appointments appear in your workspace",
    "Intégration CRM & rapports avancés": "Campagnes SMS et e-mail vers vos clients",
    "CRM integration & advanced reports": "SMS and email campaigns to your customers",
    "Personnalisation complète de l'IA": "Personnalisation du prénom de l'assistant",
    "Full AI customization": "Assistant first-name personalisation",
    "Personnalisation de l'assistant (prénom, consignes)": "Personnalisation du prénom de l'assistant",
    "Assistant personalisation (name, instructions)": "Assistant first-name personalisation",
    "Plusieurs utilisateurs (jusqu'à 10)": "Réservation en ligne depuis votre fiche publique",
    "Multiple users (up to 10)": "Online booking from your public listing",
    "Plusieurs utilisateurs & statistiques": "Réservation en ligne depuis votre fiche publique",
    "Multiple users & statistics": "Online booking from your public listing",
    "Plusieurs numéros de réception": "Ligne IA à votre nom une fois abonné",
    "Plusieurs numéros de téléphone": "Ligne IA à votre nom une fois abonné",
    "Several reception numbers": "AI line in your name once you subscribe",
    "Multiple phone numbers": "AI line in your name once you subscribe",
}


def _looks_english(text: str) -> bool:
    low = text.lower()
    return any(
        tok in low
        for tok in ("multiple", "several", "calendar", "users", "everything in", "instructions")
    )


def honest_feature_line(item: str) -> str:
    """Map one advertised line onto a capability the product actually ships."""
    text = (item or "").strip()
    if not text:
        return text
    mapped = _HONEST_FEATURE_REWRITE.get(text)
    if mapped:
        return mapped
    low = text.lower()
    english = _looks_english(text)
    if "google agenda" in low or "google calendar" in low:
        return (
            "Booked appointments appear in your workspace"
            if english
            else "Les rendez-vous posés apparaissent dans votre agenda"
        )
    if "plusieurs utilisateurs" in low or "multiple users" in low:
        return (
            "Online booking from your public listing"
            if english
            else "Réservation en ligne depuis votre fiche publique"
        )
    if "plusieurs numéro" in low or "several reception" in low or "multiple phone" in low:
        return (
            "AI line in your name once you subscribe"
            if english
            else "Ligne IA à votre nom une fois abonné"
        )
    if "consignes" in low or ("instructions" in low and "personal" in low):
        return (
            "Assistant first-name personalisation"
            if english
            else "Personnalisation du prénom de l'assistant"
        )
    return text


def honest_offer_features(offer):
    """Feature lines shown on /pro — never invent capabilities."""
    raw = offer.feature_list() if offer else []
    items = [honest_feature_line(item) for item in raw]
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
