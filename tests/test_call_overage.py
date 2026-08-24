"""Included calls are what the plan covers, not a hard ceiling."""
import uuid
from datetime import datetime, timezone

import pytest

from app.core.extensions import db
from app.models.lead import Lead
from app.models.tenant import Tenant
from app.services import billing
from app.services import plan_features as pf


def _tenant(app, plan="starter"):
    with app.app_context():
        t = Tenant(
            name=f"Plomberie {plan}",
            trade_type="plombier",
            plan=plan,
            public_slug=f"ov-{uuid.uuid4().hex[:8]}",
        )
        db.session.add(t)
        db.session.commit()
        return db.session.get(Tenant, t.id)


def _calls(app, tenant, n):
    """n handled calls — each qualified inbound call creates one Lead."""
    with app.app_context():
        for i in range(n):
            db.session.add(Lead(tenant_id=tenant.id, name=f"c{i}", phone="+33600000000"))
        db.session.commit()


def test_calls_are_still_answered_at_the_allowance(app):
    """Regression: the line went dead at the allowance. A missed-call product
    that stops answering calls mid-month has failed at its one job — and because
    a refused call creates no lead, usage could never pass the quota, so the
    overage was unbillable and ``bill_overage.py`` invoiced nothing, for anyone."""
    t = _tenant(app)
    _calls(app, t, 150)
    with app.app_context():
        assert pf.calls_used(t) == 150
        assert pf.inbound_allowed(t) == (True, None)
        assert pf.over_quota(t) is True


def test_calls_beyond_the_allowance_accrue_overage(app):
    t = _tenant(app)
    _calls(app, t, 153)
    with app.app_context():
        assert billing.overage_calls(t) == 3
        assert billing.overage_amount_cents(t) == 3 * billing.overage_price_cents()


def test_no_overage_while_within_the_allowance(app):
    t = _tenant(app)
    _calls(app, t, 10)
    with app.app_context():
        assert billing.overage_calls(t) == 0
        assert pf.over_quota(t) is False


def test_an_inactive_subscription_still_blocks(app):
    """The one thing that must still silence the line."""
    t = _tenant(app, plan="trial")
    with app.app_context():
        fresh = db.session.get(Tenant, t.id)
        # Never subscribed, and the free trial has run out.
        fresh.trial_ends_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
        db.session.commit()
        assert fresh.subscription_active is False
        allowed, reason = pf.inbound_allowed(fresh)
    assert allowed is False
    assert reason == "expired"


def test_trial_is_unlimited_and_never_bills_overage(app):
    t = _tenant(app, plan="trial")
    _calls(app, t, 400)
    with app.app_context():
        assert pf.call_quota(t) is None
        assert pf.inbound_allowed(t) == (True, None)
        assert pf.over_quota(t) is False
        assert billing.overage_calls(t) == 0


def test_plan_summary_shows_the_running_overage(app):
    """The artisan should see the cost accruing, not meet it on the invoice."""
    t = _tenant(app)
    _calls(app, t, 152)
    with app.app_context():
        summary = pf.plan_summary(t)
    assert summary["calls_used"] == 152
    assert summary["calls_remaining"] == 0
    assert summary["overage_calls"] == 2
    assert summary["overage_amount_cents"] == 2 * 50


def test_monthly_job_bills_the_overage_once(app, monkeypatch):
    """The whole point of letting the calls through: they get invoiced."""
    t = _tenant(app)
    _calls(app, t, 155)
    created = []

    class _InvoiceItem:
        @staticmethod
        def create(**kwargs):
            created.append(kwargs)

    class _Stripe:
        InvoiceItem = _InvoiceItem

    now = datetime.now(timezone.utc)
    with app.app_context():
        app.config["STRIPE_SECRET_KEY"] = "sk_test"
        fresh = db.session.get(Tenant, t.id)
        fresh.stripe_customer_id = "cus_test"
        db.session.commit()
        monkeypatch.setattr(billing, "_client", lambda: _Stripe)

        first = billing.bill_overage_for_period(fresh, now.year, now.month)
        second = billing.bill_overage_for_period(fresh, now.year, now.month)

    assert first["status"] == "billed"
    assert first["calls"] == 5
    assert first["amount_cents"] == 250
    assert len(created) == 1
    # Idempotent — re-running the monthly job must not double-bill.
    assert second["status"] == "skipped"
    assert len(created) == 1


def test_usage_resets_on_the_first_of_the_month(app):
    """The allowance 'recharges' by counting the calendar month, not a counter
    someone has to remember to reset."""
    t = _tenant(app)
    with app.app_context():
        old = Lead(tenant_id=t.id, name="last month", phone="+336")
        db.session.add(old)
        db.session.commit()
        old.created_at = datetime(2020, 1, 15, tzinfo=timezone.utc)
        db.session.commit()
        assert pf.calls_used(t) == 0
        assert billing.calls_in_month(t, 2020, 1) == 1
