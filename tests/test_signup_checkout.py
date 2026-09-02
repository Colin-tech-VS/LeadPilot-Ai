"""Who gets sent to Stripe right after signing up, and what it costs them.

Stripe Checkout at sign-up is for exactly one case: the artisan picked a paid
offer on the pricing grid and arrived with it. Two sign-ups must never reach
it — the plain 14-day trial, and the « 50 premiers artisans » founding
members. Being asked for a card seconds after signing up for something free is
how you lose the account you just created.

And when Checkout *is* right, it must not quietly cancel the free trial the
whole site promises: choosing a plan used to bill the first month on the spot,
on a page that had just said « 14 jours gratuits · sans carte bancaire ». The
trial is carried into the subscription instead, so the card is taken now and
the first charge lands the day the trial would have ended anyway.
"""
import uuid

import pytest

from app.models.tenant import TRIAL_DAYS, Tenant
from app.models.user import User


@pytest.fixture
def stripe_calls(app, monkeypatch):
    """Configure Stripe and capture what Checkout would have been asked for,
    without ever talking to Stripe."""
    calls = []

    class _Session:
        url = "https://checkout.stripe.test/session"

    class _Checkout:
        class Session:
            @staticmethod
            def create(**kwargs):
                calls.append(kwargs)
                return _Session()

    class _Stripe:
        checkout = _Checkout

    app.config["STRIPE_SECRET_KEY"] = "sk_test_signup_checkout"
    app.config["STRIPE_PRICE_STARTER"] = "price_starter_test"
    monkeypatch.setattr("app.services.billing._client", lambda: _Stripe)
    return calls


def _signup(**overrides):
    data = {
        "company_name": "Plomberie Checkout",
        "email": f"checkout-{uuid.uuid4().hex[:10]}@example.com",
        "city": "Nantes",
        "trade_type": "plombier",
        "password": "MotDePasse123",
    }
    data.update(overrides)
    return data


# ── Who is sent to Stripe ────────────────────────────────────────────────────


def test_the_plain_trial_signup_never_sees_stripe(client, app, stripe_calls):
    """No plan chosen — the artisan took the 14 free days and lands on the
    dashboard, not on a card form."""
    response = client.post("/register", data=_signup())
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")
    assert stripe_calls == []


def test_choosing_a_paid_offer_goes_straight_to_checkout(client, app, stripe_calls):
    """The one case Checkout is for: they picked Starter on the pricing grid."""
    response = client.post("/register", data=_signup(plan="starter"))
    assert response.status_code == 303
    assert response.headers["Location"] == "https://checkout.stripe.test/session"
    assert len(stripe_calls) == 1
    assert stripe_calls[0]["metadata"]["plan"] == "starter"


def test_a_founding_member_never_sees_stripe(client, app, stripe_calls):
    """The « 50 premiers artisans » offer is free by construction. Joining it
    must not end on a payment page."""
    from app.services import founding_program

    if not founding_program.accept_signups():
        pytest.skip("founding programme closed")

    email = f"founding-{uuid.uuid4().hex[:8]}@example.com"
    response = client.post(
        "/50-artisans",
        data={
            "company_name": "Plomberie Fondatrice",
            "first_name": "Jean",
            "last_name": "Dupont",
            "email": email,
            "phone": f"+3360000{uuid.uuid4().int % 10000:04d}",
            "city": "Lyon",
            "trade_type": "plombier",
            "password": "MotDePasse123",
        },
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")
    assert stripe_calls == []

    with app.app_context():
        assert User.query.filter_by(email=email).first() is not None


def test_a_plan_that_is_not_a_real_offer_is_ignored(client, app, stripe_calls):
    """``?plan=`` is visitor-controlled. Anything that is not one of the paid
    plans must fall through to the dashboard, never to a checkout attempt."""
    response = client.post("/register", data=_signup(plan="fondateur"))
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")
    assert stripe_calls == []


# ── What Checkout costs them ─────────────────────────────────────────────────


def test_billing_checkout_rejects_missing_quantity_or_amount(client, app, stripe_calls):
    """Stripe rejects an order without quantity or amount; fail clearly at 400."""
    client.post("/register", data=_signup())
    response = client.post("/billing/checkout/starter", follow_redirects=False)
    assert response.status_code == 400
    assert response.get_json() == {
        "error": "Un ordre a besoin d'une quantité ou d'un montant."
    }
    assert stripe_calls == []


def test_billing_checkout_rejects_quantity_without_amount(client, app, stripe_calls):
    client.post("/register", data=_signup())
    response = client.post(
        "/billing/checkout/starter",
        data={"quantity": "1"},
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert response.get_json() == {
        "error": "Un ordre a besoin d'une quantité ou d'un montant."
    }
    assert stripe_calls == []


def test_billing_form_sends_quantity_and_amount(client, app):
    """The payment form must post quantity and amount so checkout does not 400."""
    client.post("/register", data=_signup())
    html = client.get("/billing").get_data(as_text=True)
    assert 'name="quantity"' in html
    assert 'name="amount"' in html


def test_billing_checkout_succeeds_when_quantity_and_amount_are_present(
    client, app, stripe_calls
):
    client.post("/register", data=_signup())
    response = client.post(
        "/billing/checkout/starter",
        data={"quantity": "1", "amount": "14900"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["Location"] == "https://checkout.stripe.test/session"
    assert len(stripe_calls) == 1


def test_checkout_carries_the_free_trial_into_the_subscription(client, app, stripe_calls):
    """Choosing a plan must not cost the artisan the free days they were just
    promised: the card is taken now, the first charge waits for the trial.

    The length is whatever the page advertised at the moment they signed up —
    longer while the launch programme is open — not the bare constant."""
    from app.services import founding_program

    client.post("/register", data=_signup(plan="starter"))

    with app.app_context():
        expected = founding_program.public_trial_days()
    subscription = stripe_calls[0]["subscription_data"]
    assert subscription["trial_period_days"] == expected
    assert expected >= TRIAL_DAYS


def test_an_expired_trial_subscribes_at_full_price(app, stripe_calls):
    """Only what is *left* of the trial is carried over. Someone coming back to
    /billing months later gets the plan, not a second free fortnight."""
    from datetime import datetime, timedelta, timezone

    from app.core.extensions import db
    from app.services import billing

    with app.app_context():
        tenant = Tenant(
            name="Plomberie Expirée",
            trade_type="plombier",
            trial_ends_at=datetime.now(timezone.utc) - timedelta(days=3),
        )
        db.session.add(tenant)
        db.session.commit()

        assert billing.checkout_trial_days(tenant) == 0
        billing.create_checkout_session(tenant, "starter", "https://ok", "https://ko")

    assert "trial_period_days" not in stripe_calls[0]["subscription_data"]


def test_a_paying_tenant_gets_no_trial_at_all(app, stripe_calls):
    """Changing plan is not a reason to stop paying for a fortnight."""
    from app.core.extensions import db
    from app.services import billing

    with app.app_context():
        tenant = Tenant(name="Plomberie Abonnée", trade_type="plombier", plan="pro")
        db.session.add(tenant)
        db.session.commit()

        assert billing.checkout_trial_days(tenant) == 0


# ── What the page promises before it happens ─────────────────────────────────


def test_the_page_stops_promising_no_card_when_an_offer_is_chosen(client):
    """« Sans carte bancaire » next to a button that leads to Stripe is the
    contradiction that made the redirect feel like a bait-and-switch."""
    html = client.get("/register?plan=starter").get_data(as_text=True)
    assert "Sans carte bancaire" not in html
    assert "Carte enregistrée, débit après" in html
    # The free days are real either way, so they stay on the page.
    assert "jours gratuits" in html


def test_the_trial_signup_still_promises_no_card(client):
    html = client.get("/register").get_data(as_text=True)
    assert "Sans carte bancaire" in html
    assert "Carte enregistrée" not in html


def test_the_page_never_says_no_payment_details_on_the_way_to_stripe(client):
    """The reassurance line under the fields said « Aucune donnée bancaire
    demandée » — on the page that was about to ask for exactly that."""
    html = client.get("/register?plan=starter").get_data(as_text=True)
    assert "Aucune donnée bancaire demandée" not in html
    assert "Stripe" in html


# ── Cancelling during the days that were free anyway ─────────────────────────


def test_cancelling_inside_the_free_trial_keeps_the_days_left(app):
    """Carrying the trial into Stripe created a case that could not exist
    before: a subscription cancelled before it ever charged. The artisan is
    still inside the 14 days the site promised, and must keep them — expiring
    the trial would punish them for having tried to pay."""
    from datetime import timedelta

    from app.core.extensions import db
    from app.models.tenant import utcnow
    from app.services import billing

    with app.app_context():
        ends_at = utcnow() + timedelta(days=11)
        tenant = Tenant(
            name="Plomberie Annulée",
            trade_type="plombier",
            plan="starter",
            trial_ends_at=ends_at,
            stripe_subscription_id="sub_cancelled_in_trial",
        )
        db.session.add(tenant)
        db.session.commit()

        assert billing.apply_event(
            "customer.subscription.deleted", {"id": "sub_cancelled_in_trial"}
        )

        assert tenant.plan == "trial"
        assert tenant.trial_days_left == 11
        assert tenant.subscription_active


def test_cancelling_after_the_trial_ends_access_now(app):
    """The ordinary case is unchanged: a paying artisan who cancels stops
    there, they do not get a fortnight back."""
    from datetime import timedelta

    from app.core.extensions import db
    from app.models.tenant import utcnow
    from app.services import billing

    with app.app_context():
        tenant = Tenant(
            name="Plomberie Longue",
            trade_type="plombier",
            plan="pro",
            trial_ends_at=utcnow() - timedelta(days=90),
            stripe_subscription_id="sub_cancelled_after_trial",
        )
        db.session.add(tenant)
        db.session.commit()

        assert billing.apply_event(
            "customer.subscription.deleted", {"id": "sub_cancelled_after_trial"}
        )

        assert tenant.plan == "trial"
        assert not tenant.subscription_active
