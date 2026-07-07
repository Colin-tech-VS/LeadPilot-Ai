"""Plan capabilities and usage limits (Starter / Pro / Premium / trial).

Marketing copy lives in i18n; this module is the runtime source of truth.

Trial (14 days): all Premium features, unlimited calls while active.
Starter: voice + leads + dashboard; 150 calls/mo; no auto-booking, SMS/e-mail
         alerts, calendar, multi-user, CRM, AI customization, extra numbers.
Pro:     Starter + auto-booking, calendar, SMS/e-mail, multi-user (≤10); 500 calls/mo.
Premium: Pro + multiple numbers, full AI customization, CRM/marketing, priority
         support; 1 500 calls/mo.
"""

from __future__ import annotations

from app.services.billing import included_calls, monthly_call_usage

# Paid-plan feature sets (trial bypasses via has_feature).
_STARTER = frozenset()
_PRO = frozenset({"auto_booking", "google_calendar", "sms_email_notifications", "multi_user"})
_PREMIUM = _PRO | frozenset(
    {
        "multiple_phone_numbers",
        "ai_customization",
        "crm_marketing",
        "priority_support",
    }
)

PLAN_FEATURES: dict[str, frozenset[str]] = {
    "starter": _STARTER,
    "pro": _PRO,
    "premium": _PREMIUM,
}

MAX_TEAM_USERS: dict[str, int | None] = {
    "starter": 1,
    "pro": 10,
    "premium": None,
}

UPGRADE_PLAN_FOR: dict[str, str] = {
    "auto_booking": "pro",
    "google_calendar": "pro",
    "sms_email_notifications": "pro",
    "multi_user": "pro",
    "multiple_phone_numbers": "premium",
    "ai_customization": "premium",
    "crm_marketing": "premium",
    "priority_support": "premium",
}


def trial_has_all_features(tenant) -> bool:
    return bool(getattr(tenant, "is_trialing", False) and tenant.subscription_active)


def has_feature(tenant, feature: str) -> bool:
    if not tenant or not tenant.subscription_active:
        return False
    if trial_has_all_features(tenant):
        return True
    plan = (getattr(tenant, "plan", None) or "trial").lower()
    return feature in PLAN_FEATURES.get(plan, frozenset())


def max_team_users(tenant) -> int | None:
    if trial_has_all_features(tenant):
        return MAX_TEAM_USERS["premium"]
    plan = (getattr(tenant, "plan", None) or "starter").lower()
    return MAX_TEAM_USERS.get(plan, 1)


def call_quota(tenant) -> int | None:
    """Monthly included calls, or None when unlimited (active trial)."""
    if not tenant or not tenant.subscription_active:
        return 0
    if trial_has_all_features(tenant):
        return None
    return included_calls(tenant.plan) or 0


def calls_used(tenant) -> int:
    if not tenant:
        return 0
    return monthly_call_usage(tenant)


def calls_remaining(tenant) -> int | None:
    quota = call_quota(tenant)
    if quota is None:
        return None
    return max(0, quota - calls_used(tenant))


def inbound_allowed(tenant) -> tuple[bool, str | None]:
    """Whether a new inbound call / lead capture is allowed."""
    if not tenant or not tenant.subscription_active:
        return False, "expired"
    quota = call_quota(tenant)
    if quota is None:
        return True, None
    if calls_used(tenant) >= quota:
        return False, "quota"
    return True, None


def upgrade_label(feature: str) -> str:
    plan = UPGRADE_PLAN_FOR.get(feature, "pro")
    return plan.capitalize()


def plan_summary(tenant) -> dict:
    """Snapshot for templates / API."""
    quota = call_quota(tenant)
    used = calls_used(tenant)
    return {
        "plan": getattr(tenant, "plan", "trial"),
        "trial_all_features": trial_has_all_features(tenant),
        "subscription_active": bool(tenant.subscription_active),
        "call_quota": quota,
        "calls_used": used,
        "calls_remaining": (max(0, quota - used) if quota is not None else None),
        "features": {
            "auto_booking": has_feature(tenant, "auto_booking"),
            "google_calendar": has_feature(tenant, "google_calendar"),
            "sms_email_notifications": has_feature(tenant, "sms_email_notifications"),
            "multi_user": has_feature(tenant, "multi_user"),
            "multiple_phone_numbers": has_feature(tenant, "multiple_phone_numbers"),
            "ai_customization": has_feature(tenant, "ai_customization"),
            "crm_marketing": has_feature(tenant, "crm_marketing"),
            "priority_support": has_feature(tenant, "priority_support"),
        },
    }


def apply_booking_plan_limits(tenant, booking: dict) -> dict:
    """Downgrade BOOK_NOW when the plan does not include auto-booking."""
    booking = dict(booking or {})
    if booking.get("action") == "BOOK_NOW" and not has_feature(tenant, "auto_booking"):
        booking["action"] = "CALL_BACK"
        booking["plan_limited"] = True
        booking["plan_limit_reason"] = "auto_booking"
    return booking
