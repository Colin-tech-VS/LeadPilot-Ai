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

# Reaching the account step of the form — the visitor picked a trade and is now
# looking at the fields that create the account. Recorded separately from
# ``ACTION`` because « personne n'a envoyé le formulaire » covers two opposite
# problems: nobody ever engaged with it (the page or the offer does not sell),
# or plenty of people started and the second step lost them (the form does).
# Without this step both read as a single empty row and the fix is a guess.
ACTION_STARTED = "signup_started"

# Which form was posted. Mirrors the paths in ``traffic.REGISTER_PATHS``.
FORM_ARTISAN = "artisan"
FORM_CUSTOMER = "customer"
FORM_FOUNDING = "founding"

# How the attempt ended.
OUTCOME_OK = "ok"                # account created
OUTCOME_ERROR = "error"          # rejected, see ``reason``
OUTCOME_RATE_LIMITED = "rate_limited"

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
    "rate_limited": "Bloqué par la limite de tentatives",
    "server_error": "Erreur serveur",
}

# Where the visitor was when they decided to sign up. The value is the ``?src=``
# carried by our own CTAs (``macros/artisan_cta.html``), stashed in the session
# by the sign-up routes. Everything organic lands on client-facing pages, so
# « aucun artisan ne s'inscrit » had no way of saying *which* of those pages the
# few who do come from — or that none of them convert at all.
SOURCE_LABELS = {
    "annuaire": "Annuaire / page métier × ville",
    "fiche-registre": "Fiche registre (SIREN)",
    "profil-artisan": "Profil d'un artisan",
    "guide-artisan": "Guide « trouver un artisan »",
    "depannage": "Page dépannage urgent",
    "prix": "Référentiel de prix",
    "blog": "Blog",
    # Not a page: the link carried by an outreach e-mail
    # (``campaign_render.merge_context``).
    "campagne": "E-mail de prospection",
}

# No ``?src=``: the visitor came through /pro, the navigation, an ad or a link
# we did not tag. Not a source of its own — the absence of one.
SOURCE_UNTAGGED = "(non balisé)"

_LEVELS = {
    OUTCOME_OK: LEVEL_SUCCESS,
    OUTCOME_ERROR: LEVEL_ERROR,
    OUTCOME_RATE_LIMITED: LEVEL_ERROR,
}


def _visitor_id():
    """The long-lived ``lp_vid`` cookie, so an attempt can be tied to the same
    visitor the traffic tables count — without identifying the person."""
    from app.core.tracking import VISITOR_COOKIE

    try:
        return request.cookies.get(VISITOR_COOKIE)
    except Exception:
        return None


def _source():
    """The CTA slug that sent this visitor to the form, or None.

    Read from the session rather than from the request: the ``?src=`` is on the
    GET that opened the form, never on the POST that sends it. Only ever a
    short slug written by our own templates — anything longer is truncated, and
    a missing session is simply no source.
    """
    try:
        from flask import session

        return (session.get("signup_src") or "").strip()[:40] or None
    except Exception:
        return None


def record_attempt(
    form: str,
    outcome: str,
    reason: str | None = None,
    listing_prompt: bool = False,
) -> None:
    """Record one sign-up form submission — exactly one event per submission.

    ``listing_prompt`` marks a sign-up that turned up a registry fiche to ask
    about later. It is a note on a *successful* attempt, not an outcome of its
    own: the account exists either way, so counting it as a failure would put
    a working sign-up in the « pourquoi les envois ont échoué » list.

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
                "listing_prompt": listing_prompt or None,
                "source": _source(),
                "visitor": _visitor_id(),
                "device": "mobile" if _is_mobile() else "desktop",
            },
        )
    except Exception:  # pragma: no cover - defensive, mirrors log_event itself
        logger.exception("Failed to record a %s sign-up attempt", form)


def record_start(form: str) -> None:
    """Record that a visitor reached the account step of a sign-up form.

    Beaconed from the page, so it is best-effort by nature: an ad blocker or a
    closed tab loses it. It is only ever read as a *distinct visitor* count, so
    a duplicate beacon from the same browser costs nothing and a lost one only
    under-counts the step it measures — never the sign-ups themselves.

    Like :func:`record_attempt`, it swallows everything: nothing measured here
    is worth breaking a sign-up over.
    """
    try:
        log_event(
            CAT_AUTH,
            ACTION_STARTED,
            summary=f"{form}: formulaire commencé",
            level=LEVEL_INFO,
            actor="visitor",
            meta={
                "form": form,
                "source": _source(),
                "visitor": _visitor_id(),
                "device": "mobile" if _is_mobile() else "desktop",
            },
        )
    except Exception:  # pragma: no cover - defensive, mirrors log_event itself
        logger.exception("Failed to record a %s sign-up start", form)


def _is_mobile() -> bool:
    try:
        from app.core.tracking import _device

        return _device(request.headers.get("User-Agent", "")) == "mobile"
    except Exception:
        return False


def _rows(since, until=None, forms=None, action=ACTION):
    q = Event.query.filter(Event.action == action, Event.created_at >= since)
    if until is not None:
        q = q.filter(Event.created_at < until)
    rows = [e.get_meta() for e in q.all()]
    if forms:
        rows = [m for m in rows if m.get("form") in forms]
    return rows


def _distinct_visitors(rows) -> int:
    """Visitors without a cookie fall back to counting one each — better a
    slight over-count than silently dropping the very events this surfaces."""
    known, anonymous = set(), 0
    for meta in rows:
        vid = meta.get("visitor")
        if vid:
            known.add(vid)
        else:
            anonymous += 1
    return len(known) + anonymous


def start_visitors(since, until=None, forms=None) -> int:
    """Distinct visitors who got as far as the account step of the form."""
    return _distinct_visitors(_rows(since, until, forms, action=ACTION_STARTED))


def attempt_visitors(since, until=None, forms=None) -> int:
    """Distinct visitors who actually submitted a sign-up form."""
    return _distinct_visitors(_rows(since, until, forms))


def summary(since, until=None, forms=None) -> dict:
    """Attempt counts and the reasons submissions were turned away."""
    rows = _rows(since, until, forms)
    reasons = Counter()
    outcomes = Counter()
    sources = Counter()
    source_signups = Counter()
    for meta in rows:
        outcomes[meta.get("outcome") or "?"] += 1
        if meta.get("outcome") != OUTCOME_OK:
            reasons[meta.get("reason") or "?"] += 1
        source = meta.get("source") or SOURCE_UNTAGGED
        sources[source] += 1
        if meta.get("outcome") == OUTCOME_OK:
            source_signups[source] += 1

    return {
        "attempts": len(rows),
        "visitors": attempt_visitors(since, until, forms),
        "succeeded": outcomes.get(OUTCOME_OK, 0),
        "failed": len(rows) - outcomes.get(OUTCOME_OK, 0),
        # Sign-ups that matched a registry fiche. Asked on the dashboard now,
        # so this measures how often the question comes up — not a failure.
        "listing_prompts": sum(1 for m in rows if m.get("listing_prompt")),
        # Which CTA actually brings artisans as far as sending the form, and
        # how many of those end up with an account.
        "by_source": [
            {
                "source": source,
                "label": SOURCE_LABELS.get(source, source),
                "attempts": count,
                "signups": source_signups.get(source, 0),
            }
            for source, count in sources.most_common(12)
        ],
        "by_reason": [
            {
                "reason": reason,
                "label": REASON_LABELS.get(reason, reason),
                "count": count,
            }
            for reason, count in reasons.most_common(12)
        ],
    }
