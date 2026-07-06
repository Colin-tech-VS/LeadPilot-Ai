"""Automatic Twilio phone-number provisioning for each tenant.

The AI receptionist is a phone line, and the ONLY signal Twilio gives us to
know which plumber a caller wants is the number that was dialed (the ``To``
field, resolved in :mod:`app.routes.voice`). A single shared number therefore
cannot work in a real multi-tenant setup — every tenant needs their own
dedicated number.

This module buys that number automatically at signup and wires its voice
webhook straight to ``/voice/inbound?tenant_id=<id>`` so the plumber never has
to configure anything.

Everything here is **best-effort**: when Twilio is not configured, auto-provision
is disabled, or the purchase fails (e.g. Twilio regulatory bundle missing), the
tenant is simply left without ``ai_phone_number`` and the app falls back to the
shared ``TWILIO_AI_PHONE_NUMBER`` / ``TWILIO_DEFAULT_TENANT_ID``. Signup never
breaks because of a provisioning problem.
"""

import logging

from flask import current_app, has_request_context, request

logger = logging.getLogger(__name__)


def twilio_configured() -> bool:
    cfg = current_app.config
    return bool(cfg.get("TWILIO_ACCOUNT_SID") and cfg.get("TWILIO_AUTH_TOKEN"))


def auto_provision_enabled() -> bool:
    """Whether a dedicated number should be bought automatically at signup."""
    return bool(current_app.config.get("TWILIO_AUTO_PROVISION_NUMBERS")) and twilio_configured()


def _base_url() -> str | None:
    """Public base URL Twilio must call back on.

    Prefers the configured ``SERVER_NAME`` (set in production) so provisioning
    works outside a request too; falls back to the current request root.
    """
    cfg = current_app.config
    server_name = cfg.get("SERVER_NAME")
    if server_name:
        scheme = cfg.get("PREFERRED_URL_SCHEME", "https")
        return f"{scheme}://{server_name}"
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


def _search_number(client, country: str, area_code: str | None):
    """Return an available, voice-capable E.164 number, or None.

    Tries local numbers first, then mobile — some countries only offer one or
    the other for on-demand purchase.
    """
    for kind in ("local", "mobile"):
        try:
            catalog = getattr(client.available_phone_numbers(country), kind)
            kwargs = {"voice_enabled": True, "limit": 1}
            if area_code and kind == "local":
                kwargs["area_code"] = area_code
            found = catalog.list(**kwargs)
            if found:
                return found[0].phone_number
        except Exception:
            logger.debug("Twilio %s number search failed for %s", kind, country, exc_info=True)
    return None


def provision_ai_number(tenant) -> str | None:
    """Buy a dedicated AI number for ``tenant`` and wire its voice webhook.

    Sets ``tenant.ai_phone_number`` in place (the caller owns the DB commit) and
    returns the purchased E.164 number, or ``None`` when nothing was bought.
    Never raises.
    """
    if getattr(tenant, "ai_phone_number", None):
        return tenant.ai_phone_number  # already has a dedicated number

    if not auto_provision_enabled():
        logger.info(
            "Auto-provision disabled/unconfigured — tenant=%s keeps the shared number",
            getattr(tenant, "id", "?"),
        )
        return None

    voice_url = voice_webhook_url(str(tenant.id))
    if not voice_url:
        logger.warning(
            "No public base URL (set SERVER_NAME) — cannot provision a number for tenant=%s",
            tenant.id,
        )
        return None

    cfg = current_app.config
    country = (cfg.get("TWILIO_NUMBER_COUNTRY") or "FR").upper()
    area_code = cfg.get("TWILIO_NUMBER_AREA_CODE") or None

    try:
        from twilio.rest import Client

        client = Client(cfg["TWILIO_ACCOUNT_SID"], cfg["TWILIO_AUTH_TOKEN"])

        candidate = _search_number(client, country, area_code)
        if not candidate:
            logger.warning(
                "No voice-capable Twilio number available in %s for tenant=%s",
                country,
                tenant.id,
            )
            return None

        friendly = f"PilotCore — {tenant.name}"[:64]
        incoming = client.incoming_phone_numbers.create(
            phone_number=candidate,
            voice_url=voice_url,
            voice_method="POST",
            friendly_name=friendly,
        )
        number = incoming.phone_number
        tenant.ai_phone_number = number
        logger.info("Provisioned Twilio AI number %s for tenant=%s", number, tenant.id)
        return number
    except Exception:
        logger.exception("Twilio number provisioning failed for tenant=%s", tenant.id)
        return None
