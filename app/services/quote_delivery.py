"""Devis delivery: actually send a quote to the client by email and/or SMS.

The plumber clicks "Envoyer le devis" and the client receives a message with the
online link to view/accept the devis, plus — when an acompte (deposit) is due —
the plumber's bank details (RIB) configured once in Paramètres, so the client can
pay the deposit straight away.

Best-effort: each channel is attempted independently and never raises. Returns a
summary of what was sent so the UI can tell the plumber exactly what happened.
"""

import logging

from flask import url_for

from app.services import admin_email
from app.services.sms import normalize_msisdn, send_sms

logger = logging.getLogger(__name__)


def _public_link(quote):
    quote.ensure_token()
    try:
        return url_for(
            "quotes.public_quote",
            quote_id=quote.id,
            token=quote.public_token,
            _external=True,
        )
    except Exception:
        logger.exception("Could not build public devis link (quote=%s)", quote.id)
        return None


def _rib_lines(tenant):
    """Bank-details block for the acompte, or [] when no RIB is configured."""
    if not tenant.has_bank_details:
        return []
    lines = ["Coordonnées bancaires pour l'acompte :"]
    if (tenant.bank_holder or "").strip():
        lines.append(f"Titulaire : {tenant.bank_holder.strip()}")
    lines.append(f"IBAN : {tenant.iban.strip()}")
    if (tenant.bic or "").strip():
        lines.append(f"BIC : {tenant.bic.strip()}")
    return lines


def _deposit_line(quote):
    amount = quote.deposit_amount
    if not amount:
        return None
    return (
        f"Un acompte de {amount:.2f} € "
        f"({quote.deposit_percent} %) est demandé pour lancer l'intervention."
    )


def build_message(quote, tenant, link):
    """Plain-text body shared by email and SMS."""
    company = (tenant.name or "votre artisan").strip()
    greeting = quote.client_name.strip() if (quote.client_name or "").strip() else None
    hello = f"Bonjour {greeting}," if greeting else "Bonjour,"

    lines = [
        hello,
        "",
        f"Voici votre devis {quote.number or ''} de {company}"
        f" (montant {quote.total_ttc:.2f} € TTC).".replace("  ", " "),
        f"Consultez-le et validez-le en ligne : {link}",
    ]
    deposit = _deposit_line(quote)
    if deposit:
        lines += ["", deposit]
    rib = _rib_lines(tenant)
    if rib:
        lines += [""] + rib
    lines += ["", "Merci et à bientôt,", company]
    return "\n".join(lines)


def send_quote(quote, tenant, channels=None):
    """Send the devis to the client. Returns a result dict.

    ``channels`` is an iterable subset of {"email", "sms"}; when None both are
    attempted where a destination exists. Result::

        {"email": True/False/None, "sms": True/False/None,
         "channel": "email+sms", "any": True, "rib": True}

    A ``None`` value means the channel was not attempted (no address for it).
    """
    link = _public_link(quote)
    if not link:
        return {"email": None, "sms": None, "channel": None, "any": False, "rib": False, "error": "link"}

    body = build_message(quote, tenant, link)
    wants = set(channels) if channels else {"email", "sms"}

    email_res = None
    to_email = (quote.client_email or "").strip()
    if "email" in wants and to_email:
        subject = f"Votre devis {quote.number or ''} — {(tenant.name or '').strip()}".strip()
        try:
            msg = admin_email.send_email(
                to_email, subject, body, is_html=False, tenant_id=tenant.id
            )
            # "sent" or "simulated" both mean the message left the app cleanly.
            email_res = getattr(msg, "status", None) in ("sent", "simulated")
        except Exception:
            logger.exception("Devis email failed (quote=%s)", quote.id)
            email_res = False

    sms_res = None
    to_phone = (quote.client_phone or "").strip()
    if "sms" in wants and normalize_msisdn(to_phone):
        sms_res = send_sms(to_phone, body)

    sent_channels = []
    if email_res:
        sent_channels.append("email")
    if sms_res:
        sent_channels.append("sms")
    channel = "+".join(sent_channels) or None

    return {
        "email": email_res,
        "sms": sms_res,
        "channel": channel,
        "any": bool(sent_channels),
        "rib": tenant.has_bank_details and bool(quote.deposit_amount),
    }
