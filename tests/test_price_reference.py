"""Price grid parsing — the data behind /prix-artisans and the JSON feed.

The grids are written by a language model, so their HTML shape varies between
regenerations. These cases are the shapes actually observed in production.
"""
from app.services.price_reference import parse_price_rows


def test_currency_in_each_cell():
    html = """<table><caption>Tarifs</caption>
    <tr><th>Type</th><th>Prix</th></tr>
    <tr><td>Ouverture de porte</td><td>80 € – 150 €</td></tr></table>"""
    assert parse_price_rows(html) == [
        {"label": "Ouverture de porte", "min_eur": 80, "max_eur": 150}
    ]


def test_currency_only_in_header_with_bare_numbers():
    """Half the generated grids put € in the column header and leave the cells
    as bare numbers — this shape silently produced zero rows before."""
    html = """<table><caption>Fourchettes TTC</caption>
    <tr><th>Type d'intervention</th><th>Prix TTC (€)</th></tr>
    <tr><td>Débouchage évier</td><td>70 – 150</td></tr>
    <tr><td>Remplacement robinet</td><td>100 – 300</td></tr></table>"""
    assert parse_price_rows(html) == [
        {"label": "Débouchage évier", "min_eur": 70, "max_eur": 150},
        {"label": "Remplacement robinet", "min_eur": 100, "max_eur": 300},
    ]


def test_thead_tbody_and_thousands_separator():
    html = """<table><caption>Prix TTC</caption>
    <thead><tr><th>Type</th><th>Prix TTC (€)</th></tr></thead>
    <tbody><tr><td>Tableau électrique</td><td>800 – 2 500</td></tr></tbody></table>"""
    assert parse_price_rows(html) == [
        {"label": "Tableau électrique", "min_eur": 800, "max_eur": 2500}
    ]


def test_open_ended_price_becomes_a_point():
    html = """<table><caption>Tarifs (€)</caption>
    <tr><th>Acte</th><th>Prix</th></tr>
    <tr><td>Diagnostic</td><td>à partir de 90</td></tr></table>"""
    assert parse_price_rows(html) == [
        {"label": "Diagnostic", "min_eur": 90, "max_eur": 90}
    ]


def test_non_monetary_table_is_ignored():
    """Bare numbers are only read as prices when the table says it holds money,
    otherwise a surface area or a year would be published as a tariff."""
    html = """<table><caption>Surfaces des pièces</caption>
    <tr><th>Pièce</th><th>Surface m2</th></tr>
    <tr><td>Salon</td><td>20 – 35</td></tr></table>"""
    assert parse_price_rows(html) == []


def test_rows_without_an_amount_are_skipped():
    html = """<table><caption>Prix (€)</caption>
    <tr><th>Acte</th><th>Prix</th></tr>
    <tr><td>Devis</td><td>variable</td></tr>
    <tr><td>Pose</td><td>50 – 90</td></tr></table>"""
    assert parse_price_rows(html) == [{"label": "Pose", "min_eur": 50, "max_eur": 90}]


def test_reversed_range_is_normalised():
    html = """<table><caption>Prix (€)</caption>
    <tr><th>Acte</th><th>Prix</th></tr>
    <tr><td>Intervention</td><td>150 € – 80 €</td></tr></table>"""
    assert parse_price_rows(html) == [
        {"label": "Intervention", "min_eur": 80, "max_eur": 150}
    ]


def test_empty_input_is_safe():
    assert parse_price_rows(None) == []
    assert parse_price_rows("") == []
    assert parse_price_rows("<p>pas de tableau</p>") == []
