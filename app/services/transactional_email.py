"""Transactional emails with a single, shared PilotCore design.

Every automatic email (welcome, password change, booking confirmation…) is
rendered through :func:`render_email` so they all look identical, then sent via
:func:`app.services.admin_email.send_email` — which also records the message in
``EmailMessage`` so the admin console (/admin/emails) shows every mail we send.

Sending never raises: a transactional email must never break the user action
that triggered it (signup, booking…). Failures are logged and swallowed.
"""

import logging

from flask import current_app

logger = logging.getLogger(__name__)

BRAND = "PilotCore"
BRAND_COLOR = "#059CE0"
BRAND_DARK = "#0B1F33"


def _base_url() -> str:
    cfg = current_app.config
    return str(cfg.get("PUBLIC_BASE_URL") or "https://www.pilotcore.fr").rstrip("/")


def render_email(
    title: str,
    intro: str,
    *,
    lines: list[str] | None = None,
    cta_label: str | None = None,
    cta_url: str | None = None,
    outro: str | None = None,
    preheader: str | None = None,
) -> str:
    """Return the full branded HTML for a transactional email.

    ``lines`` are rendered as paragraphs (already-escaped/plain text). Keep the
    markup table-based and inline-styled for broad email-client support.
    """
    base = _base_url()
    body_blocks = [f'<p style="margin:0 0 16px;font-size:16px;line-height:1.6;color:#334155;">{intro}</p>']
    for ln in lines or []:
        body_blocks.append(
            f'<p style="margin:0 0 12px;font-size:15px;line-height:1.6;color:#334155;">{ln}</p>'
        )
    if cta_label and cta_url:
        body_blocks.append(
            f'''<table role="presentation" cellpadding="0" cellspacing="0" style="margin:24px 0;">
              <tr><td style="border-radius:12px;background:{BRAND_COLOR};">
                <a href="{cta_url}" style="display:inline-block;padding:14px 28px;font-size:16px;
                   font-weight:700;color:#ffffff;text-decoration:none;border-radius:12px;">{cta_label}</a>
              </td></tr>
            </table>'''
        )
    if outro:
        body_blocks.append(
            f'<p style="margin:16px 0 0;font-size:14px;line-height:1.6;color:#64748B;">{outro}</p>'
        )
    body_html = "\n".join(body_blocks)
    pre = preheader or intro

    return f'''<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#F1F5F9;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{pre}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#F1F5F9;padding:32px 12px;">
  <tr><td align="center">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
           style="max-width:560px;background:#ffffff;border-radius:18px;overflow:hidden;
                  box-shadow:0 12px 40px rgba(11,31,51,0.08);">
      <tr><td style="background:{BRAND_DARK};padding:22px 32px;">
        <span style="font-size:20px;font-weight:800;color:#ffffff;letter-spacing:-0.02em;">
          <span style="color:{BRAND_COLOR};">●</span> {BRAND}
        </span>
      </td></tr>
      <tr><td style="padding:32px;">
        <h1 style="margin:0 0 18px;font-size:22px;line-height:1.3;color:{BRAND_DARK};font-weight:800;">{title}</h1>
        {body_html}
      </td></tr>
      <tr><td style="padding:20px 32px;background:#F8FAFC;border-top:1px solid #E2E8F0;">
        <p style="margin:0;font-size:12px;line-height:1.6;color:#94A3B8;">
          {BRAND} — Réceptionniste IA & prise de RDV pour artisans.<br>
          <a href="{base}" style="color:{BRAND_COLOR};text-decoration:none;">{base.replace('https://','')}</a>
          &nbsp;·&nbsp;
          <a href="{base}/confidentialite" style="color:#94A3B8;text-decoration:none;">Confidentialité</a>
          &nbsp;·&nbsp;
          <a href="{base}/mentions-legales" style="color:#94A3B8;text-decoration:none;">Mentions légales</a>
        </p>
      </td></tr>
    </table>
  </td></tr>
</table>
</body></html>'''


def _send(to_addr, subject, html, text_body, tenant_id=None):
    """Deliver a transactional email. Never raises."""
    try:
        from app.services import admin_email

        return admin_email.send_email(
            to_addr=to_addr,
            subject=subject,
            body=text_body,
            is_html=True,
            html_body=html,
            tenant_id=tenant_id,
        )
    except Exception:
        logger.exception("Transactional email send failed to=%s subject=%s", to_addr, subject)
        return None


# --------------------------------------------------------------------------- #
#  Concrete transactional emails                                              #
# --------------------------------------------------------------------------- #

def send_artisan_welcome(user, tenant):
    if not user or not user.email:
        return None
    base = _base_url()
    name = (getattr(tenant, "first_name", None) or getattr(tenant, "name", None) or "").strip()
    hello = f"Bienvenue {name}," if name else "Bienvenue,"
    html = render_email(
        "Votre compte PilotCore est prêt 🎉",
        hello,
        lines=[
            "Votre espace artisan est créé. Votre assistant vocal IA et votre fiche "
            "publique sur l'annuaire sont désormais actifs.",
            "Connectez-vous à votre tableau de bord pour configurer votre profil, "
            "vos horaires et suivre vos demandes de RDV.",
        ],
        cta_label="Accéder à mon tableau de bord",
        cta_url=f"{base}/dashboard",
        outro="Besoin d'aide ? Répondez simplement à cet e-mail.",
    )
    text = f"{hello}\nVotre compte PilotCore est prêt. Tableau de bord : {base}/dashboard"
    return _send(user.email, "Bienvenue sur PilotCore", html, text, tenant_id=getattr(tenant, "id", None))


def send_customer_welcome(user):
    if not user or not user.email:
        return None
    base = _base_url()
    hello = f"Bienvenue {user.first_name}," if user.first_name else "Bienvenue,"
    html = render_email(
        "Votre compte est créé ✅",
        hello,
        lines=[
            "Vous pouvez désormais réserver un artisan en ligne en quelques clics et "
            "suivre vos rendez-vous depuis votre espace.",
        ],
        cta_label="Trouver un artisan",
        cta_url=f"{base}/artisans",
    )
    text = f"{hello}\nVotre compte PilotCore est créé. Trouvez un artisan : {base}/artisans"
    return _send(user.email, "Votre compte PilotCore est créé", html, text)


def send_voice_customer_credentials(user, password: str):
    """E-mail envoyé après création de compte par l'assistant vocal."""
    if not user or not user.email or not password:
        return None
    base = _base_url()
    login_url = f"{base}/client/login"
    account_url = f"{base}/client/account"
    hello = f"Bonjour {user.first_name}," if user.first_name else "Bonjour,"
    pwd_spelled = " ".join(password)
    html = render_email(
        "Votre compte PilotCore est prêt",
        hello,
        lines=[
            "Votre compte client a été créé lors de votre appel.",
            f"<strong>Identifiant :</strong> {user.email}",
            f"<strong>Mot de passe temporaire :</strong> {password}",
            "Pour votre sécurité, modifiez ce mot de passe dès votre première connexion.",
            "Vous pourrez suivre vos devis, signer en ligne et gérer vos rendez-vous.",
        ],
        cta_label="Me connecter",
        cta_url=login_url,
        outro=f"Espace client : {account_url}",
    )
    text = (
        f"{hello}\n\n"
        f"Votre compte PilotCore a été créé.\n"
        f"Identifiant : {user.email}\n"
        f"Mot de passe temporaire : {password}\n\n"
        f"Connectez-vous sur {login_url} et changez votre mot de passe.\n"
        f"Espace client : {account_url}"
    )
    return _send(user.email, "Votre compte PilotCore — identifiants", html, text)


def send_password_reset(user, reset_url):
    if not user or not user.email:
        return None
    html = render_email(
        "Réinitialisation de votre mot de passe",
        "Bonjour,",
        lines=[
            "Vous avez demandé à réinitialiser votre mot de passe PilotCore. "
            "Cliquez sur le bouton ci-dessous pour en choisir un nouveau.",
            "Ce lien est valable 1 heure. Si vous n'êtes pas à l'origine de cette "
            "demande, ignorez simplement cet e-mail — votre mot de passe reste inchangé.",
        ],
        cta_label="Choisir un nouveau mot de passe",
        cta_url=reset_url,
        outro="Pour votre sécurité, ne transférez ce lien à personne.",
    )
    text = f"Réinitialisez votre mot de passe PilotCore (valable 1h) : {reset_url}"
    return _send(user.email, "Réinitialisation de votre mot de passe", html, text,
                 tenant_id=getattr(user, "tenant_id", None))


def send_password_changed(user):
    if not user or not user.email:
        return None
    base = _base_url()
    html = render_email(
        "Votre mot de passe a été modifié",
        "Bonjour,",
        lines=[
            "Nous vous confirmons que le mot de passe de votre compte PilotCore vient "
            "d'être modifié.",
            "Si vous n'êtes pas à l'origine de ce changement, contactez-nous "
            "immédiatement afin de sécuriser votre compte.",
        ],
        cta_label="Se connecter",
        cta_url=f"{base}/login",
        outro="Cet e-mail est envoyé automatiquement pour la sécurité de votre compte.",
    )
    text = "Votre mot de passe PilotCore a été modifié. Si ce n'est pas vous, contactez-nous."
    return _send(user.email, "Votre mot de passe a été modifié", html, text,
                 tenant_id=getattr(user, "tenant_id", None))


def send_appointment_confirmation(to_addr, when_label, artisan_name, *, customer_name=None,
                                  tenant_id=None, address=None):
    if not to_addr:
        return None
    base = _base_url()
    hello = f"Bonjour {customer_name}," if customer_name else "Bonjour,"
    lines = [
        f"Votre rendez-vous avec <strong>{artisan_name}</strong> est confirmé pour "
        f"<strong>{when_label}</strong>.",
    ]
    if address:
        lines.append(f"Adresse : {address}")
    lines.append("Vous recevrez un rappel avant l'intervention. À bientôt !")
    html = render_email(
        "Rendez-vous confirmé 📅",
        hello,
        lines=lines,
        cta_label="Voir mes rendez-vous",
        cta_url=f"{base}/client/account",
    )
    text = f"{hello}\nRDV confirmé avec {artisan_name} le {when_label}."
    return _send(to_addr, f"Rendez-vous confirmé — {artisan_name}", html, text, tenant_id=tenant_id)


def send_new_booking_to_artisan(to_addr, when_label, customer_name, *, tenant_id=None,
                                customer_phone=None, issue=None):
    if not to_addr:
        return None
    base = _base_url()
    lines = [
        f"Nouvelle réservation en ligne de <strong>{customer_name or 'un client'}</strong> "
        f"pour <strong>{when_label}</strong>.",
    ]
    if customer_phone:
        lines.append(f"Téléphone : {customer_phone}")
    if issue:
        lines.append(f"Demande : {issue}")
    html = render_email(
        "Nouvelle demande de RDV 🔔",
        "Bonjour,",
        lines=lines,
        cta_label="Voir dans mon agenda",
        cta_url=f"{base}/appointments",
    )
    text = f"Nouvelle réservation de {customer_name} le {when_label}."
    return _send(to_addr, "Nouvelle demande de rendez-vous", html, text, tenant_id=tenant_id)


def send_booking_quote_for_signature(
    to_addr,
    *,
    customer_name=None,
    artisan_name,
    when_label,
    quote_total_ttc,
    sign_url,
    tenant_id=None,
):
    """Ask the customer to sign the devis before the visit is confirmed."""
    if not to_addr or not sign_url:
        return None
    hello = f"Bonjour {customer_name}," if customer_name else "Bonjour,"
    html = render_email(
        "Signez votre devis pour confirmer le rendez-vous",
        hello,
        lines=[
            f"Vous avez demandé un créneau avec <strong>{artisan_name}</strong> "
            f"le <strong>{when_label}</strong>.",
            f"Un devis pré-rempli ({quote_total_ttc:.2f} € TTC) vous attend.",
            "L'artisan ne se déplace qu'après validation du devis en ligne.",
        ],
        cta_label="Signer le devis",
        cta_url=sign_url,
        outro="Le créneau reste réservé temporairement le temps de votre signature.",
    )
    text = (
        f"{hello}\nSignez votre devis pour confirmer le RDV du {when_label} "
        f"avec {artisan_name} : {sign_url}"
    )
    return _send(
        to_addr,
        f"Signez votre devis — RDV {when_label}",
        html,
        text,
        tenant_id=tenant_id,
    )


def send_booking_quote_pending_to_artisan(
    to_addr,
    *,
    customer_name,
    when_label,
    quote_number,
    tenant_id=None,
):
    """Tell the artisan a devis was sent and the slot awaits client signature."""
    if not to_addr:
        return None
    base = _base_url()
    html = render_email(
        "Devis envoyé — en attente de signature",
        "Bonjour,",
        lines=[
            f"<strong>{customer_name or 'Un client'}</strong> a demandé le créneau "
            f"du <strong>{when_label}</strong>.",
            f"Le devis <strong>{quote_number or ''}</strong> a été envoyé automatiquement.",
            "Le rendez-vous sera confirmé dans votre agenda dès signature du client.",
        ],
        cta_label="Voir mes devis",
        cta_url=f"{base}/quotes",
    )
    text = f"Devis {quote_number} envoyé à {customer_name} pour le {when_label} — en attente de signature."
    return _send(to_addr, "Devis en attente de signature client", html, text, tenant_id=tenant_id)
