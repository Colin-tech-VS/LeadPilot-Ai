"""Turning artisan-search traffic into artisan sign-ups.

Search Console shows the same thing on every query that brings anyone here:
people looking for an artisan. The pages that rank are therefore written for a
customer — and an artisan who lands on one (searching their own company, their
town, their trade) used to find nothing addressed to them, which is why the
artisan side of the funnel converted nobody.

These tests pin the three things that fix costs nothing to break silently: the
pages speak to artisans, the CTA carries enough context that the form opens
half-answered, and which CTA sent them is recorded so a band that converts
nothing can be told from one nobody sees.
"""
import uuid

import pytest

from app.core.extensions import db
from app.models.registry_listing import STATUS_LISTED, RegistryListing
from app.services import plan_features, signup_funnel
from app.services.billing import PLANS


# ── The pages speak to artisans ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "/artisans",
        "/artisans/metier/plombier",
        "/artisans/plombier/lyon",
        "/trouver-un-artisan",
        "/depannage-urgent",
        "/prix-artisans",
        "/blog",
        "/",
    ],
)
def test_every_client_search_page_speaks_to_artisans(client, path):
    html = client.get(path).get_data(as_text=True)
    assert 'class="pro-hook"' in html, f"{path} has no « Vous êtes artisan ? » band"
    assert "/register?" in html, f"{path} sends nobody to the sign-up form"
    assert "sans carte bancaire" in html


def test_the_local_page_names_the_trade_and_the_town(client):
    """« Vous êtes artisan ? » on a page ranking for « plombier lyon » wastes
    everything the page already knows about who is reading it."""
    html = client.get("/artisans/plombier/lyon").get_data(as_text=True)
    assert "Vous êtes plombier à Lyon ?" in html
    assert "/register?trade=plombier&amp;city=Lyon&amp;src=annuaire" in html


def test_the_department_page_does_not_call_the_department_a_town(client):
    """``local_ctx.city`` holds the department name there — « Vous êtes plombier
    à Haute-Savoie ? » is not French."""
    html = client.get("/artisans/plombier/departement/haute-savoie").get_data(as_text=True)
    assert "Vous êtes plombier ?" in html
    assert "Vous êtes plombier à Haute-Savoie" not in html


def test_a_registry_listing_says_what_taking_it_over_gives(app, client):
    """The page an artisan reaches by searching their own name: the strongest
    pro intent on the site. Explaining why the coordinates are missing is not a
    reason to claim the fiche."""
    siren = str(uuid.uuid4().int)[:9]
    with app.app_context():
        db.session.add(
            RegistryListing(
                siren=siren,
                siret=siren + "00019",
                name="PLOMBERIE ESSAI",
                trade_key="plombier",
                city="Lyon",
                city_slug="lyon",
                postal_code="69003",
                dept_code="69",
                status=STATUS_LISTED,
            )
        )
        db.session.commit()

    html = client.get(f"/artisans/entreprise/{siren}").get_data(as_text=True)
    assert f"/artisans/revendiquer/{siren}" in html  # the verified path stays first
    assert "lp-unclaimed-gains" in html
    assert "lp-seller-cta" in html
    assert "lp-claim-sticky" in html
    assert "reste en ligne même sans abonnement" in html
    assert "/register?trade=plombier&amp;city=Lyon&amp;src=fiche-registre" in html


# ── The form opens half-answered ─────────────────────────────────────────────


def test_the_form_opens_on_the_account_step_with_the_trade_answered(client):
    html = client.get("/register?trade=plombier&city=Lyon&src=annuaire").get_data(as_text=True)
    assert 'data-start-step="1"' in html
    assert '<option value="plombier" data-icon="🔧" selected>' in html
    assert 'id="city" name="city"\n                         value="Lyon"' in html or 'value="Lyon"' in html


def test_an_unknown_trade_never_skips_the_first_step(client):
    """A hand-typed or stale ``?trade=`` must not drop the artisan on a form
    whose first question was silently answered wrong."""
    html = client.get("/register?trade=cosmonaute").get_data(as_text=True)
    assert 'data-start-step="0"' in html
    assert "cosmonaute" not in html


# ── Which CTA brought them ───────────────────────────────────────────────────


def _signup_form(**overrides):
    # Deliberately not plombier × Lyon: nothing is rolled back between tests in
    # this suite, and a public plumber in Lyon would push that page over the
    # indexability threshold ``test_seo`` asserts it stays under.
    data = {
        "company_name": "Peinture Acquisition",
        "email": f"acq-{uuid.uuid4().hex[:10]}@example.com",
        "city": "Vierzon",
        "trade_type": "peintre",
        "password": "MotDePasse123",
    }
    data.update(overrides)
    return data


def test_the_cta_that_sent_the_artisan_is_recorded_with_the_signup(client, app):
    """« Aucun artisan ne s'inscrit » cannot be acted on without knowing which
    page the ones who do come from — the ``?src=`` is only on the GET, so it
    has to survive until the POST."""
    with app.app_context():
        from app.models.event import Event

        Event.query.filter(Event.action == signup_funnel.ACTION).delete()
        db.session.commit()

    client.get("/register?trade=plombier&city=Lyon&src=fiche-registre")
    assert client.post("/register", data=_signup_form()).status_code == 302

    with app.app_context():
        from datetime import datetime, timezone

        report = signup_funnel.summary(datetime(2000, 1, 1, tzinfo=timezone.utc))
        rows = {r["source"]: r for r in report["by_source"]}
        assert rows["fiche-registre"]["attempts"] == 1
        assert rows["fiche-registre"]["signups"] == 1
        assert rows["fiche-registre"]["label"] == "Fiche registre (SIREN)"


def test_a_signup_with_no_tagged_cta_is_still_counted(client, app):
    with app.app_context():
        from app.models.event import Event

        Event.query.filter(Event.action == signup_funnel.ACTION).delete()
        db.session.commit()

    assert client.post("/register", data=_signup_form()).status_code == 302

    with app.app_context():
        from datetime import datetime, timezone

        report = signup_funnel.summary(datetime(2000, 1, 1, tzinfo=timezone.utc))
        assert [r["source"] for r in report["by_source"]] == [signup_funnel.SOURCE_UNTAGGED]


def test_the_traffic_dashboard_reports_the_signup_sources(app):
    from app.services.traffic import conversions

    with app.app_context():
        assert "signup_sources" in conversions(days=30)


# ── The offer, said once and said straight ───────────────────────────────────


def test_the_comparison_table_is_built_from_the_enforced_rules(app):
    with app.app_context():
        matrix = plan_features.public_comparison()

    assert [c["key"] for c in matrix["columns"]] == ["trial", "starter", "pro", "premium"]
    cells = {row["key"]: row["cells"] for row in matrix["rows"]}

    # Allowances come from the Stripe plans, not from copy.
    for plan_key, plan in PLANS.items():
        assert cells["included_calls"][plan_key] == plan["included_calls"]
    assert cells["included_calls"]["trial"] is None  # unlimited while trialing

    # Auto-booking is a Pro feature — the table may not sell it with Starter.
    assert cells["auto_booking"]["starter"] is False
    assert cells["auto_booking"]["pro"] is True
    assert cells["auto_booking"]["trial"] is True

    # The dedicated line is bought when the artisan pays (twilio_provisioning).
    assert cells["dedicated_number"]["trial"] is False
    assert cells["dedicated_number"]["starter"] is True

    # Answering the phone is the product, not an upsell.
    for row_key in plan_features._ALWAYS_INCLUDED:
        assert all(cells[row_key].values())


def test_every_comparison_row_is_gated_by_a_real_feature(app):
    """A row whose gate no longer exists in ``PLAN_FEATURES`` would silently
    read « non compris » on every paid plan."""
    known = set().union(*plan_features.PLAN_FEATURES.values())
    for _row_key, _kind, gate in plan_features._COMPARISON_ROWS:
        assert gate is None or gate in known, gate


def test_the_pricing_section_shows_the_table_and_what_follows_the_trial(client):
    html = client.get("/pro").get_data(as_text=True)
    assert 'class="pricing-matrix"' in html
    assert "Ce que comprend chaque offre" in html
    assert "prélevé automatiquement" in html
    assert "0,50 € / appel" in html
