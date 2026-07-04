"""Outbound SMS via Twilio.

Best-effort and optional: if Twilio is not configured (no SID / token / sender)
the helpers simply return ``False`` and log — they never raise, so the calling
flow (e.g. the voice AI booking an appointment) keeps working without SMS.
"""

import logging

from flask import current_app

logger = logging.getLogger(__name__)

# Twilio caps a single SMS segment at 160 chars but concatenates longer bodies;
# keep a sane upper bound so a runaway body can't be sent.
MAX_SMS_LEN = 1200


def sms_configured() -> bool:
    cfg = current_app.config
    sender = cfg.get("TWILIO_SMS_FROM") or cfg.get("TWILIO_AI_PHONE_NUMBER")
    return bool(cfg.get("TWILIO_ACCOUNT_SID") and cfg.get("TWILIO_AUTH_TOKEN") and sender)


def normalize_msisdn(phone: str) -> str | None:
    """Best-effort E.164 for the French market.

    Twilio requires E.164 (e.g. +33612345678). A local French number written
    "06 12 34 56 78" is converted to +33...; anything already starting with "+"
    is kept as-is.
    """
    if not phone:
        return None
    raw = phone.strip()
    digits = "".join(c for c in raw if c.isdigit())
    if not digits:
        return None
    if raw.startswith("+"):
        return "+" + digits
    # International access code "00" (e.g. 0033...) -> "+".
    if digits.startswith("00"):
        return "+" + digits[2:]
    # French mobile/landline: 10 digits starting with 0 -> +33 and drop the 0.
    if len(digits) == 10 and digits.startswith("0"):
        return "+33" + digits[1:]
    if len(digits) == 11 and digits.startswith("33"):
        return "+" + digits
    # Unknown format — return the raw digits with a leading + so Twilio can try.
    return "+" + digits


def send_sms(to: str, body: str) -> bool:
    """Send an SMS. Returns True on success, False otherwise. Never raises."""
    to_e164 = normalize_msisdn(to)
    body = (body or "").strip()
    if not to_e164 or not body:
        return False

    if not sms_configured():
        logger.info("SMS skipped — Twilio not configured (to=%s)", to_e164)
        return False

    cfg = current_app.config
    from_number = cfg.get("TWILIO_SMS_FROM") or cfg.get("TWILIO_AI_PHONE_NUMBER")

    try:
        from twilio.rest import Client

        client = Client(cfg["TWILIO_ACCOUNT_SID"], cfg["TWILIO_AUTH_TOKEN"])
        message = client.messages.create(
            body=body[:MAX_SMS_LEN],
            from_=from_number,
            to=to_e164,
        )
        logger.info("SMS sent sid=%s to=%s", getattr(message, "sid", "?"), to_e164)
        return True
    except Exception:
        logger.exception("SMS send failed to=%s", to_e164)
        return False
