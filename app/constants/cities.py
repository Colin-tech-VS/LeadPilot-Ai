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
    # ---- Extension top-150 : villes moyennes à fort volume de recherche ----
    ("roubaix", "Roubaix"),
    ("tourcoing", "Tourcoing"),
    ("saint-denis", "Saint-Denis"),
    ("avignon", "Avignon"),
    ("boulogne-billancourt", "Boulogne-Billancourt"),
    ("saint-paul", "Saint-Paul"),
    ("aulnay-sous-bois", "Aulnay-sous-Bois"),
    ("colombes", "Colombes"),
    ("asnieres-sur-seine", "Asnières-sur-Seine"),
    ("rueil-malmaison", "Rueil-Malmaison"),
    ("aubervilliers", "Aubervilliers"),
    ("champigny-sur-marne", "Champigny-sur-Marne"),
    ("saint-maur-des-fosses", "Saint-Maur-des-Fossés"),
    ("courbevoie", "Courbevoie"),
    ("cannes", "Cannes"),
    ("antibes", "Antibes"),
    ("saint-nazaire", "Saint-Nazaire"),
    ("dunkerque", "Dunkerque"),
    ("poitiers", "Poitiers"),
    ("colmar", "Colmar"),
    ("courbevoie", "Courbevoie"),
    ("fort-de-france", "Fort-de-France"),
    ("crateil", "Créteil"),
    ("creteil", "Créteil"),
    ("pau", "Pau"),
    ("la-rochelle", "La Rochelle"),
    ("calais", "Calais"),
    ("cergy", "Cergy"),
    ("saint-quentin", "Saint-Quentin"),
    ("beziers", "Béziers"),
    ("valence", "Valence"),
    ("merignac", "Mérignac"),
    ("ajaccio", "Ajaccio"),
    ("issy-les-moulineaux", "Issy-les-Moulineaux"),
    ("levallois-perret", "Levallois-Perret"),
    ("quimper", "Quimper"),
    ("noisy-le-grand", "Noisy-le-Grand"),
    ("antony", "Antony"),
    ("neuilly-sur-seine", "Neuilly-sur-Seine"),
    ("sarcelles", "Sarcelles"),
    ("les-sables-d-olonne", "Les Sables-d'Olonne"),
    ("lorient", "Lorient"),
    ("chambery", "Chambéry"),
    ("montauban", "Montauban"),
    ("beauvais", "Beauvais"),
    ("hyeres", "Hyères"),
    ("cholet", "Cholet"),
    ("evry-courcouronnes", "Évry-Courcouronnes"),
    ("saint-pierre", "Saint-Pierre"),
    ("meaux", "Meaux"),
    ("cagnes-sur-mer", "Cagnes-sur-Mer"),
    ("chelles", "Chelles"),
    ("bourges", "Bourges"),
    ("bayonne", "Bayonne"),
    ("frejus", "Fréjus"),
    ("arles", "Arles"),
    ("laval", "Laval"),
    ("clichy", "Clichy"),
    ("vannes", "Vannes"),
    ("evreux", "Évreux"),
    ("clamart", "Clamart"),
    ("annemasse", "Annemasse"),
    ("thionville", "Thionville"),
    ("saint-brieuc", "Saint-Brieuc"),
    ("belfort", "Belfort"),
    ("niort", "Niort"),
    ("le-blanc-mesnil", "Le Blanc-Mesnil"),
    ("montrouge", "Montrouge"),
    ("suresnes", "Suresnes"),
    ("saint-priest", "Saint-Priest"),
    ("puteaux", "Puteaux"),
    ("cherbourg-en-cotentin", "Cherbourg-en-Cotentin"),
    ("saint-malo", "Saint-Malo"),
    ("charleville-mezieres", "Charleville-Mézières"),
    ("meudon", "Meudon"),
    ("noisy-le-sec", "Noisy-le-Sec"),
    ("brive-la-gaillarde", "Brive-la-Gaillarde"),
    ("draguignan", "Draguignan"),
    ("albi", "Albi"),
    ("compiegne", "Compiègne"),
    ("carcassonne", "Carcassonne"),
    ("bastia", "Bastia"),
    ("thonon-les-bains", "Thonon-les-Bains"),
    ("chartres", "Chartres"),
    ("gap", "Gap"),
    ("agen", "Agen"),
    ("angouleme", "Angoulême"),
    ("mont-de-marsan", "Mont-de-Marsan"),
    ("chalon-sur-saone", "Chalon-sur-Saône"),
    ("macon", "Mâcon"),
    ("nevers", "Nevers"),
    ("saint-etienne-du-rouvray", "Saint-Étienne-du-Rouvray"),
    ("le-perreux-sur-marne", "Le Perreux-sur-Marne"),
    ("bagneux", "Bagneux"),
    ("gennevilliers", "Gennevilliers"),
    ("livry-gargan", "Livry-Gargan"),
    ("massy", "Massy"),
    ("epinay-sur-seine", "Épinay-sur-Seine"),
    ("houilles", "Houilles"),
    ("choisy-le-roi", "Choisy-le-Roi"),
    ("garges-les-gonesse", "Garges-lès-Gonesse"),
    ("saint-ouen", "Saint-Ouen-sur-Seine"),
    ("pantin", "Pantin"),
    ("drancy", "Drancy"),
    ("noumea", "Nouméa"),
    ("papeete", "Papeete"),
]

# De-duplicate while preserving order (defensive: extension can accidentally
# repeat an existing slug from the top-40 or elsewhere).
_seen: set[str] = set()
TOP_CITIES = [(s, n) for s, n in TOP_CITIES if not (s in _seen or _seen.add(s))]

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
