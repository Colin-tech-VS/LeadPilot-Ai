"""System diagnostics for the admin console (/admin/diagnostics).

Reads the running configuration (populated from Scalingo environment variables)
and reports, group by group, which integrations are wired up and which
variables are still missing. Secret values are never returned in full — only a
masked preview so an admin can confirm *a* value is set without exposing it.

Nothing here mutates state; it only inspects ``current_app.config``. Live
connectivity probes (database ``SELECT 1``, SMTP login) are exposed as separate
functions the routes call on demand.
"""
import logging

from flask import current_app
from sqlalchemy import text

from app.core.extensions import db

logger = logging.getLogger(__name__)

# Statuses used by the template for colour coding.
OK = "ok"
MISSING = "missing"
WARN = "warn"
INFO = "info"


def _mask(value, keep=2):
    """Return a masked preview of a secret so it can be shown without leaking it."""
    if value is None:
        return ""
    s = str(value)
    if not s:
        return ""
    if len(s) <= keep:
        return "•" * len(s)
    return s[:keep] + "•" * min(len(s) - keep, 8)


def _get(key, default=""):
    return current_app.config.get(key, default)


def _check(label, key, *, secret=False, required=False, show_value=False, hint=""):
    """Build one row describing a single environment variable."""
    raw = _get(key)
    present = bool(raw not in (None, "", 0) or (raw == 0 and key in _ZERO_OK))
    if isinstance(raw, bool):
        present = True  # a bool is always explicitly set
    status = OK if present else (MISSING if required else INFO)
    if present and secret:
        display = _mask(raw)
    elif present and show_value:
        display = str(raw)
    elif present:
        display = str(raw)
    else:
        display = "—"
    return {
        "label": label,
        "key": key,
        "value": display,
        "present": present,
        "required": required,
        "status": status,
        "hint": hint,
    }


# Keys where a literal 0 / False is a valid configured value.
_ZERO_OK = {"CALL_OVERAGE_PRICE_CENTS", "JWT_EXPIRATION_HOURS"}


def _group(title, icon, rows, *, note=""):
    required_missing = [r for r in rows if r["required"] and not r["present"]]
    any_present = any(r["present"] for r in rows)
    if required_missing:
        status = MISSING
    elif any_present:
        status = OK
    else:
        status = WARN
    return {
        "title": title,
        "icon": icon,
        "rows": rows,
        "status": status,
        "missing_count": len(required_missing),
        "note": note,
    }


def collect():
    """Return the full list of diagnostic groups for the page."""
    cfg = current_app.config
    is_prod = cfg.get("ENV") == "production"

    groups = []

    # ------------------------------------------------------------- Core / app
    groups.append(
        _group(
            "Application",
            "⚙️",
            [
                _check("Environnement Flask", "ENV", show_value=True, required=True),
                _check("Clé secrète app", "SECRET_KEY", secret=True, required=True,
                       hint="SECRET_KEY — signature des sessions"),
                _check("Clé JWT", "JWT_SECRET_KEY", secret=True, required=True),
                _check("URL publique", "PUBLIC_BASE_URL", show_value=True, required=True,
                       hint="PUBLIC_BASE_URL — liens e-mail, webhooks Twilio/Stripe"),
            ],
        )
    )

    # ------------------------------------------------------------- Database
    groups.append(
        _group(
            "Base de données (Supabase)",
            "🗄️",
            [
                _check("URL de connexion", "SQLALCHEMY_DATABASE_URI", secret=True, required=True,
                       hint="DATABASE_URL — pooler Supabase :6543 (IPv4) requis sur Scalingo"),
            ],
        )
    )

    # ------------------------------------------------------------- Email (SMTP out)
    smtp_rows = [
        _check("Serveur SMTP", "SMTP_HOST", show_value=True, required=True,
               hint="SMTP_HOST — ex. mail.pilotcore.fr"),
        _check("Port SMTP", "SMTP_PORT", show_value=True,
               hint="465 (SSL) ou 587 (STARTTLS)"),
        _check("Identifiant SMTP", "SMTP_USER", show_value=True, required=True),
        _check("Mot de passe SMTP", "SMTP_PASSWORD", secret=True, required=True,
               hint="SMTP_PASSWORD — sans lui l'authentification LWS échoue"),
        _check("SSL", "SMTP_USE_SSL", show_value=True),
        _check("STARTTLS", "SMTP_USE_TLS", show_value=True),
        _check("Expéditeur", "EMAIL_FROM", show_value=True, required=True),
    ]
    smtp_note = (
        "Emails transactionnels (bienvenue, réinitialisation, confirmation de RDV) "
        "et console. Sans configuration complète, les envois sont simulés — donc "
        "jamais réellement délivrés."
    )
    groups.append(_group("Email sortant (SMTP)", "✉️", smtp_rows, note=smtp_note))

    # ------------------------------------------------------------- Email (IMAP in)
    groups.append(
        _group(
            "Email entrant (IMAP)",
            "📥",
            [
                _check("Serveur IMAP", "IMAP_HOST", show_value=True),
                _check("Port IMAP", "IMAP_PORT", show_value=True),
                _check("Identifiant IMAP", "IMAP_USER", show_value=True),
                _check("Mot de passe IMAP", "IMAP_PASSWORD", secret=True),
                _check("Secret webhook entrant", "EMAIL_INBOUND_SECRET", secret=True,
                       required=is_prod,
                       hint="EMAIL_INBOUND_SECRET — garde /admin/email/inbound"),
            ],
        )
    )

    # ------------------------------------------------------------- Admin
    admin_rows = [
        _check("Utilisateur admin", "ADMIN_USERNAME", show_value=True, required=True),
    ]
    has_pw = bool(cfg.get("ADMIN_PASSWORD") or cfg.get("ADMIN_PASSWORD_HASH"))
    admin_rows.append({
        "label": "Mot de passe admin",
        "key": "ADMIN_PASSWORD",
        "value": _mask(cfg.get("ADMIN_PASSWORD")) if cfg.get("ADMIN_PASSWORD")
        else ("hash défini" if cfg.get("ADMIN_PASSWORD_HASH") else "—"),
        "present": has_pw,
        "required": True,
        "status": OK if has_pw else MISSING,
        "hint": "ADMIN_PASSWORD (ou ADMIN_PASSWORD_HASH)",
    })
    groups.append(_group("Console admin", "🔐", admin_rows))

    # ------------------------------------------------------------- Twilio
    groups.append(
        _group(
            "Téléphonie (Twilio)",
            "📞",
            [
                _check("Account SID", "TWILIO_ACCOUNT_SID", secret=True),
                _check("Auth token", "TWILIO_AUTH_TOKEN", secret=True),
                _check("Numéro IA", "TWILIO_AI_PHONE_NUMBER", show_value=True),
                _check("Tenant par défaut", "TWILIO_DEFAULT_TENANT_ID", show_value=True),
                _check("Validation signature", "TWILIO_VALIDATE_SIGNATURE", show_value=True),
                _check("Provision auto numéros", "TWILIO_AUTO_PROVISION_NUMBERS", show_value=True),
            ],
        )
    )

    # ------------------------------------------------------------- Stripe
    groups.append(
        _group(
            "Facturation (Stripe)",
            "💳",
            [
                _check("Clé secrète", "STRIPE_SECRET_KEY", secret=True),
                _check("Clé publique", "STRIPE_PUBLISHABLE_KEY", show_value=True),
                _check("Secret webhook", "STRIPE_WEBHOOK_SECRET", secret=True),
            ],
        )
    )

    # ------------------------------------------------------------- AI
    groups.append(
        _group(
            "Intelligence artificielle",
            "🤖",
            [
                _check("Mistral API key", "MISTRAL_API_KEY", secret=True,
                       hint="MISTRAL_API_KEY — extraction leads / chat"),
                _check("Modèle Mistral", "MISTRAL_MODEL", show_value=True),
                _check("OpenAI API key", "OPENAI_API_KEY", secret=True,
                       hint="Optionnel — Whisper STT + TTS neural"),
            ],
        )
    )

    return groups


def summary(groups):
    """Aggregate counts for the header banner."""
    total = sum(len(g["rows"]) for g in groups)
    missing_required = sum(g["missing_count"] for g in groups)
    return {"total": total, "missing_required": missing_required}


def database_probe():
    """Live check: can we reach the database?"""
    try:
        db.session.execute(text("SELECT 1"))
        db.session.commit()
        return {"ok": True, "detail": "Connexion établie (SELECT 1)."}
    except Exception as exc:  # pragma: no cover - depends on live DB
        db.session.rollback()
        logger.exception("Diagnostics DB probe failed")
        return {"ok": False, "detail": str(exc)[:300]}
