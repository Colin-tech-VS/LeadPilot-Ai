"""The free trial must be able to handle a call.

Dedicated Twilio numbers were bought for paying tenants only, and a call to the
shared fallback is routed to ``TWILIO_DEFAULT_TENANT_ID`` — so a trial artisan's
clients could not reach them through PilotCore at all. Fourteen days ran out on
an empty dashboard that said the assistant was answering, and there was nothing
to subscribe for at the end.
"""
import uuid

import pytest

from app.core.extensions import db
from app.services import activation, twilio_provisioning
from app.services.signup_service import register_plumber


def _artisan(app, **kwargs):
    fields = {
        "email": f"act-{uuid.uuid4().hex[:8]}@example.com",
        "password": "password1",
        "company_name": "Plomberie Activation",
        "city": "Angers",
        "phone": "+33612000001",
    }
    fields.update(kwargs)
    _user, tenant = register_plumber(**fields)
    return tenant


# ── Who may hold a line ──────────────────────────────────────────────────────


def test_a_trial_that_never_asked_gets_no_line(app):
    """The gate is the request, not the payment — but it is still a gate. A
    scanner that POSTs /register and never comes back must not cost a number."""
    with app.app_context():
        tenant = _artisan(app)
        assert tenant.line_requested_at is None
        assert twilio_provisioning.should_buy_dedicated_number(tenant) is False
        assert twilio_provisioning.dedicated_number(tenant) is None


def test_a_trial_without_a_phone_number_is_told_what_to_fix(app):
    with app.app_context():
        tenant = _artisan(app, phone=None)
        assert twilio_provisioning.trial_line_blocker(tenant) == "phone_missing"

        result = twilio_provisioning.request_line(tenant)
        assert result["status"] == "blocked"
        assert result["reason"] == "phone_missing"
        # A blocked request is not recorded: it would show « en cours
        # d'attribution » forever on a dashboard nobody could unblock.
        assert tenant.line_requested_at is None


def test_an_expired_trial_is_sent_to_billing_not_to_a_line(app):
    from datetime import timedelta

    from app.models.tenant import utcnow

    with app.app_context():
        tenant = _artisan(app)
        tenant.trial_ends_at = utcnow() - timedelta(days=1)
        db.session.commit()
        assert twilio_provisioning.trial_line_blocker(tenant) == "trial_expired"
        assert twilio_provisioning.request_line(tenant)["status"] == "blocked"


def test_asking_records_the_request_even_when_twilio_cannot_serve_it(app):
    """Under pytest no number is ever bought. The request must still be stored,
    so ``scripts/provision_numbers.py`` can fulfil it later — otherwise a Twilio
    outage during signup means that artisan never gets a line at all."""
    with app.app_context():
        tenant = _artisan(app)
        result = twilio_provisioning.request_line(tenant)
        assert result["status"] == "pending"
        assert tenant.line_requested_at is not None


def test_the_trial_line_cap_is_enforced(app):
    with app.app_context():
        app.config["TWILIO_MAX_TRIAL_LINES"] = 1
        try:
            holder = _artisan(app, phone="+33612000009")
            holder.ai_phone_number = "+33159160099"
            db.session.commit()

            assert twilio_provisioning.trial_lines_in_use() == 1
            assert twilio_provisioning.trial_lines_remaining() == 0

            latecomer = _artisan(app, phone="+33612000010")
            assert twilio_provisioning.trial_line_blocker(latecomer) == "capacity"
            assert twilio_provisioning.should_buy_dedicated_number(latecomer) is False
        finally:
            app.config["TWILIO_MAX_TRIAL_LINES"] = 50


def test_the_shared_fallback_is_never_presented_as_the_artisans_own(app):
    """A call to the shared number routes to TWILIO_DEFAULT_TENANT_ID, so
    showing it as « votre numéro » tells the artisan their clients are being
    answered when they are not."""
    with app.app_context():
        tenant = _artisan(app)
        tenant.ai_phone_number = app.config["TWILIO_AI_PHONE_NUMBER"]
        db.session.commit()

        assert twilio_provisioning.dedicated_number(tenant) is None
        assert twilio_provisioning.has_active_line(tenant) is False
        assert twilio_provisioning.line_state(tenant)["status"] == "off"


# ── What the artisan is told to do ───────────────────────────────────────────


def test_the_checklist_orders_the_steps_and_stops_at_the_first_gap(app):
    with app.app_context():
        tenant = _artisan(app)
        check = activation.checklist(tenant)

        assert [s["key"] for s in check["steps"]] == ["account", "profile", "line", "forwarding"]
        assert check["complete"] is False
        assert check["next_step"]["key"] == "line"  # profile is complete at signup

        tenant.city = None
        db.session.commit()
        assert activation.checklist(tenant)["next_step"]["key"] == "profile"


def test_a_recorded_request_does_not_tick_the_line_step(app):
    """Saying « done » on a request that produced no number is exactly the lie
    the dashboard used to tell for fourteen days."""
    with app.app_context():
        tenant = _artisan(app)
        twilio_provisioning.request_line(tenant)
        check = activation.checklist(tenant)
        assert check["line"]["status"] == "pending"
        assert check["steps"][2]["done"] is False


def test_forwarding_codes_carry_the_artisans_own_number(app):
    with app.app_context():
        tenant = _artisan(app)
        tenant.ai_phone_number = "+33159160001"
        db.session.commit()

        check = activation.checklist(tenant)
        assert check["next_step"]["key"] == "forwarding"
        codes = [item["code"] for item in check["forwarding"]]
        assert codes == ["**61*+33159160001#", "**67*+33159160001#", "**62*+33159160001#"]
        assert check["forward_cancel_code"] == "##002#"
        # No spaces: a dial code with a space in it does nothing on a handset.
        assert all(" " not in code for code in codes)


def test_no_line_means_no_forwarding_instructions(app):
    with app.app_context():
        tenant = _artisan(app)
        assert activation.forwarding_instructions(None) == []
        assert activation.checklist(tenant)["forwarding"] == []


# ── What the dashboard says ──────────────────────────────────────────────────


@pytest.fixture
def logged_in(client, app):
    email = f"dash-{uuid.uuid4().hex[:8]}@example.com"
    client.post(
        "/register",
        data={
            "company_name": "Plomberie Tableau",
            "email": email,
            "password": "MotDePasse123",
            "city": "Angers",
            "trade_type": "plombier",
            "phone": f"+3361{uuid.uuid4().int % 10**7:07d}",
        },
        follow_redirects=False,
    )
    with app.app_context():
        from app.models.user import User

        return User.query.filter_by(email=email).first().tenant_id


def test_the_dashboard_does_not_claim_an_assistant_is_answering(client, logged_in):
    html = client.get("/dashboard").get_data(as_text=True)
    assert "Mettez votre standard en service" in html
    assert "Votre assistant téléphonique répond encore" not in html
    assert "Activer ma ligne" in html


def test_activating_from_the_dashboard_records_the_request(client, app, logged_in):
    resp = client.post("/ligne/activer", follow_redirects=False)
    assert resp.status_code == 302
    assert "/dashboard" in resp.headers["Location"]
    with app.app_context():
        from app.models.tenant import Tenant

        assert db.session.get(Tenant, logged_in).line_requested_at is not None


def test_the_checklist_disappears_once_a_call_has_been_handled(client, app, logged_in):
    from app.models.lead import Lead
    from app.models.tenant import Tenant

    with app.app_context():
        tenant = db.session.get(Tenant, logged_in)
        tenant.ai_phone_number = "+33159160002"
        db.session.add(Lead(tenant_id=tenant.id, name="Sophie", phone="+33611111111", status="new"))
        db.session.commit()
        assert activation.checklist(tenant)["complete"] is True

    html = client.get("/dashboard").get_data(as_text=True)
    assert "Mettez votre standard en service" not in html


# ── Giving the line back ─────────────────────────────────────────────────────


def test_a_line_is_only_released_after_the_grace_period(app):
    from datetime import timedelta

    from app.models.tenant import utcnow

    with app.app_context():
        tenant = _artisan(app, phone="+33612000077")
        tenant.ai_phone_number = "+33159160077"
        tenant.line_requested_at = utcnow()

        # Just expired: someone subscribing two days late keeps the number
        # their clients have been calling.
        tenant.trial_ends_at = utcnow() - timedelta(days=1)
        db.session.commit()
        assert tenant not in twilio_provisioning.expired_trial_lines()

        tenant.trial_ends_at = utcnow() - timedelta(
            days=twilio_provisioning.TRIAL_LINE_GRACE_DAYS + 1
        )
        db.session.commit()
        assert tenant in twilio_provisioning.expired_trial_lines()

        released, failed = twilio_provisioning.release_expired_trial_lines()
        assert released >= 1 and failed == 0
        assert tenant.ai_phone_number is None
        # Cleared with the number: an artisan who comes back asks again.
        assert tenant.line_requested_at is None


def test_a_paying_artisan_never_loses_their_line(app):
    from datetime import timedelta

    from app.models.tenant import utcnow

    with app.app_context():
        tenant = _artisan(app, phone="+33612000078")
        tenant.ai_phone_number = "+33159160078"
        tenant.plan = "starter"
        tenant.trial_ends_at = utcnow() - timedelta(days=365)
        db.session.commit()
        assert tenant not in twilio_provisioning.expired_trial_lines()
