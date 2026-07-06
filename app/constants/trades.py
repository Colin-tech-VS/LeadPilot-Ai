"""Métiers d'artisan supportés sur l'annuaire public."""

TRADES = {
    "plombier": {"label_fr": "Plombier", "label_en": "Plumber", "icon": "🔧"},
    "serrurier": {"label_fr": "Serrurier", "label_en": "Locksmith", "icon": "🔑"},
    "electricien": {"label_fr": "Électricien", "label_en": "Electrician", "icon": "⚡"},
    "chauffagiste": {"label_fr": "Chauffagiste", "label_en": "Heating engineer", "icon": "🔥"},
    "menuisier": {"label_fr": "Menuisier", "label_en": "Carpenter", "icon": "🪚"},
    "peintre": {"label_fr": "Peintre", "label_en": "Painter", "icon": "🎨"},
    "macon": {"label_fr": "Maçon", "label_en": "Mason", "icon": "🧱"},
    "couvreur": {"label_fr": "Couvreur", "label_en": "Roofer", "icon": "🏠"},
    "vitrier": {"label_fr": "Vitrier", "label_en": "Glazier", "icon": "🪟"},
    "autre": {"label_fr": "Artisan", "label_en": "Tradesperson", "icon": "🛠️"},
}

DEFAULT_TRADE = "plombier"


def trade_label(trade_key: str | None, lang: str = "fr") -> str:
    key = trade_key if trade_key in TRADES else DEFAULT_TRADE
    field = "label_en" if lang == "en" else "label_fr"
    return TRADES[key][field]


def trade_icon(trade_key: str | None) -> str:
    key = trade_key if trade_key in TRADES else DEFAULT_TRADE
    return TRADES[key]["icon"]
