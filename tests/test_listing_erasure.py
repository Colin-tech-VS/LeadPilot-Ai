"""Erasure of a registry listing, under GDPR articles 17 and 21.

Most of these entries name a sole trader, so the company name *is* a person's
name. When that person asks to be removed — and especially once INSEE has
flipped them to non-diffusion — the listing has to disappear from every surface
and stay gone, including through the next import.

A partial erasure is a failed erasure, so these check every read path rather
than just the page.
"""
import uuid

import pytest

from app.core.extensions import db
from app.models.registry_listing import (
    STATUS_LISTED,
    STATUS_OPTED_OUT,
    RegistryListing,
)
from app.services import listing_claims


@pytest.fixture
def erased(app):
    """A listing that named someone, and the erasure they asked for."""
    with app.app_context():
        siren = str(uuid.uuid4().int)[:9]
        db.session.add(
            RegistryListing(
                siren=siren,
                siret=siren + "00019",
                name="JEAN TESTEUR",
                trade_key="carreleur",
                city="Compiègne",
                city_slug="compiegne",
                postal_code="60200",
                dept_code="60",
                status=STATUS_LISTED,
            )
        )
        db.session.commit()
        listing_claims.opt_out(siren, reason="RGPD art.17 — test")
        return siren


def test_the_page_answers_gone_not_missing(client, erased):
    """404 invites a re-crawl for weeks; 410 is dropped on the next visit."""
    response = client.get(f"/artisans/entreprise/{erased}")
    assert response.status_code == 410
    assert "noindex" in response.headers.get("X-Robots-Tag", "")


def test_the_page_no_longer_carries_the_name(client, erased):
    body = client.get(f"/artisans/entreprise/{erased}").get_data(as_text=True)
    assert "TESTEUR" not in body.upper()


def test_an_unknown_siren_is_indistinguishable_from_an_erased_one(client, erased):
    """Otherwise this URL becomes a way of probing who asked to be delisted."""
    known = client.get(f"/artisans/entreprise/{erased}")
    unknown = client.get("/artisans/entreprise/999999999")
    assert known.status_code == unknown.status_code == 410
    assert known.get_data() == unknown.get_data()


def test_it_leaves_the_sitemap(client, app, erased):
    body = client.get("/sitemap-entreprises.xml").get_data(as_text=True)
    assert erased not in body


def test_it_is_no_longer_offered_at_signup(app, erased):
    """The claim suggestions on /register read the same table."""
    from app.services import listing_link

    with app.app_context():
        listing, suggestions, _ = listing_link.resolve_signup_listing(
            siret="", claim_siren="", name="JEAN TESTEUR", city="Compiègne", trade="carreleur"
        )
        assert listing is None
        assert not any(s.siren == erased for s in suggestions or [])


def test_a_later_import_cannot_bring_it_back(app, erased):
    """The whole point of a tombstone: the person asked once, not once per import.

    Driven through the real ingestion path with the registry feed stubbed, so
    the guard is exercised where it actually runs.
    """
    from unittest.mock import patch

    from app.services import registry_import

    record = {
        "siren": erased,
        "nom_complet": "JEAN TESTEUR",
        "nom_raison_sociale": "JEAN TESTEUR",
        "siege": {
            "siret": erased + "00019",
            "libelle_commune": "Compiègne",
            "code_postal": "60200",
            "activite_principale": "43.33Z",
        },
        "etat_administratif": "A",
        "statut_diffusion": "O",
    }

    with app.app_context():
        with patch.object(registry_import, "iter_registry", return_value=iter([record])):
            result = registry_import.import_trade("carreleur", postal_code="60200")

        after = RegistryListing.query.filter_by(siren=erased).one()
        assert after.status == STATUS_OPTED_OUT, "an import resurrected an erased listing"
        assert after.name == "JEAN TESTEUR"  # untouched tombstone, not overwritten
        assert result["skipped"] >= 1


def test_the_name_search_no_longer_finds_it(app, erased):
    """« Est-ce que mon entreprise est déjà listée ? » reads the same table."""
    from app.services import registry_import

    with app.app_context():
        assert not registry_import.find_by_name("JEAN TESTEUR")
        assert not registry_import.find_by_name(erased)
        assert not any(
            row.siren == erased
            for row in registry_import.search_listings(trade_key="carreleur", q="testeur")
        )


def test_erasing_an_unknown_siren_still_leaves_a_tombstone(app):
    """Someone can ask to be removed before we ever import them."""
    with app.app_context():
        siren = str(uuid.uuid4().int)[:9]
        listing_claims.opt_out(siren, reason="RGPD — jamais importé")
        row = RegistryListing.query.filter_by(siren=siren).one()
        assert row.status == STATUS_OPTED_OUT


def test_erasure_touches_only_the_person_who_asked(app):
    """« Zurek » matched seven listings in production and six were other people.

    A name search is a starting point for a human, never an instruction to the
    code.
    """
    with app.app_context():
        mine = str(uuid.uuid4().int)[:9]
        neighbour = str(uuid.uuid4().int)[:9]
        for siren, name in ((mine, "MIROSLAW TESTEUR"), (neighbour, "GREGORY TESTEUR")):
            db.session.add(
                RegistryListing(
                    siren=siren, name=name, trade_key="carreleur", status=STATUS_LISTED
                )
            )
        db.session.commit()

        listing_claims.opt_out(mine, reason="RGPD")

        assert RegistryListing.query.filter_by(siren=mine).one().status == STATUS_OPTED_OUT
        assert RegistryListing.query.filter_by(siren=neighbour).one().status == STATUS_LISTED
