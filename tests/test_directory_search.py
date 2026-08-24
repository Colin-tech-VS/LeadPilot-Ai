"""Free-text directory search must never answer with unrelated towns."""
import uuid

import pytest

from app.core.extensions import db
from app.models.registry_listing import STATUS_LISTED, RegistryListing


@pytest.fixture
def listings(app):
    """A Chaville business plus decoys in towns that must never match it."""
    with app.app_context():
        RegistryListing.query.delete()
        rows = [
            ("Plomberie Chavilloise", "chaville", "Chaville", "92370", "92", "plombier", "1998-01-01"),
            ("Serrurerie de Dax", "dax", "Dax", "40100", "40", "serrurier", "1990-01-01"),
            ("Toiture Pontlevoy", "pontlevoy", "Pontlevoy", "41400", "41", "couvreur", "1985-01-01"),
            ("Elec Dunieres", "dunieres", "Dunières", "43220", "43", "electricien", "1980-01-01"),
            ("Maçonnerie Troyes", "troyes", "Troyes", "10000", "10", "macon", "1975-01-01"),
        ]
        for name, slug, city, cp, dept, trade, created in rows:
            db.session.add(
                RegistryListing(
                    siren=str(uuid.uuid4().int)[:9],
                    name=name,
                    city_slug=slug,
                    city=city,
                    postal_code=cp,
                    dept_code=dept,
                    trade_key=trade,
                    date_creation=created,
                    status=STATUS_LISTED,
                )
            )
        db.session.commit()
    yield
    with app.app_context():
        RegistryListing.query.delete()
        db.session.commit()


def _cities(payload):
    return sorted(r["city"] for r in payload["registry"])


def test_free_text_town_returns_only_that_town(client, listings):
    """Regression: ``q`` was never passed to the registry query, so an unmatched
    search fell through to no filter at all and returned the oldest twelve rows.
    Searching « chaville » answered with Dax, Pontlevoy and Troyes."""
    payload = client.get("/api/public/artisans/search?q=chaville").get_json()
    assert _cities(payload) == ["Chaville"]


def test_free_text_matches_a_business_name(client, listings):
    payload = client.get("/api/public/artisans/search?q=Serrurerie").get_json()
    assert _cities(payload) == ["Dax"]


def test_free_text_matches_a_postcode(client, listings):
    payload = client.get("/api/public/artisans/search?q=92370").get_json()
    assert _cities(payload) == ["Chaville"]


def test_unaccented_query_matches_an_accented_town(client, listings):
    """« dunieres » must find « Dunières » — nobody types the accent."""
    payload = client.get("/api/public/artisans/search?q=dunieres").get_json()
    assert _cities(payload) == ["Dunières"]


def test_no_match_returns_nothing_rather_than_filler(client, listings):
    """An empty result is honest; a list of unrelated towns looks like an answer."""
    payload = client.get("/api/public/artisans/search?q=zzzznowhere").get_json()
    assert payload["registry"] == []


def test_rendered_directory_page_honours_the_query(client, listings):
    """The server-rendered page had the same bug as the JSON endpoint."""
    html = client.get("/artisans?q=chaville").data.decode()
    assert "Chavilloise" in html
    for absent in ("Dax", "Pontlevoy", "Troyes"):
        assert absent not in html, f"{absent} shown for a Chaville search"


def test_trade_and_city_filters_still_work(client, listings):
    """The q filter must not disturb the existing trade/town path."""
    payload = client.get("/api/public/artisans/search?metier=serrurier&ville=dax").get_json()
    assert _cities(payload) == ["Dax"]
