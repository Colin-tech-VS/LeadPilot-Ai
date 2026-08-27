"""A team test account must never appear as a customer — anywhere.

The team signs up through the real forms to check the real forms work. Those
rows are indistinguishable from a customer's, so every place that counts or
lists accounts has to skip them. Missing one is not cosmetic: in production,
with a single internal account and no real one, /admin/traffic reported
« 1 inscription » and « 3431 visiteurs / inscription » — a number that reads
like a funnel measurement and is pure noise.

The suite shares one database across tests, so these assert on *deltas* rather
than absolute counts: what matters is that an internal account moves a number
by zero and a real one moves it by one.
"""
import uuid

import pytest

from app.services import analytics, internal_accounts, traffic
from app.services.signup_service import register_plumber


@pytest.fixture
def pair(app):
    """One internal account and one real one, created back to back.

    The internal address is unique per test and declared through the config, so
    tests never collide on a shared database and the configuration path itself
    is exercised.

    The city is deliberately not one the SEO tests measure: those assert that
    « plombier / Lyon » stays thin enough to remain noindex, and a public
    artisan created here would tip it over the threshold and fail them.
    """
    with app.app_context():
        internal_email = f"interne-{uuid.uuid4().hex[:8]}@pilotcore.fr"
        app.config["INTERNAL_ACCOUNT_EMAILS"] = internal_email

        before = {
            "signups": traffic.conversions(30)["signups_total"],
            "tenants": analytics.kpis(30)["tenants_total"],
            "users": analytics.kpis(30)["users_total"],
        }

        internal_name = f"Test Interne {uuid.uuid4().hex[:6]}"
        register_plumber(
            email=internal_email, password="MotDePasse123",
            company_name=internal_name, city="Guéret", send_welcome=False,
        )
        real_name = f"Plomberie Reelle {uuid.uuid4().hex[:6]}"
        register_plumber(
            email=f"vrai-{uuid.uuid4().hex[:8]}@example.com", password="MotDePasse123",
            company_name=real_name, city="Guéret", send_welcome=False,
        )
        return {
            "before": before,
            "internal_email": internal_email,
            "internal_name": internal_name,
            "real_name": real_name,
        }


def test_the_default_list_covers_the_address_the_team_tests_with(app):
    with app.app_context():
        app.config.pop("INTERNAL_ACCOUNT_EMAILS", None)
        assert internal_accounts.is_internal_email("contact@pilotcore.fr")
        assert internal_accounts.is_internal_email("  CONTACT@PilotCore.FR  ")
        assert not internal_accounts.is_internal_email("artisan@example.com")
        assert not internal_accounts.is_internal_email(None)


def test_the_list_is_configurable(app):
    with app.app_context():
        app.config["INTERNAL_ACCOUNT_EMAILS"] = "a@x.fr, B@X.FR"
        assert internal_accounts.internal_emails() == {"a@x.fr", "b@x.fr"}
        assert internal_accounts.is_internal_email("A@x.fr")
        assert not internal_accounts.is_internal_email("contact@pilotcore.fr")


def test_two_accounts_count_as_one_signup(app, pair):
    """The bug behind the 3431: /admin/traffic counted the test account."""
    with app.app_context():
        after = traffic.conversions(30)["signups_total"]
        assert after - pair["before"]["signups"] == 1


def test_two_accounts_count_as_one_in_the_kpis(app, pair):
    with app.app_context():
        k = analytics.kpis(30)
        assert k["tenants_total"] - pair["before"]["tenants"] == 1
        assert k["users_total"] - pair["before"]["users"] == 1


def test_visitors_per_signup_is_blank_rather_than_a_fake_ratio(app):
    """With no real sign-up the honest answer is « — », not a big number.

    Stated as the invariant so it holds whatever the shared database contains.
    """
    with app.app_context():
        conv = traffic.conversions(30)
        if conv["signups_total"]:
            assert conv["visitors_per_signup"] == round(
                conv["unique_visitors"] / conv["signups_total"], 1
            )
        else:
            assert conv["visitors_per_signup"] is None


def test_the_admin_accounts_list_shows_the_real_one_and_hides_the_test_one(client, app, pair):
    with client.session_transaction() as sess:
        sess["admin_authenticated"] = True
        sess["admin_username"] = "admin"

    html = client.get("/admin/clients?tab=artisans").get_data(as_text=True)
    assert pair["real_name"] in html, "a real account must still be listed"
    assert pair["internal_name"] not in html


def test_the_public_directory_hides_the_test_one(app, pair):
    from app.services import artisan_directory

    with app.app_context():
        names = [t.name for t in artisan_directory.public_artisans_query().all()]
        assert pair["real_name"] in names
        assert pair["internal_name"] not in names


def test_the_signup_curve_does_not_spike_on_it(app, pair):
    with app.app_context():
        series = traffic.timeseries(30)
        points = series["points"] if isinstance(series, dict) else series
        total = sum(p.get("signups", 0) for p in points)
        assert total >= 1
        # The internal account must not have added a second point-worth.
        assert total == traffic.conversions(30)["signups_total"]
