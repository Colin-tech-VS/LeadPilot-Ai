"""What happens between « la page inscription s'affiche » and « le compte existe ».

The traffic funnel used to jump straight from *page visitée* to *compte créé*.
When that last step read zero there was no way to tell the three very different
stories behind it apart:

* nobody ever pressed the button (a page / offer problem),
* people pressed it and the server turned them away (a bug or a rule too
  strict — e-mail already taken, SIRET refused, rate limit…),
* the visits were never human to begin with (a scanner hitting ``/register``
  keeps no cookie, so every hit counted as a brand-new unique visitor).

Every POST to a sign-up form now records its outcome here, so the funnel can
show the missing step and the admin can read *why* attempts failed. No PII is
stored: the e-mail address and the password never leave the request.
"""
from __future__ import annotations

import logging
from collections import Counter

from flask import request

from app.models.event import CAT_AUTH, LEVEL_ERROR, LEVEL_INFO, LEVEL_SUCCESS, Event
from app.services.events import log_event

logger = logging.getLogger(__name__)

ACTION = "signup_attempt"

# Which form was posted. Mirrors the paths in ``traffic.REGISTER_PATHS``.
FORM_ARTISAN = "artisan"
FORM_CUSTOMER = "customer"
FORM_FOUNDING = "founding"

# How the attempt ended.
OUTCOME_OK = "ok"                # account created
OUTCOME_ERROR = "error"          # rejected, see ``reason``
OUTCOME_RATE_LIMITED = "rate_limited"
OUTCOME_CLAIM = "claim_prompt"   # sent back to pick a registry listing

# Human labels for the admin. Keys are the ``reason`` recorded with an attempt.
REASON_LABELS = {
    "required": "Champ obligatoire vide",
    "password_mismatch": "Mots de passe différents",
    "password_short": "Mot de passe trop court",
    "invalid_email": "E-mail invalide",
    "email_taken": "E-mail déjà utilisé",
    "phone_taken": "Téléphone déjà utilisé",
    "siret_invalid": "SIRET invalide",
    "trade_invalid": "Métier invalide",
    "listing_claim": "Fiche à réclamer (2ᵉ envoi demandé)",
    "rate_limited": "Bloqué par la limite de tentatives",
    "server_error": "Erreur serveur",
}

_LEVELS = {
    OUTCOME_OK: LEVEL_SUCCESS,
    OUTCOME_ERROR: LEVEL_ERROR,
    OUTCOME_RATE_LIMITED: LEVEL_ERROR,
    OUTCOME_CLAIM: LEVEL_INFO,
}


def _visitor_id():
    """The long-lived ``lp_vid`` cookie, so an attempt can be tied to the same
    visitor the traffic tables count — without identifying the person."""
    from app.core.tracking import VISITOR_COOKIE

    try:
        return request.cookies.get(VISITOR_COOKIE)
    except Exception:
        return None


def record_attempt(form: str, outcome: str, reason: str | None = None) -> None:
    """Record one sign-up form submission.

    This sits on the critical path of the sign-up form, so it swallows
    everything: measuring why sign-ups fail must never become a reason one
    fails. ``log_event`` already guards its own database write — the try here
    covers the rest (reading the cookie, the user agent, the event log itself
    being unavailable).
    """
    try:
        log_event(
            CAT_AUTH,
            ACTION,
            summary=f"{form}: {outcome}" + (f" ({reason})" if reason else ""),
            level=_LEVELS.get(outcome, LEVEL_INFO),
            actor="visitor",
            meta={
                "form": form,
                "outcome": outcome,
                "reason": reason,
                "visitor": _visitor_id(),
                "device": "mobile" if _is_mobile() else "desktop",
            },
        )
    except Exception:  # pragma: no cover - defensive, mirrors log_event itself
        logger.exception("Failed to record a %s sign-up attempt", form)


def _is_mobile() -> bool:
    try:
        from app.core.tracking import _device

        return _device(request.headers.get("User-Agent", "")) == "mobile"
    except Exception:
        return False


def _rows(since, until=None, forms=None):
    q = Event.query.filter(Event.action == ACTION, Event.created_at >= since)
    if until is not None:
        q = q.filter(Event.created_at < until)
    rows = [e.get_meta() for e in q.all()]
    if forms:
        rows = [m for m in rows if m.get("form") in forms]
    return rows


def attempt_visitors(since, until=None, forms=None) -> int:
    """Distinct visitors who actually submitted a sign-up form.

    Visitors without a cookie fall back to counting one attempt each — better a
    slight over-count than silently dropping the very submissions this exists
    to surface.
    """
    known, anonymous = set(), 0
    for meta in _rows(since, until, forms):
        vid = meta.get("visitor")
        if vid:
            known.add(vid)
        else:
            anonymous += 1
    return len(known) + anonymous


def summary(since, until=None, forms=None) -> dict:
    """Attempt counts and the reasons submissions were turned away."""
    rows = _rows(since, until, forms)
    reasons = Counter()
    outcomes = Counter()
    for meta in rows:
        outcomes[meta.get("outcome") or "?"] += 1
        if meta.get("outcome") != OUTCOME_OK:
            reasons[meta.get("reason") or "?"] += 1

    return {
        "attempts": len(rows),
        "visitors": attempt_visitors(since, until, forms),
        "succeeded": outcomes.get(OUTCOME_OK, 0),
        "failed": len(rows) - outcomes.get(OUTCOME_OK, 0),
        "by_reason": [
            {
                "reason": reason,
                "label": REASON_LABELS.get(reason, reason),
                "count": count,
            }
            for reason, count in reasons.most_common(12)
        ],
    }
