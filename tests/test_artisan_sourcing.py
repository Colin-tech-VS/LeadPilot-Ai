"""Bulk artisan sourcing from the ADEME RGE open register."""
import uuid
from unittest.mock import patch

from app.core.extensions import db
from app.models.outreach_prospect import OutreachProspect
from app.services import artisan_sourcing


def _row(**kwargs):
    base = {
        "siret": "12345678900011",
        "nom_entreprise": "DUPONT PLOMBERIE",
        "email": "contact@dupont-plomberie.fr",
        "telephone": "04 78 00 00 00",
        "site_internet": "https://dupont-plomberie.fr",
        "adresse": "1 RUE DES LILAS",
        "code_postal": "69003",
        "commune": "LYON",
        "domaine": "Pompe à chaleur : chauffage",
        "particulier": True,
        "lien_date_fin": "2099-01-01",
    }
    base.update(kwargs)
    return base


def _login_admin(client):
    with client.session_transaction() as sess:
        sess["admin_authenticated"] = True
        sess["admin_username"] = "admin"


def test_query_asks_only_for_records_with_an_email(app):
    query = artisan_sourcing._build_query(trades=None, departments=None)
    assert "email:*" in query
    assert "particulier:true" in query
    # Study and architecture domains are not artisans and must not be requested.
    assert "Architecte" not in query
    assert "Etude thermique" not in query


def test_query_excludes_companies_outside_france(app):
    query = artisan_sourcing._build_query(trades=None, departments=None)
    assert 'NOT code_postal:"00000"' in query


def test_foreign_records_are_dropped_even_if_the_query_lets_them_through(app):
    rows = [
        _row(code_postal="00000", commune="TOURNAI",
             email=f"be-{uuid.uuid4().hex[:6]}@x.be", siret="10100011100011"),
        _row(code_postal="", commune="BRAGA",
             email=f"pt-{uuid.uuid4().hex[:6]}@x.pt", siret="10100011100012"),
    ]
    with patch("app.services.artisan_sourcing._iter_rows", return_value=iter(rows)):
        result = artisan_sourcing.source_artisans(target=10)

    assert result["imported"] == 0
    assert result["skipped"]["foreign"] == 2


def test_french_postal_codes_including_overseas_are_kept(app):
    for postal in ("69003", "20000", "97400", "01000"):
        assert artisan_sourcing._is_french({"code_postal": postal}), postal
    for postal in ("00000", "", "9999", "abcde"):
        assert not artisan_sourcing._is_french({"code_postal": postal}), postal


def test_query_scopes_to_the_requested_departments(app):
    query = artisan_sourcing._build_query(trades=["chauffagiste"], departments=["69", "2A"])
    assert "code_postal:(69* OR 2A*)" in query
    assert "Pompe à chaleur : chauffage" in query


def test_import_persists_prospects_with_a_mapped_trade(app):
    email = f"contact-{uuid.uuid4().hex[:8]}@artisan-test.fr"
    rows = [_row(email=email, siret="99900011100011")]
    with patch("app.services.artisan_sourcing._iter_rows", return_value=iter(rows)):
        result = artisan_sourcing.source_artisans(target=10)

    assert result["imported"] == 1
    prospect = OutreachProspect.query.filter_by(email=email).one()
    assert prospect.trade_type == "chauffagiste"
    assert prospect.source == "rge_ademe"
    assert prospect.status == "ready"
    assert prospect.city == "Lyon"          # the register shouts; we don't
    assert prospect.phone == "0478000000"
    assert "SIRET" in prospect.notes


def test_records_without_an_email_are_never_stored(app):
    rows = [_row(email="", siret="88800011100011"), _row(email=None, siret="88800011100012")]
    with patch("app.services.artisan_sourcing._iter_rows", return_value=iter(rows)):
        result = artisan_sourcing.source_artisans(target=10)

    assert result["imported"] == 0
    assert result["skipped"]["no_email"] == 2


def test_non_artisan_domains_are_dropped(app):
    rows = [
        _row(domaine="Architecte", email=f"a-{uuid.uuid4().hex[:6]}@x.fr", siret="77700011100011"),
        _row(domaine="Etude thermique reglementaire", email=f"b-{uuid.uuid4().hex[:6]}@x.fr",
             siret="77700011100012"),
    ]
    with patch("app.services.artisan_sourcing._iter_rows", return_value=iter(rows)):
        result = artisan_sourcing.source_artisans(target=10)

    assert result["imported"] == 0
    assert result["skipped"]["off_trade"] == 2


def test_expired_certifications_are_skipped(app):
    rows = [_row(lien_date_fin="2001-01-01", email=f"old-{uuid.uuid4().hex[:6]}@x.fr",
                 siret="66600011100011")]
    with patch("app.services.artisan_sourcing._iter_rows", return_value=iter(rows)):
        result = artisan_sourcing.source_artisans(target=10)

    assert result["imported"] == 0
    assert result["skipped"]["expired"] == 1


def test_undeliverable_addresses_never_enter_the_sending_pool(app):
    rows = [_row(email="logo@2x.png", siret="55500011100011")]
    with patch("app.services.artisan_sourcing._iter_rows", return_value=iter(rows)):
        result = artisan_sourcing.source_artisans(target=10)

    assert result["imported"] == 0
    assert result["skipped"]["invalid_email"] == 1


def test_the_same_company_is_imported_once_across_its_qualifications(app):
    """One RGE company holds several certifications — one row each, one prospect."""
    email = f"multi-{uuid.uuid4().hex[:8]}@artisan-test.fr"
    rows = [
        _row(email=email, siret="44400011100011", domaine="Pompe à chaleur : chauffage"),
        _row(email=email, siret="44400011100011", domaine="Chaudière bois"),
        _row(email=email, siret="44400011100011", domaine="Ventilation mécanique"),
    ]
    with patch("app.services.artisan_sourcing._iter_rows", return_value=iter(rows)):
        result = artisan_sourcing.source_artisans(target=10)

    assert result["imported"] == 1
    assert result["skipped"]["duplicate"] == 2
    assert OutreachProspect.query.filter_by(email=email).count() == 1


def test_an_address_already_known_is_not_sourced_again(app):
    email = f"known-{uuid.uuid4().hex[:8]}@artisan-test.fr"
    db.session.add(OutreachProspect(email=email, trade_type="plombier", source="web_search"))
    db.session.commit()

    rows = [_row(email=email, siret="33300011100011")]
    with patch("app.services.artisan_sourcing._iter_rows", return_value=iter(rows)):
        result = artisan_sourcing.source_artisans(target=10)

    assert result["imported"] == 0
    assert result["skipped"]["duplicate"] == 1


def test_import_stops_at_the_requested_target(app):
    rows = [
        _row(email=f"t{i}-{uuid.uuid4().hex[:6]}@artisan-test.fr", siret=f"2220001110{i:04d}")
        for i in range(10)
    ]
    with patch("app.services.artisan_sourcing._iter_rows", return_value=iter(rows)):
        result = artisan_sourcing.source_artisans(target=3)

    assert result["imported"] == 3


def test_generic_mailboxes_are_flagged_as_lower_confidence(app):
    assert artisan_sourcing._confidence("contact@artisan.fr") == "medium"
    assert artisan_sourcing._confidence("accueil2@artisan.fr") == "medium"
    assert artisan_sourcing._confidence("julien.dupont@artisan.fr") == "high"


def test_admin_import_endpoint_reports_the_run(app, client):
    _login_admin(client)
    rows = [_row(email=f"api-{uuid.uuid4().hex[:8]}@artisan-test.fr", siret="11100011100011")]
    with patch("app.services.artisan_sourcing._iter_rows", return_value=iter(rows)):
        response = client.post(
            "/admin/api/prospecting/import-rge",
            json={"trades": ["chauffagiste"], "departments": ["69"], "target": 5},
        )

    assert response.status_code == 200
    data = response.get_json()
    assert data["imported"] == 1
    assert data["by_trade"]["chauffagiste"] == 1


def test_import_endpoint_requires_admin(client):
    response = client.post("/admin/api/prospecting/import-rge", json={"target": 5})
    assert response.status_code in (302, 401, 403)
