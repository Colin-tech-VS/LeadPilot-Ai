"""French departments (métropole + DROM) for programmatic local SEO.

Powers `/artisans/<trade>/departement/<code>` pages so search intent like
« plombier haute-savoie 74 » or « électricien départemental 06 » ranks on a
dedicated, self-canonical URL with the whole department's slug, chef-lieu,
and neighbouring cities pre-linked.
"""
from __future__ import annotations

# (code, slug, display_name, chef_lieu)
DEPARTMENTS: list[tuple[str, str, str, str]] = [
    ("01", "ain", "Ain", "Bourg-en-Bresse"),
    ("02", "aisne", "Aisne", "Laon"),
    ("03", "allier", "Allier", "Moulins"),
    ("04", "alpes-de-haute-provence", "Alpes-de-Haute-Provence", "Digne-les-Bains"),
    ("05", "hautes-alpes", "Hautes-Alpes", "Gap"),
    ("06", "alpes-maritimes", "Alpes-Maritimes", "Nice"),
    ("07", "ardeche", "Ardèche", "Privas"),
    ("08", "ardennes", "Ardennes", "Charleville-Mézières"),
    ("09", "ariege", "Ariège", "Foix"),
    ("10", "aube", "Aube", "Troyes"),
    ("11", "aude", "Aude", "Carcassonne"),
    ("12", "aveyron", "Aveyron", "Rodez"),
    ("13", "bouches-du-rhone", "Bouches-du-Rhône", "Marseille"),
    ("14", "calvados", "Calvados", "Caen"),
    ("15", "cantal", "Cantal", "Aurillac"),
    ("16", "charente", "Charente", "Angoulême"),
    ("17", "charente-maritime", "Charente-Maritime", "La Rochelle"),
    ("18", "cher", "Cher", "Bourges"),
    ("19", "correze", "Corrèze", "Tulle"),
    ("21", "cote-d-or", "Côte-d'Or", "Dijon"),
    ("22", "cotes-d-armor", "Côtes-d'Armor", "Saint-Brieuc"),
    ("23", "creuse", "Creuse", "Guéret"),
    ("24", "dordogne", "Dordogne", "Périgueux"),
    ("25", "doubs", "Doubs", "Besançon"),
    ("26", "drome", "Drôme", "Valence"),
    ("27", "eure", "Eure", "Évreux"),
    ("28", "eure-et-loir", "Eure-et-Loir", "Chartres"),
    ("29", "finistere", "Finistère", "Quimper"),
    ("2a", "corse-du-sud", "Corse-du-Sud", "Ajaccio"),
    ("2b", "haute-corse", "Haute-Corse", "Bastia"),
    ("30", "gard", "Gard", "Nîmes"),
    ("31", "haute-garonne", "Haute-Garonne", "Toulouse"),
    ("32", "gers", "Gers", "Auch"),
    ("33", "gironde", "Gironde", "Bordeaux"),
    ("34", "herault", "Hérault", "Montpellier"),
    ("35", "ille-et-vilaine", "Ille-et-Vilaine", "Rennes"),
    ("36", "indre", "Indre", "Châteauroux"),
    ("37", "indre-et-loire", "Indre-et-Loire", "Tours"),
    ("38", "isere", "Isère", "Grenoble"),
    ("39", "jura", "Jura", "Lons-le-Saunier"),
    ("40", "landes", "Landes", "Mont-de-Marsan"),
    ("41", "loir-et-cher", "Loir-et-Cher", "Blois"),
    ("42", "loire", "Loire", "Saint-Étienne"),
    ("43", "haute-loire", "Haute-Loire", "Le Puy-en-Velay"),
    ("44", "loire-atlantique", "Loire-Atlantique", "Nantes"),
    ("45", "loiret", "Loiret", "Orléans"),
    ("46", "lot", "Lot", "Cahors"),
    ("47", "lot-et-garonne", "Lot-et-Garonne", "Agen"),
    ("48", "lozere", "Lozère", "Mende"),
    ("49", "maine-et-loire", "Maine-et-Loire", "Angers"),
    ("50", "manche", "Manche", "Saint-Lô"),
    ("51", "marne", "Marne", "Châlons-en-Champagne"),
    ("52", "haute-marne", "Haute-Marne", "Chaumont"),
    ("53", "mayenne", "Mayenne", "Laval"),
    ("54", "meurthe-et-moselle", "Meurthe-et-Moselle", "Nancy"),
    ("55", "meuse", "Meuse", "Bar-le-Duc"),
    ("56", "morbihan", "Morbihan", "Vannes"),
    ("57", "moselle", "Moselle", "Metz"),
    ("58", "nievre", "Nièvre", "Nevers"),
    ("59", "nord", "Nord", "Lille"),
    ("60", "oise", "Oise", "Beauvais"),
    ("61", "orne", "Orne", "Alençon"),
    ("62", "pas-de-calais", "Pas-de-Calais", "Arras"),
    ("63", "puy-de-dome", "Puy-de-Dôme", "Clermont-Ferrand"),
    ("64", "pyrenees-atlantiques", "Pyrénées-Atlantiques", "Pau"),
    ("65", "hautes-pyrenees", "Hautes-Pyrénées", "Tarbes"),
    ("66", "pyrenees-orientales", "Pyrénées-Orientales", "Perpignan"),
    ("67", "bas-rhin", "Bas-Rhin", "Strasbourg"),
    ("68", "haut-rhin", "Haut-Rhin", "Colmar"),
    ("69", "rhone", "Rhône", "Lyon"),
    ("70", "haute-saone", "Haute-Saône", "Vesoul"),
    ("71", "saone-et-loire", "Saône-et-Loire", "Mâcon"),
    ("72", "sarthe", "Sarthe", "Le Mans"),
    ("73", "savoie", "Savoie", "Chambéry"),
    ("74", "haute-savoie", "Haute-Savoie", "Annecy"),
    ("75", "paris", "Paris", "Paris"),
    ("76", "seine-maritime", "Seine-Maritime", "Rouen"),
    ("77", "seine-et-marne", "Seine-et-Marne", "Melun"),
    ("78", "yvelines", "Yvelines", "Versailles"),
    ("79", "deux-sevres", "Deux-Sèvres", "Niort"),
    ("80", "somme", "Somme", "Amiens"),
    ("81", "tarn", "Tarn", "Albi"),
    ("82", "tarn-et-garonne", "Tarn-et-Garonne", "Montauban"),
    ("83", "var", "Var", "Toulon"),
    ("84", "vaucluse", "Vaucluse", "Avignon"),
    ("85", "vendee", "Vendée", "La Roche-sur-Yon"),
    ("86", "vienne", "Vienne", "Poitiers"),
    ("87", "haute-vienne", "Haute-Vienne", "Limoges"),
    ("88", "vosges", "Vosges", "Épinal"),
    ("89", "yonne", "Yonne", "Auxerre"),
    ("90", "territoire-de-belfort", "Territoire de Belfort", "Belfort"),
    ("91", "essonne", "Essonne", "Évry-Courcouronnes"),
    ("92", "hauts-de-seine", "Hauts-de-Seine", "Nanterre"),
    ("93", "seine-saint-denis", "Seine-Saint-Denis", "Bobigny"),
    ("94", "val-de-marne", "Val-de-Marne", "Créteil"),
    ("95", "val-d-oise", "Val-d'Oise", "Cergy"),
    ("971", "guadeloupe", "Guadeloupe", "Basse-Terre"),
    ("972", "martinique", "Martinique", "Fort-de-France"),
    ("973", "guyane", "Guyane", "Cayenne"),
    ("974", "la-reunion", "La Réunion", "Saint-Denis"),
    ("976", "mayotte", "Mayotte", "Mamoudzou"),
]

_BY_CODE: dict[str, tuple[str, str, str]] = {code: (slug, name, chef) for code, slug, name, chef in DEPARTMENTS}
_BY_SLUG: dict[str, tuple[str, str, str]] = {slug: (code, name, chef) for code, slug, name, chef in DEPARTMENTS}


def is_known_department(key: str) -> bool:
    k = (key or "").strip().lower()
    return k in _BY_CODE or k in _BY_SLUG


def department_postal_prefix(code: str) -> str:
    """Postal-code prefix for a department code.

    Corsica is the exception that a naive ``code[:2]`` gets wrong: the
    department codes are ``2A``/``2B`` but every Corsican postal code starts
    with ``20``. DROM codes are already three digits and match directly.
    """
    c = (code or "").strip().lower()
    if c in ("2a", "2b"):
        return "20"
    return c.upper()


def department_info(key: str) -> tuple[str, str, str, str] | None:
    """Resolve either a code (e.g. '75', '2a', '974') or a slug to
    ``(code, slug, display_name, chef_lieu)``. Returns None if unknown."""
    k = (key or "").strip().lower()
    if k in _BY_CODE:
        slug, name, chef = _BY_CODE[k]
        return (k, slug, name, chef)
    if k in _BY_SLUG:
        code, name, chef = _BY_SLUG[k]
        return (code, k, name, chef)
    return None
