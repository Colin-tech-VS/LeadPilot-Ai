"""Plan capabilities and usage limits (Starter / Pro / Premium / trial).

Marketing copy lives in i18n; this module is the runtime source of truth.

Trial (14 days): all Premium features, unlimited calls while active.
Starter (149 €): stop missing calls — voice + qualification + listing +
         dashboard; 150 calls/mo; no auto-booking, no client SMS/e-mail.
Pro (349 €): turn calls into appointments — Starter + auto-booking, SMS/e-mail
         to the client, public listing booking; 500 calls/mo.
Premium (699 €): automate part of acquisition / client follow-up — Pro +
         marketing campaigns and segments, assistant first name; 1 500 calls/mo.
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


# Rows of the public comparison table on /pro, in display order:
# ``(row key, cell kind, feature gate)``. ``flag`` cells are yes/no, ``number``
# cells carry a count (None meaning "no limit"), and the gate is the entry of
# ``PLAN_FEATURES`` that decides the cell — ``None`` for rows computed below.
#
# The row key and the gate are deliberately allowed to differ. Several gates are
# named after a capability the product does not ship yet (a Google Calendar
# sync, extra seats): what they actually unlock today is an appointment written
# into the workspace agenda and online booking from the public listing. The row
# key names the shipped thing, so the label in i18n describes what an artisan
# really gets — the same rule ``content_studio.honest_feature_line`` applies to
# the offer cards, enforced by ``tests/test_offer_honesty.py``.
#
# Gates with nothing shipped behind them (``multiple_phone_numbers``,
# ``multi_user`` as team seats) have no row at all rather than a row nobody
# could honestly fill.
#
# The first three rows are true of every plan and of the trial: they are what
# the product *is*, and a table listing only the differences would read as if
# answering the phone were an option.
_ALWAYS_INCLUDED = ("ai_reception", "qualification", "public_listing")

_COMPARISON_ROWS = (
    ("included_calls", "number", None),
    ("call_overage", "number", None),
    ("ai_reception", "flag", None),
    ("qualification", "flag", None),
    ("public_listing", "flag", None),
    ("dedicated_number", "flag", None),
    ("auto_booking", "flag", "auto_booking"),
    ("appointments_in_agenda", "flag", "google_calendar"),
    ("client_notifications", "flag", "sms_email_notifications"),
    ("listing_online_booking", "flag", "multi_user"),
    ("crm_marketing", "flag", "crm_marketing"),
    ("assistant_first_name", "flag", "ai_customization"),
    ("priority_support", "flag", "priority_support"),
)

_COMPARISON_PLANS = ("trial", "starter", "pro", "premium")


def public_comparison() -> dict:
    """The offer as a single table: what the trial opens, what each plan keeps.

    Built from ``PLAN_FEATURES`` / ``MAX_TEAM_USERS`` / ``billing.PLANS`` rather
    than from marketing copy, so /pro cannot drift from what the app enforces.
    Labels stay in i18n — this returns keys and raw values only.
    """
    from flask import current_app

    from app.services.billing import PLANS

    try:
        overage = int(current_app.config.get("CALL_OVERAGE_PRICE_CENTS", 50))
    except RuntimeError:  # outside an app context
        overage = 50

    columns = []
    for key in _COMPARISON_PLANS:
        plan = PLANS.get(key) or {}
        columns.append(
            {
                "key": key,
                "trial": key == "trial",
                # Cents, so the template formats the amount for its locale.
                "price_cents": 0 if key == "trial" else plan.get("amount"),
            }
        )

    def _cell(plan_key, row_key, gate):
        trial = plan_key == "trial"
        if row_key == "included_calls":
            # The trial answers every call; a paid plan covers an allowance and
            # bills the rest (see ``inbound_allowed``).
            return None if trial else PLANS.get(plan_key, {}).get("included_calls")
        if row_key == "call_overage":
            return 0 if trial else overage
        if row_key == "dedicated_number":
            # Dedicated numbers are bought when the artisan pays; the trial
            # shares the PilotCore line (``twilio_provisioning``).
            return not trial
        if row_key in _ALWAYS_INCLUDED:
            return True
        if trial:
            return True  # the trial opens every Premium feature
        return gate in PLAN_FEATURES.get(plan_key, frozenset())

    rows = [
        {
            "key": row_key,
            "kind": kind,
            # Named ``cells`` rather than ``values``: in a template
            # ``row.values`` resolves to the dict method, not to this key.
            "cells": {c["key"]: _cell(c["key"], row_key, gate) for c in columns},
        }
        for row_key, kind, gate in _COMPARISON_ROWS
    ]
    return {"columns": columns, "rows": rows}


def trial_has_all_features(tenant) -> bool:
    """Classic /register trial: every Premium feature, unlimited calls.

    The /50-artisans gift is Starter for 30 days — not this bypass.
    """
    if founding_starter_gift_active(tenant):
        return False
    return bool(getattr(tenant, "is_trialing", False) and tenant.subscription_active)


def founding_starter_gift_active(tenant) -> bool:
    try:
        from flask import has_app_context

        from app.services.founding_program import gift_active_for_tenant

        if not has_app_context():
            return bool(getattr(tenant, "_founding_gift_active", False))
        return gift_active_for_tenant(tenant)
    except Exception:
        return bool(getattr(tenant, "_founding_gift_active", False))


def has_feature(tenant, feature: str) -> bool:
    if not tenant or not tenant.subscription_active:
        return False
    if trial_has_all_features(tenant):
        return True
    plan = (getattr(tenant, "plan", None) or "trial").lower()
    if founding_starter_gift_active(tenant):
        plan = "starter"
    return feature in PLAN_FEATURES.get(plan, frozenset())


def max_team_users(tenant) -> int | None:
    if trial_has_all_features(tenant):
        return MAX_TEAM_USERS["premium"]
    plan = (getattr(tenant, "plan", None) or "starter").lower()
    if founding_starter_gift_active(tenant):
        plan = "starter"
    return MAX_TEAM_USERS.get(plan, 1)


def call_quota(tenant) -> int | None:
    """Monthly included calls, or None when unlimited (active trial)."""
    if not tenant or not tenant.subscription_active:
        return 0
    if trial_has_all_features(tenant):
        return None
    if founding_starter_gift_active(tenant):
        return included_calls("starter") or 0
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


def over_quota(tenant) -> bool:
    """True once this month's calls have passed the plan's included allowance.

    Calls keep being answered past this point — see :func:`inbound_allowed` —
    and the excess is invoiced by ``scripts/bill_overage.py``.
    """
    quota = call_quota(tenant)
    if quota is None:
        return False
    return calls_used(tenant) >= quota


def inbound_allowed(tenant) -> tuple[bool, str | None]:
    """Whether a new inbound call / lead capture is allowed.

    Only an inactive subscription stops a call. Exhausting the monthly
    allowance does not: the included calls are what the plan *covers*, not a
    hard ceiling, and the excess is billed per call (``CALL_OVERAGE_PRICE_CENTS``)
    on the next invoice.

    This used to refuse the call at the allowance, which made the overage
    unreachable in practice: a refused call creates no lead, usage could
    therefore never exceed the quota, ``overage_calls`` was always zero and the
    monthly billing job invoiced nothing, for anyone, ever. It also meant an
    artisan's line went dead mid-month — the failure mode a missed-call product
    exists to prevent.
    """
    if not tenant or not tenant.subscription_active:
        return False, "expired"
    return True, None


def upgrade_label(feature: str) -> str:
    plan = UPGRADE_PLAN_FOR.get(feature, "pro")
    return plan.capitalize()


def plan_summary(tenant) -> dict:
    """Snapshot for templates / API."""
    from app.services.billing import overage_price_cents

    quota = call_quota(tenant)
    used = calls_used(tenant)
    extra = max(0, used - quota) if quota is not None else 0
    plan = getattr(tenant, "plan", "trial")
    if founding_starter_gift_active(tenant) and not getattr(tenant, "is_paid", False):
        plan = "starter"
    return {
        "plan": plan,
        "trial_all_features": trial_has_all_features(tenant),
        "founding_starter_gift": founding_starter_gift_active(tenant),
        "subscription_active": bool(tenant.subscription_active),
        "call_quota": quota,
        "calls_used": used,
        "calls_remaining": (max(0, quota - used) if quota is not None else None),
        # Calls past the allowance are answered and billed; showing the running
        # total is the difference between a known cost and a surprise invoice.
        "overage_calls": extra,
        "overage_amount_cents": extra * overage_price_cents(),
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
