"""Plan feature matrix and call quotas."""

from datetime import timedelta

from app.models.tenant import TRIAL_DAYS, Tenant, utcnow
from app.services import plan_features as pf


def _tenant(plan="starter", trialing=False):
    t = Tenant(name="Test Co", plan=plan)
    if trialing:
        t.plan = "trial"
        t.trial_ends_at = utcnow() + timedelta(days=TRIAL_DAYS)
    return t


def test_trial_has_all_premium_features():
    t = _tenant(trialing=True)
    assert pf.has_feature(t, "auto_booking")
    assert pf.has_feature(t, "crm_marketing")
    assert pf.has_feature(t, "ai_customization")
    assert pf.call_quota(t) is None


def test_starter_excludes_pro_features():
    t = _tenant(plan="starter")
    t.trial_ends_at = utcnow() - timedelta(days=1)
    assert not pf.has_feature(t, "auto_booking")
    assert not pf.has_feature(t, "sms_email_notifications")
    assert not pf.has_feature(t, "crm_marketing")
    assert pf.call_quota(t) == 150


def test_pro_includes_booking_not_crm():
    t = _tenant(plan="pro")
    assert pf.has_feature(t, "auto_booking")
    assert pf.has_feature(t, "sms_email_notifications")
    assert not pf.has_feature(t, "crm_marketing")


def test_premium_includes_crm():
    t = _tenant(plan="premium")
    assert pf.has_feature(t, "crm_marketing")
    assert pf.has_feature(t, "multiple_phone_numbers")


def test_apply_booking_plan_limits_starter():
    t = _tenant(plan="pro")
    t.plan = "starter"
    out = pf.apply_booking_plan_limits(t, {"action": "BOOK_NOW", "suggested_slot": "2026-01-01T10:00:00"})
    assert out["action"] == "CALL_BACK"
    assert out.get("plan_limited")
