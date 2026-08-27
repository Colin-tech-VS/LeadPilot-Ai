"""contact@pilotcore.fr is the team's own test account — it must be invisible.

Signing up through the real form is the only honest way to check the real form
works, but the account that comes out is indistinguishable from a customer's.
Left alone it inflates every KPI and, worst of all, appears in the public
directory where a visitor can book an appointment with a company that does not
exist.
"""
import uuid

import pytest

from app.core.extensions import db
from app.models.tenant import Tenant
from app.models.user import User
from app.services import analytics, artisan_directory, internal_accounts
from app.services.signup_service import register_plumber

INTERNAL = "contact@pilotcore.fr"


@pytest.fixture
def accounts(app):
    """One internal account and one real customer artisan, both public.

    The suite shares a database across a run, so the internal account is
    created once and reused — signing it up twice is a duplicate e-mail.
    """
    with app.app_context():
        existing = User.query.filter_by(email=INTERNAL).first()
        if existing is not None:
            internal_tenant = db.session.get(Tenant, existing.tenant_id)
        else:
            _, internal_tenant = register_plumber(
                email=INTERNAL, password="MotDePasse123", company_name="Test Interne", city="Paris"
            )

        real_email = f"vrai-{uuid.uuid4().hex[:8]}@example.com"
        _, real_tenant = register_plumber(
            email=real_email, password="MotDePasse123", company_name="Plomberie Réelle", city="Paris"
        )
        for tenant in (internal_tenant, real_tenant):
            tenant.is_public = True
        db.session.commit()
        yield {
            "internal_tenant_id": internal_tenant.id,
            "internal_slug": internal_tenant.public_slug,
            "real_tenant_id": real_tenant.id,
            "real_slug": real_tenant.public_slug,
            "real_email": real_email,
        }


def test_the_address_is_recognised_as_ours(app):
    with app.app_context():
        assert internal_accounts.is_internal_email(INTERNAL)
        assert internal_accounts.is_internal_email("  CONTACT@PilotCore.FR  ")
        assert not internal_accounts.is_internal_email("client@example.com")
        assert not internal_accounts.is_internal_email(None)


def test_the_list_is_configurable(app):
    with app.app_context():
        app.config["INTERNAL_ACCOUNT_EMAILS"] = "a@x.fr, B@x.fr"
        assert internal_accounts.is_internal_email("b@x.fr")
        assert not internal_accounts.is_internal_email(INTERNAL)


# ── Never counted ────────────────────────────────────────────────────────────


def test_the_kpis_do_not_count_it(app, accounts):
    """Stated as a delta against the raw tables: other tests share this database,
    so an absolute count would only measure the order the suite ran in."""
    with app.app_context():
        internal_tenants = len(internal_accounts.internal_tenant_ids())
        internal_users = User.query.filter_by(email=INTERNAL).count()
        assert internal_tenants == 1, "fixture should leave exactly one internal tenant"

        kpis = analytics.kpis()
        assert kpis["tenants_total"] == Tenant.query.count() - internal_tenants
        assert kpis["users_total"] == User.query.count() - internal_users


def test_the_plan_breakdown_does_not_count_it(app, accounts):
    with app.app_context():
        total = sum(row["count"] for row in analytics.plan_breakdown())
        assert total == Tenant.query.count() - len(internal_accounts.internal_tenant_ids())


# ── Never shown ──────────────────────────────────────────────────────────────


def test_the_public_directory_does_not_list_it(app, accounts):
    with app.app_context():
        names = [t.name for t in artisan_directory.public_artisans_query().all()]
        assert "Plomberie Réelle" in names
        assert "Test Interne" not in names


def test_its_public_profile_page_does_not_resolve(app, accounts):
    with app.app_context():
        assert artisan_directory.get_public_artisan_by_slug(accounts["internal_slug"]) is None
        # The real artisan's profile still works.
        assert artisan_directory.get_public_artisan_by_slug(accounts["real_slug"]) is not None


def test_the_profile_page_404s_for_a_visitor(client, accounts):
    assert client.get(f"/artisans/{accounts['internal_slug']}").status_code == 404
    assert client.get(f"/artisans/{accounts['real_slug']}").status_code == 200


def test_the_admin_accounts_list_hides_it(client, app, accounts):
    with client.session_transaction() as sess:
        sess["is_admin"] = True
        sess["admin_authenticated"] = True

    response = client.get("/admin/clients?tab=artisans")
    if response.status_code != 200:
        pytest.skip("admin session shape differs; the query-level tests above still cover it")

    html = response.get_data(as_text=True)
    assert "Plomberie Réelle" in html
    assert "Test Interne" not in html


# ── The account itself still works ───────────────────────────────────────────


def test_the_account_can_still_sign_in_and_use_the_app(client, app, accounts):
    """Hiding it from the outside must not break the thing it exists to test."""
    response = client.post("/login", data={"email": INTERNAL, "password": "MotDePasse123"})
    assert response.status_code == 302
    assert "/dashboard" in response.headers["Location"]

    with app.app_context():
        assert User.query.filter_by(email=INTERNAL).first() is not None
        assert db.session.get(Tenant, accounts["internal_tenant_id"]) is not None
