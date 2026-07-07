"""Métiers d'artisan supportés sur l'annuaire public."""

TRADES = {
    "plombier": {"label_fr": "Plombier", "label_en": "Plumber", "icon": "🔧", "group_fr": "Dépannage"},
    "serrurier": {"label_fr": "Serrurier", "label_en": "Locksmith", "icon": "🔑", "group_fr": "Dépannage"},
    "electricien": {"label_fr": "Électricien", "label_en": "Electrician", "icon": "⚡", "group_fr": "Dépannage"},
    "chauffagiste": {"label_fr": "Chauffagiste", "label_en": "Heating engineer", "icon": "🔥", "group_fr": "Dépannage"},
    "climaticien": {"label_fr": "Climaticien", "label_en": "HVAC technician", "icon": "❄️", "group_fr": "Dépannage"},
    "vitrier": {"label_fr": "Vitrier", "label_en": "Glazier", "icon": "🪟", "group_fr": "Dépannage"},
    "menuisier": {"label_fr": "Menuisier", "label_en": "Carpenter", "icon": "🪚", "group_fr": "Bâtiment"},
    "peintre": {"label_fr": "Peintre", "label_en": "Painter", "icon": "🎨", "group_fr": "Bâtiment"},
    "macon": {"label_fr": "Maçon", "label_en": "Mason", "icon": "🧱", "group_fr": "Bâtiment"},
    "couvreur": {"label_fr": "Couvreur", "label_en": "Roofer", "icon": "🏠", "group_fr": "Bâtiment"},
    "carreleur": {"label_fr": "Carreleur", "label_en": "Tiler", "icon": "◻️", "group_fr": "Bâtiment"},
    "charpentier": {"label_fr": "Charpentier", "label_en": "Timber framer", "icon": "🪵", "group_fr": "Bâtiment"},
    "paysagiste": {"label_fr": "Paysagiste", "label_en": "Landscaper", "icon": "🌿", "group_fr": "Extérieur"},
    "autre": {"label_fr": "Autre artisan", "label_en": "Other tradesperson", "icon": "🛠️", "group_fr": "Autre"},
}

DEFAULT_TRADE = "plombier"

# schema.org @type for local SEO on artisan profile pages
TRADE_SCHEMA_TYPES = {
    "plombier": "Plumber",
    "serrurier": "Locksmith",
    "electricien": "Electrician",
    "chauffagiste": "HVACBusiness",
    "climaticien": "HVACBusiness",
    "vitrier": "HomeAndConstructionBusiness",
    "menuisier": "HomeAndConstructionBusiness",
    "peintre": "HousePainter",
    "macon": "HomeAndConstructionBusiness",
    "couvreur": "RoofingContractor",
    "carreleur": "HomeAndConstructionBusiness",
    "charpentier": "HomeAndConstructionBusiness",
    "paysagiste": "LandscapingBusiness",
    "autre": "HomeAndConstructionBusiness",
}


def trade_label(trade_key: str | None, lang: str = "fr") -> str:
    key = trade_key if trade_key in TRADES else DEFAULT_TRADE
    field = "label_en" if lang == "en" else "label_fr"
    return TRADES[key][field]


def trade_icon(trade_key: str | None) -> str:
    key = trade_key if trade_key in TRADES else DEFAULT_TRADE
    return TRADES[key]["icon"]


def trade_schema_type(trade_key: str | None) -> str:
    key = trade_key if trade_key in TRADES else DEFAULT_TRADE
    return TRADE_SCHEMA_TYPES.get(key, "HomeAndConstructionBusiness")


def trade_choices(lang: str = "fr") -> list[dict]:
    """Ordered list for templates and APIs."""
    field = "label_en" if lang == "en" else "label_fr"
    return [
        {
            "key": key,
            "label": meta[field],
            "icon": meta["icon"],
            "group": meta.get("group_fr" if lang == "fr" else "group_fr", ""),
        }
        for key, meta in TRADES.items()
    ]
