"""Signup and settings attach an unclaimed registry listing when identity is clear."""
import uuid
from unittest.mock import patch

from app.core.extensions import db
from app.models.registry_listing import STATUS_CLAIMED, STATUS_LISTED, RegistryListing
from app.models.tenant import Tenant
from app.services import listing_claims, listing_link
from app.services.signup_service import register_plumber


def _listing(**overrides):
    row = RegistryListing(
        siren=overrides.pop("siren", str(uuid.uuid4().int)[:9]),
        siret=overrides.pop("siret", None),
        name=overrides.pop("name", "PLOMBERIE CHAVILLOISE"),
        city_slug=overrides.pop("city_slug", "chaville"),
        city=overrides.pop("city", "Chaville"),
        postal_code=overrides.pop("postal_code", "92370"),
        dept_code=overrides.pop("dept_code", "92"),
        trade_key=overrides.pop("trade_key", "plombier"),
        address=overrides.pop("address", "12 rue de la Gare"),
        status=overrides.pop("status", STATUS_LISTED),
        **overrides,
    )
    db.session.add(row)
    db.session.commit()
    return row


def _signup_data(**overrides):
    data = {
        "company_name": "Plomberie Chavilloise",
        "first_name": "Jean",
        "last_name": "Dupont",
        "email": f"artisan-{uuid.uuid4().hex[:10]}@example.com",
        "phone": "+33601020304",
        "city": "Chaville",
        "trade_type": "plombier",
        "password": "password1",
        "confirm_password": "password1",
    }
    data.update(overrides)
    return data


def test_register_form_includes_siret_field(client):
    html = client.get("/register").get_data(as_text=True)
    assert 'name="siret"' in html
    assert "SIRET ou SIREN" in html


def test_siret_at_signup_attaches_the_listing(client, app):
    with app.app_context():
        listing = _listing(siret="12345678900012", siren="123456789")
        siren = listing.siren

    response = client.post("/register", data=_signup_data(siret="123 456 789 00012"))
    assert response.status_code == 302

    with app.app_context():
        row = RegistryListing.query.filter_by(siren=siren).one()
        assert row.status == STATUS_CLAIMED
        tenant = db.session.get(Tenant, row.claimed_tenant_id)
        assert tenant is not None
        assert tenant.siret == "12345678900012"
        assert tenant.address == "12 rue de la Gare"


def test_siren_at_signup_attaches_the_listing(client, app):
    with app.app_context():
        listing = _listing(siren="987654321", siret="98765432100019")
        siren = listing.siren

    response = client.post("/register", data=_signup_data(siret="987654321"))
    assert response.status_code == 302

    with app.app_context():
        row = RegistryListing.query.filter_by(siren=siren).one()
        assert row.status == STATUS_CLAIMED
        tenant = db.session.get(Tenant, row.claimed_tenant_id)
        assert tenant.siret == "98765432100019"


def test_name_match_asks_before_creating_the_account(client, app):
    with app.app_context():
        listing = _listing()
        siren = listing.siren

    data = _signup_data()
    response = client.post("/register", data=data)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Une fiche correspond déjà" in html
    assert siren in html

    with app.app_context():
        from app.models.user import User

        assert User.query.filter_by(email=data["email"]).first() is None
        assert RegistryListing.query.filter_by(siren=siren).one().status == STATUS_LISTED


def test_confirmed_name_match_attaches(client, app):
    with app.app_context():
        listing = _listing()
        siren = listing.siren

    response = client.post("/register", data=_signup_data(claim_siren=siren))
    assert response.status_code == 302

    with app.app_context():
        row = RegistryListing.query.filter_by(siren=siren).one()
        assert row.status == STATUS_CLAIMED


def test_skipped_name_match_leaves_the_listing(client, app):
    with app.app_context():
        listing = _listing()
        siren = listing.siren

    data = _signup_data(claim_siren="skip")
    response = client.post("/register", data=data)
    assert response.status_code == 302

    with app.app_context():
        from app.models.user import User

        assert RegistryListing.query.filter_by(siren=siren).one().status == STATUS_LISTED
        assert User.query.filter_by(email=data["email"]).first() is not None


def test_invalid_siret_does_not_register(client, app):
    data = _signup_data(siret="12345")
    response = client.post("/register", data=data)
    assert response.status_code == 200
    assert "14 chiffres" in response.get_data(as_text=True)
    with app.app_context():
        from app.models.user import User

        assert User.query.filter_by(email=data["email"]).first() is None


def test_already_claimed_listing_is_not_stolen(app):
    with app.app_context():
        listing = _listing(siren="111222333")
        user, owner = register_plumber(
            email=f"owner-{uuid.uuid4().hex[:8]}@example.com",
            password="password1",
            company_name="Owner",
            city="Chaville",
        )
        listing_claims.attach(listing.siren, owner.id)
        other = Tenant(name="Intrus", trade_type="plombier", city="Chaville")
        db.session.add(other)
        db.session.commit()
        other_id = other.id
        owner_id = owner.id

        assert listing_claims.attach(listing.siren, other_id) is None
        row = RegistryListing.query.filter_by(siren="111222333").one()
        assert row.claimed_tenant_id == owner_id


def test_settings_siret_attaches_existing_listing(client, app):
    with app.app_context():
        listing = _listing(siret="55566677700018", siren="555666777")
        user, tenant = register_plumber(
            email=f"later-{uuid.uuid4().hex[:8]}@example.com",
            password="password1",
            company_name="Atelier Dupont",
            city="Chaville",
            trade_type="plombier",
        )
        user_id = str(user.id)
        tenant_id = tenant.id
        email = user.email
        siren = listing.siren

    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess["tenant_id"] = str(tenant_id)
        sess["role"] = "admin"

    with patch("app.utils.geocoding.geocode_address", return_value=None):
        response = client.post(
            "/settings",
            data={
                "name": "Atelier Dupont",
                "email": email,
                "city": "Chaville",
                "trade_type": "plombier",
                "is_public": "on",
                "siret": "55566677700018",
                "service_radius_km": "30",
            },
        )
    assert response.status_code == 200
    assert "fiche annuaire" in response.get_data(as_text=True)

    with app.app_context():
        row = RegistryListing.query.filter_by(siren=siren).one()
        assert row.status == STATUS_CLAIMED
        assert row.claimed_tenant_id == tenant_id


def test_suggest_requires_city_and_ignores_other_trades(app):
    with app.app_context():
        _listing(name="PLOMBERIE MARTIN", city_slug="chaville", city="Chaville", trade_key="plombier")
        _listing(
            name="PLOMBERIE MARTIN",
            city_slug="dax",
            city="Dax",
            trade_key="plombier",
            postal_code="40100",
        )
        hits = listing_link.suggest_listings(name="Plomberie Martin", city="Chaville", trade="plombier")
        assert len(hits) == 1
        assert hits[0].city_slug == "chaville"
        assert listing_link.suggest_listings(name="Plomberie Martin", city="Chaville", trade="serrurier") == []
        assert listing_link.suggest_listings(name="Plomberie Martin", city="", trade="plombier") == []
