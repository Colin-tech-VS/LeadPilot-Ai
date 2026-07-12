"""Curated list of top French cities for programmatic local SEO landing pages.

Each entry maps a URL slug (accent-free, lowercase, hyphenated) to a display
name and department. The list powers `/artisans/<trade>/<city>` landing pages
and their inclusion in sitemap.xml so local-intent queries like
« plombier lyon » can rank on a dedicated, self-canonical URL.
"""
from __future__ import annotations

import re
import unicodedata

# (slug, display_name) — top French cities by population / search demand.
TOP_CITIES: list[tuple[str, str]] = [
    ("paris", "Paris"),
    ("marseille", "Marseille"),
    ("lyon", "Lyon"),
    ("toulouse", "Toulouse"),
    ("nice", "Nice"),
    ("nantes", "Nantes"),
    ("montpellier", "Montpellier"),
    ("strasbourg", "Strasbourg"),
    ("bordeaux", "Bordeaux"),
    ("lille", "Lille"),
    ("rennes", "Rennes"),
    ("reims", "Reims"),
    ("saint-etienne", "Saint-Étienne"),
    ("toulon", "Toulon"),
    ("le-havre", "Le Havre"),
    ("grenoble", "Grenoble"),
    ("dijon", "Dijon"),
    ("angers", "Angers"),
    ("nimes", "Nîmes"),
    ("villeurbanne", "Villeurbanne"),
    ("clermont-ferrand", "Clermont-Ferrand"),
    ("aix-en-provence", "Aix-en-Provence"),
    ("le-mans", "Le Mans"),
    ("brest", "Brest"),
    ("tours", "Tours"),
    ("amiens", "Amiens"),
    ("limoges", "Limoges"),
    ("annecy", "Annecy"),
    ("perpignan", "Perpignan"),
    ("besancon", "Besançon"),
    ("metz", "Metz"),
    ("orleans", "Orléans"),
    ("rouen", "Rouen"),
    ("mulhouse", "Mulhouse"),
    ("caen", "Caen"),
    ("nancy", "Nancy"),
    ("versailles", "Versailles"),
    ("nanterre", "Nanterre"),
    ("montreuil", "Montreuil"),
    ("argenteuil", "Argenteuil"),
]

_CITY_BY_SLUG: dict[str, str] = {slug: name for slug, name in TOP_CITIES}


def city_slugify(value: str) -> str:
    """URL-safe, accent-free slug for a city name."""
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^\w\s-]", "", value).strip().lower()
    return re.sub(r"[-\s]+", "-", value).strip("-")


def city_display_name(slug: str) -> str:
    """Human-readable city name for a slug.

    Known cities keep their accented display name; unknown slugs are prettified
    (`aix-en-provence` → `Aix En Provence`) so arbitrary city pages still render.
    """
    slug = (slug or "").strip().lower()
    if slug in _CITY_BY_SLUG:
        return _CITY_BY_SLUG[slug]
    parts = [p for p in slug.replace("_", "-").split("-") if p]
    return " ".join(p.capitalize() for p in parts)


def is_known_city(slug: str) -> bool:
    return (slug or "").strip().lower() in _CITY_BY_SLUG
