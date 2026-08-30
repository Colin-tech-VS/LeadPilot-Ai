"""What an artisan must do before PilotCore answers a single call.

The dashboard used to open on five counters at zero and a banner saying « votre
assistant téléphonique répond encore » — to an account with no phone line, no
call forwarding and no way to test either. Fourteen days later the trial
expired having handled nothing, so there was never anything to subscribe for.

This is the missing middle: four steps, in order, each one either done or
carrying the single action that finishes it. It is deliberately the same
checklist for everyone (trial, founding member, subscriber) so there is one
answer to « où en suis-je ? » instead of three.
"""
from __future__ import annotations

# Standard GSM call-forwarding codes. They are dialled on the artisan's own
# handset and understood by every French mobile operator, which is why the
# instructions are codes rather than « ask your operator »: the artisan keeps
# their number and their SIM, and only the calls they cannot take are handed
# over.
FORWARD_CODES = (
    # (key, when it applies, the code with {number} substituted)
    ("no_answer", "activation.forward_no_answer", "**61*{number}#"),
    ("busy", "activation.forward_busy", "**67*{number}#"),
    ("unreachable", "activation.forward_unreachable", "**62*{number}#"),
)

FORWARD_CANCEL_CODE = "##002#"


def _dial_code(template: str, number: str) -> str:
    """A forwarding code with the receptionist number inlined.

    E.164 is what the handset expects (``+33…``); spaces would break the code.
    """
    return template.format(number="".join(ch for ch in number if ch.isdigit() or ch == "+"))


def forwarding_instructions(number: str | None) -> list[dict]:
    """The three codes to dial, or an empty list when there is no line yet."""
    if not number:
        return []
    return [
        {"key": key, "label_key": label_key, "code": _dial_code(template, number)}
        for key, label_key, template in FORWARD_CODES
    ]


def _first_call_count(tenant_id) -> int:
    from app.models.lead import Lead

    return Lead.query.filter(Lead.tenant_id == tenant_id).count()


def profile_complete(tenant) -> bool:
    """Everything the receptionist needs before it can answer for this artisan."""
    return bool(
        tenant
        and (tenant.name or "").strip()
        and (tenant.city or "").strip()
        and (tenant.phone_number or "").strip()
        and (tenant.trade_type or "").strip()
    )


def checklist(tenant) -> dict:
    """The activation state of one artisan.

    Returns ``steps`` (ordered, each with ``done`` and a ``cta`` key naming what
    finishes it), ``next_step`` — the first unfinished one, which is the only
    thing the dashboard needs to show — and ``complete``.
    """
    from app.services import twilio_provisioning

    line = twilio_provisioning.line_state(tenant)
    has_profile = profile_complete(tenant)
    calls = _first_call_count(tenant.id) if tenant is not None else 0

    steps = [
        {
            "key": "account",
            "done": True,
            "cta": None,
        },
        {
            "key": "profile",
            "done": has_profile,
            "cta": "settings",
        },
        {
            "key": "line",
            # A recorded request that has not produced a number yet is not done:
            # saying otherwise is how the dashboard came to claim an assistant
            # was answering when nothing was.
            "done": bool(line["number"]),
            "cta": "activate_line",
            "blocked_by": None if has_profile else "profile",
        },
        {
            "key": "forwarding",
            "done": calls >= 1,
            "cta": "test_call",
        },
    ]
    done = sum(1 for step in steps if step["done"])
    next_step = next((step for step in steps if not step["done"]), None)
    return {
        "steps": steps,
        "done": done,
        "total": len(steps),
        "pct": int(round(100 * done / len(steps))),
        "complete": next_step is None,
        "next_step": next_step,
        "line": line,
        "calls": calls,
        "forwarding": forwarding_instructions(line["number"]),
        "forward_cancel_code": FORWARD_CANCEL_CODE,
    }
