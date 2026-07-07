"""Devis delivery: actually send a quote to the client by email and/or SMS.

The plumber clicks "Envoyer le devis" and the client receives a message with the
online link to view/accept the devis, plus — when an acompte (deposit) is due —
the plumber's bank details (RIB) configured once in Paramètres, so the client can
pay the deposit straight away.

Best-effort: each channel is attempted independently and never raises. Returns a
summary of what was sent so the UI can tell the plumber exactly what happened.
"""

import logging

from app.core.extensions import db
from app.models.email_message import STATUS_SENT, STATUS_SIMULATED
from app.services.sms import normalize_msisdn, send_sms

logger = logging.getLogger(__name__)


def _public_link(quote):
    """Build the client-facing devis URL (requires app + request context)."""
    quote.ensure_token()
    token = (quote.public_token or "").strip()
    if not token:
        return None
    try:
        from app.utils.seo import canonical_url

        return canonical_url(f"/quotes/public/{quote.id}/{token}")
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
        lines.append(
            "Vous pouvez signer le devis et régler l'acompte directement en ligne via le lien ci-dessus."
        )
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
         "channel": "email+sms", "any": True, "rib": True, "error": str|None}

    A ``None`` value means the channel was not attempted (no address for it).
    """
    quote.ensure_token()
    try:
        db.session.flush()
    except Exception:
        logger.exception("Could not persist public token before send quote=%s", quote.id)

    link = _public_link(quote)
    if not link:
        return {
            "email": None,
            "sms": None,
            "channel": None,
            "any": False,
            "rib": False,
            "error": "link",
        }

    wants = set(channels) if channels else {"email", "sms"}
    if not wants:
        wants = {"email", "sms"}

    email_res = None
    to_email = (quote.client_email or "").strip()
    if "email" in wants and to_email:
        subject = f"Votre devis {quote.number or ''} — {(tenant.name or '').strip()}".strip()
        try:
            from app.services.transactional_email import send_devis_to_client

            msg = send_devis_to_client(
                to_email,
                customer_name=quote.client_name,
                artisan_name=(tenant.name or "votre artisan").strip(),
                quote_number=quote.number,
                quote_total_ttc=quote.total_ttc,
                sign_url=link,
                deposit_amount=quote.deposit_amount,
                deposit_percent=quote.deposit_percent,
                rib_lines=_rib_lines(tenant),
                tenant_id=tenant.id,
            )
            email_res = msg is not None and getattr(msg, "status", None) in (
                STATUS_SENT,
                STATUS_SIMULATED,
            )
        except Exception:
            logger.exception("Devis email failed (quote=%s)", quote.id)
            email_res = False

    sms_res = None
    to_phone = (quote.client_phone or "").strip()
    sms_body = build_message(quote, tenant, link)
    if "sms" in wants and normalize_msisdn(to_phone):
        sms_res = send_sms(to_phone, sms_body)

    sent_channels = []
    if email_res:
        sent_channels.append("email")
    if sms_res:
        sent_channels.append("sms")
    channel = "+".join(sent_channels) or None

    error = None
    if not sent_channels:
        if not to_email and not normalize_msisdn(to_phone):
            error = "no_contact"
        elif "email" in wants and to_email and email_res is False:
            error = "email_failed"
        elif "sms" in wants and normalize_msisdn(to_phone) and sms_res is False:
            error = "sms_failed"
        else:
            error = "none"

    return {
        "email": email_res,
        "sms": sms_res,
        "channel": channel,
        "any": bool(sent_channels),
        "rib": tenant.has_bank_details and bool(quote.deposit_amount),
        "error": error,
    }


def resolve_channels(quote, requested):
    """Pick delivery channels from the form, falling back to available contacts."""
    chosen = [c for c in (requested or []) if c in ("email", "sms")]
    if chosen:
        return chosen
    auto = []
    if (quote.client_email or "").strip():
        auto.append("email")
    if normalize_msisdn((quote.client_phone or "").strip()):
        auto.append("sms")
    return auto or None
