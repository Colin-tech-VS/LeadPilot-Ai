"""Twilio account snapshot for the admin console — balance, usage, console links.

Twilio auto-recharge is configured in the Twilio Console (Billing), not via this
API. Stripe subscriptions in PilotCore are separate: they bill artisans; funds
land in your Stripe account and do not top up Twilio automatically unless you
wire that up operationally or build a custom flow.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from flask import current_app

logger = logging.getLogger(__name__)


def is_configured() -> bool:
    return bool(
        current_app.config.get("TWILIO_ACCOUNT_SID")
        and current_app.config.get("TWILIO_AUTH_TOKEN")
    )


def _client():
    from twilio.rest import Client

    return Client(
        current_app.config["TWILIO_ACCOUNT_SID"],
        current_app.config["TWILIO_AUTH_TOKEN"],
    )


def console_url() -> str:
    sid = current_app.config.get("TWILIO_ACCOUNT_SID", "")
    if sid:
        return f"https://console.twilio.com/us1/account/manage-account?frameUrl=%2Fconsole%2Faccount%2Fbilling%3Fx-target-region%3Dus1"
    return "https://console.twilio.com/"


def billing_console_url() -> str:
    return "https://console.twilio.com/us1/billing/manage-billing/billing-overview"


def collect_status() -> dict:
    """Return Twilio balance + month-to-date usage for /admin/twilio."""
    out = {
        "configured": is_configured(),
        "account_sid": current_app.config.get("TWILIO_ACCOUNT_SID", ""),
        "console_url": console_url(),
        "billing_url": billing_console_url(),
        "auto_provision": bool(current_app.config.get("TWILIO_AUTO_PROVISION_NUMBERS")),
        "ai_phone": current_app.config.get("TWILIO_AI_PHONE_DISPLAY")
        or current_app.config.get("TWILIO_AI_PHONE_NUMBER", ""),
        "error": None,
        "balance": None,
        "balance_currency": None,
        "usage_rows": [],
        "usage_total_usd": None,
        "month_label": datetime.now(timezone.utc).strftime("%B %Y"),
        "stripe_overage_cents": current_app.config.get("CALL_OVERAGE_PRICE_CENTS", 50),
    }
    if not out["configured"]:
        out["error"] = "TWILIO_ACCOUNT_SID ou TWILIO_AUTH_TOKEN manquant."
        return out

    try:
        client = _client()
        bal = client.balance.fetch()
        out["balance"] = bal.balance
        out["balance_currency"] = bal.currency
    except Exception as exc:
        logger.warning("Twilio balance fetch failed: %s", exc)
        out["error"] = f"Solde Twilio inaccessible : {exc}"

    try:
        client = _client()
        today = date.today()
        start = today.replace(day=1).isoformat()
        categories = ("calls", "phonenumbers", "recordings", "transcriptions", "totalprice")
        rows = []
        total = 0.0
        for cat in categories:
            recs = client.usage.records.list(category=cat, start_date=start)
            if not recs:
                continue
            r = recs[0]
            try:
                price = float(r.price or 0)
            except (TypeError, ValueError):
                price = 0.0
            if cat == "totalprice":
                total = price
            rows.append(
                {
                    "category": cat,
                    "description": r.description or cat,
                    "usage": r.usage,
                    "usage_unit": r.usage_unit,
                    "price": price,
                    "price_unit": r.price_unit or "USD",
                }
            )
        out["usage_rows"] = rows
        out["usage_total_usd"] = total if total else sum(r["price"] for r in rows if r["category"] != "totalprice")
    except Exception as exc:
        logger.warning("Twilio usage fetch failed: %s", exc)
        if not out["error"]:
            out["error"] = f"Usage Twilio inaccessible : {exc}"

    return out
