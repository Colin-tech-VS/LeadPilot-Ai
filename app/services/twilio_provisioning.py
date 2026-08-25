"""Twilio phone-number provisioning for paying tenants.

The AI receptionist is a phone line, and the ONLY signal Twilio gives us to
know which plumber a caller wants is the number that was dialed (the ``To``
field, resolved in :mod:`app.routes.voice`). Paying artisans therefore get a
dedicated number. Trials, test signups and unpaid accounts share
``TWILIO_AI_PHONE_NUMBER`` / ``TWILIO_DEFAULT_TENANT_ID`` — buying a FR line at
every ``/register`` is what drained the Twilio balance on dummy accounts.

Purchase is **best-effort** and never runs under pytest. Signup, founding
enrolment and listing-claim approval must not call this. Stripe checkout
(paid plan) does.
"""

import logging

from flask import current_app, has_request_context, request

logger = logging.getLogger(__name__)


def twilio_configured() -> bool:
    cfg = current_app.config
    return bool(cfg.get("TWILIO_ACCOUNT_SID") and cfg.get("TWILIO_AUTH_TOKEN"))


def auto_provision_enabled() -> bool:
    """Whether the app is allowed to buy dedicated numbers at all."""
    if current_app.config.get("TESTING"):
        return False
    return bool(current_app.config.get("TWILIO_AUTO_PROVISION_NUMBERS")) and twilio_configured()


def _digits(value: str | None) -> str:
    return "".join(ch for ch in (value or "") if ch.isdigit())


def _is_shared_line(number: str | None) -> bool:
    shared = current_app.config.get("TWILIO_AI_PHONE_NUMBER") or ""
    return bool(number and shared and _digits(number) == _digits(shared))


def should_buy_dedicated_number(tenant) -> bool:
    """True only for a paying artisan, on a live app, with buying enabled.

    Trials, waitlists, founding tests and pytest never qualify. That is the
    gate that stops a FR number (~1.35 USD/month) being bought per dummy signup.
    """
    if current_app.config.get("TESTING"):
        return False
    if not auto_provision_enabled():
        return False
    return bool(getattr(tenant, "is_paid", False))


def _base_url() -> str | None:
    """Public base URL Twilio must call back on.

    Prefers ``PUBLIC_BASE_URL`` (set in production) so provisioning works outside
    a request too; falls back to the current request root.
    """
    cfg = current_app.config
    public_base = cfg.get("PUBLIC_BASE_URL")
    if public_base:
        return str(public_base).rstrip("/")
    if has_request_context():
        return request.url_root.rstrip("/")
    return None


def voice_webhook_url(tenant_id: str) -> str | None:
    """The exact URL configured on the tenant's Twilio number.

    The ``tenant_id`` query param lets :func:`app.routes.voice._get_tenant_id`
    resolve the tenant directly (O(1)) instead of scanning every number, and
    Twilio signs this full URL so signature validation still passes.
    """
    base = _base_url()
    if not base:
        return None
    return f"{base}/voice/inbound?tenant_id={tenant_id}"


def _search_numbers(client, country: str, area_code: str | None, limit: int = 8):
    """Return a list of available, voice-capable E.164 numbers (local first, then mobile).

    We return several candidates on purpose: in regulated countries (e.g. FR) a
    given number range may not match the account's approved Regulatory Bundle
    ("does not have the correct regulation type"), so the caller tries each
    candidate until one purchase succeeds instead of giving up on the first.
    """
    candidates: list[str] = []
    for kind in ("local", "mobile"):
        try:
            catalog = getattr(client.available_phone_numbers(country), kind)
            kwargs = {"voice_enabled": True, "limit": limit}
            if area_code and kind == "local":
                kwargs["area_code"] = area_code
            for n in catalog.list(**kwargs):
                if n.phone_number not in candidates:
                    candidates.append(n.phone_number)
        except Exception:
            logger.debug("Twilio %s number search failed for %s", kind, country, exc_info=True)
    return candidates


def _address_from_bundle(client, bundle_sid: str) -> str | None:
    """Return the address SID embedded in an approved Regulatory Bundle.

    Twilio requires the ``address_sid`` passed at purchase to be the very address
    the bundle was built from — otherwise it rejects with "Address not contained
    in bundle". The address lives on one of the bundle's supporting documents
    (``address_sids`` attribute), so we read it straight from there rather than
    guessing the first address on the account.
    """
    try:
        assignments = (
            client.numbers.v2.regulatory_compliance.bundles(bundle_sid)
            .item_assignments.list(limit=20)
        )
        for ia in assignments:
            osid = getattr(ia, "object_sid", "") or ""
            if not osid.startswith("RD"):
                continue
            doc = client.numbers.v2.regulatory_compliance.supporting_documents(osid).fetch()
            addrs = (getattr(doc, "attributes", None) or {}).get("address_sids") or []
            if addrs:
                return addrs[0]
    except Exception:
        logger.debug("Could not derive address from bundle %s", bundle_sid, exc_info=True)
    return None


def _regulatory_ids(client, country: str):
    """Return (address_sid, bundle_sid) required to buy a regulated number.

    Prefers explicit config (TWILIO_ADDRESS_SID / TWILIO_BUNDLE_SID), otherwise
    auto-discovers an Address for ``country`` and a ``twilio-approved`` Regulatory
    Bundle on the account. Either may be None when not needed/available.
    """
    import os as _os

    cfg = current_app.config
    address_sid = cfg.get("TWILIO_ADDRESS_SID") or _os.environ.get("TWILIO_ADDRESS_SID")
    bundle_sid = cfg.get("TWILIO_BUNDLE_SID") or _os.environ.get("TWILIO_BUNDLE_SID")

    if not bundle_sid:
        try:
            for b in client.numbers.v2.regulatory_compliance.bundles.list(limit=50):
                if getattr(b, "status", "") == "twilio-approved":
                    bundle_sid = b.sid
                    break
        except Exception:
            logger.debug("Twilio bundle lookup failed", exc_info=True)

    # The purchase address MUST be the one contained in the bundle. Derive it from
    # the bundle itself rather than picking the first FR address on the account
    # (which may belong to a different bundle → "Address not contained in bundle").
    if bundle_sid and not address_sid:
        address_sid = _address_from_bundle(client, bundle_sid)

    if not address_sid:
        try:
            for a in client.addresses.list(limit=50):
                if (getattr(a, "iso_country", "") or "").upper() == country:
                    address_sid = a.sid
                    break
        except Exception:
            logger.debug("Twilio address lookup failed", exc_info=True)

    logger.info("Regulatory ids for %s: address=%s bundle=%s", country, address_sid, bundle_sid)
    return address_sid, bundle_sid


def provision_ai_number(tenant, *, force: bool = False) -> str | None:
    """Buy a dedicated AI number for a **paying** tenant and wire its webhook.

    Sets ``tenant.ai_phone_number`` in place (the caller owns the DB commit) and
    returns the purchased E.164 number, or ``None`` when nothing was bought.
    Never raises. Pass ``force=True`` only for an explicit admin override.
    """
    existing = getattr(tenant, "ai_phone_number", None)
    if existing and not _is_shared_line(existing):
        return existing

    if not force and not should_buy_dedicated_number(tenant):
        logger.info(
            "Skip Twilio purchase for unpaid/test tenant=%s — shared line",
            getattr(tenant, "id", "?"),
        )
        return None

    if not auto_provision_enabled() and not force:
        logger.info(
            "Auto-provision disabled/unconfigured — tenant=%s keeps the shared number",
            getattr(tenant, "id", "?"),
        )
        return None

    # Pytest loads the real .env. Never buy or release against the live account.
    if current_app.config.get("TESTING"):
        return None

    voice_url = voice_webhook_url(str(tenant.id))
    if not voice_url:
        logger.warning(
            "No public base URL (set PUBLIC_BASE_URL) — cannot provision a number for tenant=%s",
            tenant.id,
        )
        return None

    cfg = current_app.config
    country = (cfg.get("TWILIO_NUMBER_COUNTRY") or "FR").upper()
    area_code = cfg.get("TWILIO_NUMBER_AREA_CODE") or None

    try:
        from twilio.rest import Client

        client = Client(cfg["TWILIO_ACCOUNT_SID"], cfg["TWILIO_AUTH_TOKEN"])

        candidates = _search_numbers(client, country, area_code)
        if not candidates:
            logger.warning(
                "No voice-capable Twilio number available in %s for tenant=%s",
                country,
                tenant.id,
            )
            return None

        # Regulated numbers (e.g. FR, address_requirements="local") require an
        # Address and, for regulated countries, an approved Regulatory Bundle to
        # be attached at purchase — otherwise Twilio rejects the create().
        address_sid, bundle_sid = _regulatory_ids(client, country)
        base_kwargs = {
            "voice_url": voice_url,
            "voice_method": "POST",
            "friendly_name": f"PilotCore — {tenant.name}"[:64],
        }
        if address_sid:
            base_kwargs["address_sid"] = address_sid
        if bundle_sid:
            base_kwargs["bundle_sid"] = bundle_sid

        # Try each candidate until one purchase succeeds. In FR some number ranges
        # don't match the account's bundle ("does not have the correct regulation
        # type"); skipping to the next candidate finds a compatible one instead of
        # abandoning the whole signup after a single failure.
        last_exc = None
        for candidate in candidates:
            try:
                incoming = client.incoming_phone_numbers.create(
                    phone_number=candidate, **base_kwargs
                )
            except Exception as exc:  # noqa: BLE001 - try the next candidate
                last_exc = exc
                logger.warning(
                    "Twilio purchase attempt failed for tenant=%s (candidate=%s): %s",
                    tenant.id, candidate, exc,
                )
                continue
            number = incoming.phone_number
            tenant.ai_phone_number = number
            logger.info("Provisioned Twilio AI number %s for tenant=%s", number, tenant.id)
            return number

        logger.error(
            "All %d Twilio purchase attempts failed for tenant=%s. Last error: %s. %s",
            len(candidates), tenant.id, last_exc, _purchase_hint(last_exc),
        )
        return None
    except Exception:
        logger.exception("Twilio number provisioning failed for tenant=%s", tenant.id)
        return None


def release_ai_number(tenant) -> bool:
    """Release the tenant's dedicated Twilio number and clear the field.

    Never releases the shared ``TWILIO_AI_PHONE_NUMBER``. Best-effort: a Twilio
    outage or suspended account logs and returns False without raising, so
    account deletion can still proceed. Under pytest the field is cleared only.
    """
    number = (getattr(tenant, "ai_phone_number", None) or "").strip()
    if not number:
        return True
    if _is_shared_line(number):
        tenant.ai_phone_number = None
        return True
    if current_app.config.get("TESTING") or not twilio_configured():
        tenant.ai_phone_number = None
        return True

    try:
        from twilio.rest import Client

        cfg = current_app.config
        client = Client(cfg["TWILIO_ACCOUNT_SID"], cfg["TWILIO_AUTH_TOKEN"])
        matches = client.incoming_phone_numbers.list(phone_number=number, limit=5)
        if not matches:
            # Fallback: digit-match if Twilio wants a slightly different E.164.
            want = _digits(number)
            matches = [
                n for n in client.incoming_phone_numbers.list(limit=200)
                if _digits(n.phone_number) == want
            ]
        released = False
        for incoming in matches:
            incoming.delete()
            released = True
            logger.info(
                "Released Twilio number %s for tenant=%s",
                incoming.phone_number,
                getattr(tenant, "id", "?"),
            )
        tenant.ai_phone_number = None
        if not released:
            logger.warning(
                "No Twilio incoming number matched %s for tenant=%s — field cleared",
                number,
                getattr(tenant, "id", "?"),
            )
        return True
    except Exception:
        logger.exception(
            "Twilio number release failed for tenant=%s number=%s",
            getattr(tenant, "id", "?"),
            number,
        )
        return False


def _purchase_hint(exc) -> str:
    """Turn a Twilio purchase error into an actionable hint for the logs."""
    code = getattr(exc, "code", None)
    text = f"{getattr(exc, 'msg', '')} {exc}".lower()
    # Trial accounts cannot buy extra numbers; upgrade to a paid account.
    if "trial" in text or code == 21404:
        return (
            "Compte Twilio en mode ESSAI (Trial) — un compte d'essai ne peut pas "
            "acheter de numéros dédiés. Passez le compte en payant (ajoutez un moyen "
            "de paiement) dans la console Twilio."
        )
    # French numbers are regulated and need an approved Regulatory Bundle + Address.
    if "bundle" in text or "regulatory" in text or "address" in text or code in (21649, 21631):
        return (
            "Numéro FR réglementé — créez et faites approuver un Regulatory Bundle "
            "(pièce d'identité + adresse) dans Twilio > Regulatory Compliance, ou "
            "choisissez un pays non réglementé via TWILIO_NUMBER_COUNTRY."
        )
    return "Vérifiez le solde, le bundle réglementaire et les permissions du compte Twilio."
