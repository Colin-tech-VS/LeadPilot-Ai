"""Dedicated Twilio numbers are for paying artisans only — not trial signups."""
import uuid

from app.core.extensions import db
from app.models.tenant import Tenant
from app.services.signup_service import register_plumber
from app.services.twilio_provisioning import (
    release_ai_number,
    should_buy_dedicated_number,
)
from app.services import billing


def _tenant(app, *, plan="trial", number=None):
    with app.app_context():
        t = Tenant(
            name="Atelier Test",
            trade_type="plombier",
            plan=plan,
            public_slug=f"tw-{uuid.uuid4().hex[:8]}",
            ai_phone_number=number,
        )
        db.session.add(t)
        db.session.commit()
        return t.id


def test_signup_does_not_buy_a_twilio_number(app):
    with app.app_context():
        email = f"trial-{uuid.uuid4().hex[:8]}@example.com"
        _user, tenant = register_plumber(
            email=email,
            password="password1",
            company_name="Plomberie Test",
            city="Lyon",
        )
        assert tenant.plan == "trial"
        assert not tenant.ai_phone_number


def test_trial_never_qualifies_for_a_dedicated_number(app):
    tid = _tenant(app, plan="trial")
    with app.app_context():
        tenant = db.session.get(Tenant, tid)
        assert should_buy_dedicated_number(tenant) is False


def test_paid_tenant_qualifies_only_in_production(app, monkeypatch):
    tid = _tenant(app, plan="starter")
    with app.app_context():
        tenant = db.session.get(Tenant, tid)
        assert tenant.is_paid is True
        assert should_buy_dedicated_number(tenant) is False
        monkeypatch.setenv("LIVE_PROVIDER_SPEND", "1")
        monkeypatch.setenv("FLASK_ENV", "production")
        app.config["TESTING"] = False
        app.config["ENV"] = "production"
        app.config["LIVE_PROVIDER_SPEND"] = "1"
        app.config["TWILIO_AUTO_PROVISION_NUMBERS"] = True
        app.config["TWILIO_ACCOUNT_SID"] = "ACffffffffffffffffffffffffffffffff"
        app.config["TWILIO_AUTH_TOKEN"] = "tok"
        assert should_buy_dedicated_number(tenant) is True


def test_sms_never_sends_during_pytest(app, monkeypatch):
    from app.services import sms as sms_mod

    called = []
    monkeypatch.setattr(sms_mod, "sms_configured", lambda: True)

    class _Boom:
        def __init__(self, *a, **k):
            raise AssertionError("Twilio Client must not be constructed in tests")

    monkeypatch.setattr("twilio.rest.Client", _Boom)
    with app.app_context():
        app.config["TWILIO_ACCOUNT_SID"] = "ACffffffffffffffffffffffffffffffff"
        app.config["TWILIO_AUTH_TOKEN"] = "tok"
        app.config["TWILIO_AI_PHONE_NUMBER"] = "+33159169691"
        assert sms_mod.send_sms("0612345678", "hello") is False
    assert not called


def test_stripe_checkout_does_not_buy_during_pytest(app):
    tid = _tenant(app, plan="trial")
    with app.app_context():
        ok = billing.apply_event(
            "checkout.session.completed",
            {
                "metadata": {"tenant_id": str(tid), "plan": "starter"},
                "customer": "cus_test",
                "subscription": "sub_test",
            },
        )
        assert ok is True
        tenant = db.session.get(Tenant, tid)
        assert tenant.plan == "starter"
        assert tenant.ai_phone_number is None


def test_release_clears_the_field_without_calling_twilio_in_tests(app):
    tid = _tenant(app, plan="starter", number="+33600000000")
    with app.app_context():
        tenant = db.session.get(Tenant, tid)
        assert release_ai_number(tenant) is True
        assert tenant.ai_phone_number is None


def test_shared_line_is_cleared_without_releasing_twilio(app):
    shared = app.config.get("TWILIO_AI_PHONE_NUMBER") or "+33159169691"
    tid = _tenant(app, plan="starter", number=shared)
    with app.app_context():
        app.config["TWILIO_AI_PHONE_NUMBER"] = shared
        tenant = db.session.get(Tenant, tid)
        assert release_ai_number(tenant) is True
        assert tenant.ai_phone_number is None
