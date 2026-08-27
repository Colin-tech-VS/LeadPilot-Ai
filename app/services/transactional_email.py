"""Transactional emails with a single, shared PilotCore design.

Every automatic email (welcome, password change, booking confirmation…) is
rendered through :func:`render_email` so they all look identical, then sent via
:func:`app.services.admin_email.send_email` — which also records the message in
``EmailMessage`` so the admin console (/admin/emails) shows every mail we send.

Sending never raises: a transactional email must never break the user action
that triggered it (signup, booking…). Failures are logged and swallowed.
"""

import html as html_lib
import logging
import re

from flask import current_app

logger = logging.getLogger(__name__)

BRAND = "PilotCore"
INK = "#1C1914"
INK_MUTED = "#6B6458"
INK_DEEP = "#121820"
BRAND_COLOR = "#1A2332"
PAPER = "#EFE9DC"
SURFACE = "#FBF7EE"
CREAM = "#F6F1E6"
BORDER = "#D7CDB8"
BORDER_STRONG = "#C4B79A"
SUCCESS = "#3D9A6A"
FONT_DISPLAY = "Georgia,'Times New Roman',Times,serif"
FONT_BODY = "'Segoe UI',Tahoma,Geneva,Verdana,sans-serif"


def _base_url() -> str:
    cfg = current_app.config
    return str(cfg.get("PUBLIC_BASE_URL") or "https://www.pilotcore.fr").rstrip("/")


def _looks_like_html(value: str) -> bool:
    return (value or "").lstrip().startswith("<")


def _block(html: str, *, size: str = "16px", color: str = INK, mb: str = "16px") -> str:
    if not html:
        return ""
    if _looks_like_html(html):
        return html
    return (
        f'<p style="margin:0 0 {mb};font-size:{size};line-height:1.65;color:{color};'
        f'font-family:{FONT_BODY};">{html}</p>'
    )


def render_email(
    title: str,
    intro: str,
    *,
    lines: list[str] | None = None,
    cta_label: str | None = None,
    cta_url: str | None = None,
    outro: str | None = None,
    preheader: str | None = None,
    summary_html: str | None = None,
    kicker: str | None = None,
) -> str:
    """Return the full branded HTML for a transactional email.

    Ledger layout (paper / ink), table-based and inline-styled for clients.
    ``lines`` may contain light HTML (``<strong>``, ``<br>``).
    """
    base = _base_url()
    body_blocks = [_block(intro)]
    if summary_html:
        body_blocks.append(summary_html)
    for ln in lines or []:
        body_blocks.append(_block(ln, size="15px", mb="12px"))
    if cta_label and cta_url:
        body_blocks.append(
            f'''<table role="presentation" cellpadding="0" cellspacing="0" style="margin:28px 0 8px;">
              <tr><td style="background:{INK_DEEP};border-radius:4px;">
                <a href="{cta_url}" style="display:inline-block;padding:13px 26px;font-size:15px;
                   font-weight:700;letter-spacing:.02em;color:{CREAM};text-decoration:none;
                   border-radius:4px;font-family:{FONT_BODY};">{html_lib.escape(cta_label)}</a>
              </td></tr>
            </table>'''
        )
    if outro:
        body_blocks.append(_block(outro, size="13px", color=INK_MUTED, mb="0"))
    body_html = "\n".join(b for b in body_blocks if b)
    pre = re.sub(r"<[^>]+>", " ", preheader or intro).strip()
    kicker_html = ""
    if kicker:
        kicker_html = (
            f'<p style="margin:0 0 10px;font-size:11px;font-weight:600;letter-spacing:.16em;'
            f'text-transform:uppercase;color:{INK_MUTED};font-family:{FONT_BODY};">{html_lib.escape(kicker)}</p>'
        )

    return f'''<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light">
<title>{html_lib.escape(title)}</title></head>
<body style="margin:0;padding:0;background:{PAPER};">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{html_lib.escape(pre)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{PAPER};padding:36px 12px;">
  <tr><td align="center">
    <table role="presentation" width="560" cellpadding="0" cellspacing="0"
           style="width:100%;max-width:560px;background:{SURFACE};border:1px solid {BORDER_STRONG};border-radius:4px;">
      <tr><td style="height:4px;background:{INK_DEEP};font-size:0;line-height:0;">&nbsp;</td></tr>
      <tr><td style="padding:22px 32px 8px;">
        <img src="{base}/static/images/logo-512.png" width="36" height="36" alt="PilotCore"
             style="vertical-align:middle;border:0;display:inline-block;">
        <span style="font-size:22px;font-weight:650;color:{INK};letter-spacing:-0.03em;
                     font-family:{FONT_DISPLAY};vertical-align:middle;margin-left:10px;">{BRAND}</span>
      </td></tr>
      <tr><td style="padding:8px 32px 32px;">
        {kicker_html}
        <h1 style="margin:0 0 18px;font-size:26px;line-height:1.2;color:{INK};font-weight:650;
                   letter-spacing:-0.03em;font-family:{FONT_DISPLAY};">{html_lib.escape(title)}</h1>
        {body_html}
      </td></tr>
      <tr><td style="padding:18px 32px 22px;background:{PAPER};border-top:1px solid {BORDER};">
        <p style="margin:0 0 6px;font-size:11px;font-weight:600;letter-spacing:.14em;text-transform:uppercase;
                  color:{INK_MUTED};font-family:{FONT_BODY};">Réceptionniste IA · Prise de RDV</p>
        <p style="margin:0;font-size:12px;line-height:1.6;color:{INK_MUTED};font-family:{FONT_BODY};">
          <a href="{base}" style="color:{BRAND_COLOR};text-decoration:none;">{base.replace("https://", "")}</a>
          &nbsp;·&nbsp;
          <a href="{base}/confidentialite" style="color:{INK_MUTED};text-decoration:none;">Confidentialité</a>
          &nbsp;·&nbsp;
          <a href="{base}/mentions-legales" style="color:{INK_MUTED};text-decoration:none;">Mentions légales</a>
        </p>
      </td></tr>
    </table>
  </td></tr>
</table>
</body></html>'''


def wrap_plain_as_html(subject: str, body: str, *, kicker: str | None = None) -> str:
    """Turn a plain-text outbound body into the shared ledger template."""
    text = (body or "").strip()
    parts = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    intro = html_lib.escape(parts[0]).replace("\n", "<br>") if parts else ""
    rest = [html_lib.escape(p).replace("\n", "<br>") for p in parts[1:]]
    title = (subject or "Message").strip()
    for prefix in ("[Contact] ", "[PilotCore] "):
        if title.startswith(prefix):
            title = title[len(prefix):].strip() or title
            kicker = kicker or prefix.strip("[] ").title()
            break
    return render_email(title, intro, lines=rest, kicker=kicker or "Message")


def _quote_summary_box(*, quote_number: str | None, total_ttc: float, artisan_name: str) -> str:
    number = (quote_number or "—").strip()
    amount = f"{total_ttc:.2f}".replace(".", ",")
    return f'''<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
      style="margin:0 0 20px;background:{PAPER};border:1px solid {BORDER};border-radius:4px;">
  <tr><td style="padding:16px 18px;">
    <p style="margin:0 0 10px;font-size:11px;font-weight:600;letter-spacing:.14em;text-transform:uppercase;
              color:{INK_MUTED};font-family:{FONT_BODY};">Récapitulatif</p>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
      <tr>
        <td style="padding:6px 0;font-size:14px;color:{INK_MUTED};font-family:{FONT_BODY};">Devis</td>
        <td style="padding:6px 0;font-size:14px;font-weight:700;color:{INK};text-align:right;font-family:{FONT_BODY};">{number}</td>
      </tr>
      <tr>
        <td style="padding:6px 0;font-size:14px;color:{INK_MUTED};font-family:{FONT_BODY};">Montant TTC</td>
        <td style="padding:6px 0;font-size:18px;font-weight:700;color:{INK};text-align:right;font-family:{FONT_DISPLAY};">{amount} €</td>
      </tr>
      <tr>
        <td style="padding:6px 0;font-size:14px;color:{INK_MUTED};font-family:{FONT_BODY};">Artisan</td>
        <td style="padding:6px 0;font-size:14px;font-weight:600;color:{INK};text-align:right;font-family:{FONT_BODY};">{artisan_name}</td>
      </tr>
    </table>
  </td></tr>
</table>'''


def _rib_box(rib_lines: list[str] | None) -> str:
    if not rib_lines:
        return ""
    items = "".join(
        f'<p style="margin:0 0 6px;font-size:14px;line-height:1.5;color:{INK};font-family:{FONT_BODY};">{line}</p>'
        for line in rib_lines
    )
    return f'''<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
      style="margin:0 0 16px;background:{PAPER};border:1px solid {BORDER};border-left:4px solid {SUCCESS};border-radius:4px;">
  <tr><td style="padding:14px 16px;">{items}</td></tr>
</table>'''


def _artisan_reply_to(tenant_id) -> str | None:
    """The artisan's own address, for mail their client receives.

    A devis reaches a homeowner who will want to answer it — about a date, a
    price, a question. That reply belongs to the artisan, not to us.
    """
    if not tenant_id:
        return None
    try:
        from app.models.user import User

        owner = (
            User.query.filter(User.tenant_id == tenant_id, User.role == "admin")
            .order_by(User.created_at.asc())
            .first()
        )
        return owner.email if owner and owner.email else None
    except Exception:
        logger.debug("Could not resolve a reply-to for tenant=%s", tenant_id, exc_info=True)
        return None


def _send(to_addr, subject, html, text_body, tenant_id=None, reply_to=None):
    """Deliver a transactional email. Never raises.

    Always carries a Reply-To. The envelope From is pinned to SMTP_USER (LWS
    refuses anything else), and that mailbox is a no-reply, so without this
    header every answer a recipient writes would fall on the floor.
    """
    try:
        from app.services import admin_email

        return admin_email.send_email(
            to_addr=to_addr,
            subject=subject,
            body=text_body,
            is_html=True,
            html_body=html,
            tenant_id=tenant_id,
            reply_to=reply_to or admin_email.default_from_addr(),
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
        "Votre compte PilotCore est prêt",
        hello,
        kicker="Espace artisan",
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
        "Votre compte est créé",
        hello,
        kicker="Espace client",
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
        kicker="Espace client",
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
        kicker="Sécurité",
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
        kicker="Sécurité",
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
        "Rendez-vous confirmé",
        hello,
        kicker="Agenda",
        lines=lines,
        cta_label="Voir mes rendez-vous",
        cta_url=f"{base}/client/account",
    )
    text = f"{hello}\nRDV confirmé avec {artisan_name} le {when_label}."
    return _send(
        to_addr, f"Rendez-vous confirmé — {artisan_name}", html, text,
        tenant_id=tenant_id, reply_to=_artisan_reply_to(tenant_id),
    )


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
        "Nouvelle demande de rendez-vous",
        "Bonjour,",
        kicker="Agenda",
        lines=lines,
        cta_label="Voir dans mon agenda",
        cta_url=f"{base}/appointments",
    )
    text = f"Nouvelle réservation de {customer_name} le {when_label}."
    return _send(to_addr, "Nouvelle demande de rendez-vous", html, text, tenant_id=tenant_id)


def send_devis_to_client(
    to_addr,
    *,
    customer_name=None,
    artisan_name,
    quote_number=None,
    quote_total_ttc,
    sign_url,
    deposit_amount=None,
    deposit_percent=None,
    rib_lines=None,
    tenant_id=None,
):
    """Send a devis link to the client (manual send from artisan dashboard)."""
    if not to_addr or not sign_url:
        return None

    hello = f"Bonjour {customer_name.strip()}," if (customer_name or "").strip() else "Bonjour,"
    number = (quote_number or "").strip()
    title = f"Votre devis {number}" if number else "Votre devis"
    summary = _quote_summary_box(
        quote_number=number or None,
        total_ttc=quote_total_ttc,
        artisan_name=artisan_name,
    )
    lines = [
        "Consultez votre devis en ligne, signez-le et réglez l'acompte si nécessaire.",
    ]
    if deposit_amount:
        pct = f" ({deposit_percent} %)" if deposit_percent else ""
        lines.append(
            f"Un acompte de <strong>{deposit_amount:.2f} €</strong>{pct} "
            "est demandé pour confirmer l'intervention."
        )
    rib_html = _rib_box(rib_lines)
    if rib_html:
        lines.append(rib_html)

    html = render_email(
        title,
        hello,
        kicker="Devis",
        lines=lines,
        summary_html=summary,
        cta_label="Voir et signer le devis",
        cta_url=sign_url,
        outro="Merci de votre confiance.",
        preheader=f"{artisan_name} vous a envoyé un devis de {quote_total_ttc:.2f} € TTC.",
    )
    text_lines = [
        hello,
        "",
        f"{artisan_name} vous adresse un devis de {quote_total_ttc:.2f} € TTC.",
        f"Consultez et signez-le en ligne : {sign_url}",
    ]
    if deposit_amount:
        text_lines.append(f"Acompte demandé : {deposit_amount:.2f} €.")
    for rib in rib_lines or []:
        text_lines.append(rib)
    text = "\n".join(text_lines)
    subject = f"{title} — {artisan_name}".strip(" —")
    return _send(
        to_addr, subject, html, text, tenant_id=tenant_id,
        reply_to=_artisan_reply_to(tenant_id),
    )


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
    summary = _quote_summary_box(
        quote_number=None,
        total_ttc=quote_total_ttc,
        artisan_name=artisan_name,
    )
    html = render_email(
        "Signez votre devis pour confirmer le rendez-vous",
        hello,
        kicker="Devis",
        lines=[
            f"Vous avez demandé un créneau le <strong>{when_label}</strong>.",
            "Un devis pré-rempli vous attend. L'artisan ne se déplace qu'après "
            "validation du devis en ligne.",
        ],
        summary_html=summary,
        cta_label="Signer le devis",
        cta_url=sign_url,
        outro="Le créneau reste réservé temporairement le temps de votre signature.",
        preheader=f"Confirmez votre RDV du {when_label} en signant votre devis.",
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
        reply_to=_artisan_reply_to(tenant_id),
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
        kicker="Devis",
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


def send_founding_welcome(user, tenant, participant):
    if not user or not user.email:
        return None
    base = _base_url()
    place = getattr(participant, "place_number", None)
    name = (getattr(tenant, "first_name", None) or getattr(tenant, "name", None) or "").strip()
    hello = f"Bonjour {name}," if name else "Bonjour,"
    slot = f"Vous occupez la place {place} du programme." if place else "Votre place dans le programme est confirmée."
    html = render_email(
        "Bienvenue parmi les premiers artisans PilotCore",
        hello,
        kicker="Programme des 50",
        lines=[
            "Votre compte artisan est créé. Vous avez l'offre Starter pendant "
            "30 jours, sans carte bancaire.",
            slot,
            "L'offre Starter, c'est la réponse aux appels, la qualification des "
            "demandes, la fiche et le tableau de bord. Pas de prise de rendez-vous "
            "automatique. Pendant ce mois, les appels passent par la ligne PilotCore "
            "partagée — une ligne à votre nom n'est achetée que si vous prenez ensuite "
            "un abonnement payant.",
            "Prochaine étape : ouvrez votre tableau de bord et vérifiez votre fiche "
            "(adresse, métier, téléphone).",
            "Dites-nous si quelque chose bloque — répondez simplement à cet e-mail.",
        ],
        cta_label="Ouvrir mon tableau de bord",
        cta_url=f"{base}/dashboard",
    )
    text = (
        f"{hello}\n{slot}\nStarter offert 30 jours. Tableau de bord : {base}/dashboard\n"
        "Complétez votre fiche, puis dites-nous si un point bloque."
    )
    return _send(
        user.email,
        "Bienvenue — programme des 50 premiers artisans PilotCore",
        html,
        text,
        tenant_id=getattr(tenant, "id", None),
    )


def send_founding_waitlist(row):
    if not row or not row.email:
        return None
    base = _base_url()
    hello = f"Bonjour {row.name}," if row.name else "Bonjour,"
    html = render_email(
        "Vous êtes sur la liste d'attente",
        hello,
        kicker="Programme des 50",
        lines=[
            "Le programme des 50 premiers artisans est complet. Nous avons bien "
            "enregistré votre demande.",
            "Nous vous écrirons si une prochaine ouverture a lieu. L'essai 14 jours "
            "sans carte reste disponible à tout moment via la création de compte classique.",
        ],
        cta_label="Créer un compte essai",
        cta_url=f"{base}/register",
    )
    text = f"{hello}\nListe d'attente enregistrée. Essai classique : {base}/register"
    return _send(row.email, "PilotCore — liste d'attente des 50 artisans", html, text)


def _founding_nudge(user, tenant, title, lines, subject):
    if not user or not user.email:
        return None
    base = _base_url()
    name = (getattr(tenant, "first_name", None) or getattr(tenant, "name", None) or "").strip()
    hello = f"Bonjour {name}," if name else "Bonjour,"
    html = render_email(
        title,
        hello,
        kicker="Programme des 50",
        lines=lines,
        cta_label="Ouvrir mon espace",
        cta_url=f"{base}/dashboard",
    )
    text = f"{hello}\n{title}\n{base}/dashboard"
    return _send(user.email, subject, html, text, tenant_id=getattr(tenant, "id", None))


def send_founding_nudge_inactive(user, tenant, participant):
    return _founding_nudge(
        user,
        tenant,
        "Votre compte est créé — une étape manque encore",
        [
            "Vous êtes inscrit au programme des 50 premiers artisans, mais votre "
            "espace n'a pas encore servi.",
            "Ouvrez votre fiche, vérifiez téléphone et adresse, puis revenez au "
            "tableau de bord. Rien n'est obligatoire : c'est simplement la suite utile.",
        ],
        "PilotCore — prochaine étape de votre mois Starter",
    )


def send_founding_nudge_no_usage(user, tenant, participant):
    return _founding_nudge(
        user,
        tenant,
        "Votre espace est prêt, il n'a pas encore reçu de demande",
        [
            "Votre fiche est en ligne. Quand un particulier vous contacte via "
            "PilotCore, la demande apparaît dans votre tableau de bord.",
            "Ce message ne dit pas que vous avez déjà reçu un client — seulement "
            "que l'espace est prêt à en enregistrer une.",
        ],
        "PilotCore — votre espace n'a pas encore été utilisé",
    )


def send_founding_ask_feedback(user, tenant, participant):
    return _founding_nudge(
        user,
        tenant,
        "Une demande est arrivée — votre avis nous aide",
        [
            "Une demande a bien été enregistrée dans votre espace. Si quelque chose "
            "a coincé, répondez à cet e-mail ou laissez un avis depuis le tableau de bord.",
            "Nous ne publierons rien sans votre accord explicite.",
        ],
        "PilotCore — votre avis sur une vraie demande",
    )


def send_founding_ask_testimonial(user, tenant, participant):
    return _founding_nudge(
        user,
        tenant,
        "Vous utilisez PilotCore — un retour, si vous le souhaitez",
        [
            "Vous avez déjà plusieurs demandes dans votre espace. Si le service vous "
            "aide réellement, vous pouvez laisser un avis dans le tableau de bord "
            "et cocher l'autorisation de le citer.",
            "Sans cette case, rien n'est publié.",
        ],
        "PilotCore — partagez votre expérience (facultatif)",
    )


def send_founding_expiry_7(user, tenant, participant):
    return _founding_nudge(
        user,
        tenant,
        "Votre période de test arrive bientôt à son terme",
        [
            "Il reste environ 7 jours sur votre mois Starter offert. Ensuite, "
            "vous pourrez continuer avec une offre payante, ou vous arrêter — "
            "sans engagement caché.",
        ],
        "PilotCore — J-7 avant la fin de votre Starter offert",
    )


def send_founding_expiry_3(user, tenant, participant):
    return _founding_nudge(
        user,
        tenant,
        "Votre programme PilotCore se termine bientôt",
        [
            "Il reste environ 3 jours. Vous pourrez choisir une offre depuis "
            "votre espace, ou ne pas continuer.",
        ],
        "PilotCore — J-3 avant la fin de votre Starter offert",
    )


def send_founding_expiry_1(user, tenant, participant):
    return _founding_nudge(
        user,
        tenant,
        "Votre accès gratuit se termine demain",
        [
            "Demain, le mois Starter offert se termine. Si vous voulez garder "
            "la ligne et l'espace, choisissez une offre. Sinon, vous n'avez "
            "rien à faire.",
        ],
        "PilotCore — votre Starter offert se termine demain",
    )


def send_founding_expiry_0(user, tenant, participant):
    if not user or not user.email:
        return None
    base = _base_url()
    name = (getattr(tenant, "first_name", None) or getattr(tenant, "name", None) or "").strip()
    hello = f"Bonjour {name}," if name else "Bonjour,"
    html = render_email(
        "Votre programme est arrivé à son terme",
        hello,
        kicker="Programme des 50",
        lines=[
            "Le mois Starter offert est terminé. Vous pouvez continuer avec une offre "
            "PilotCore, ou vous arrêter.",
            "Aucun prélèvement n'a lieu tant que vous n'avez pas choisi d'offre.",
        ],
        cta_label="Voir les offres",
        cta_url=f"{base}/programme/continuer",
    )
    text = f"{hello}\nFin de période. Offres : {base}/programme/continuer"
    return _send(
        user.email,
        "PilotCore — fin de votre mois Starter offert",
        html,
        text,
        tenant_id=getattr(tenant, "id", None),
    )


def send_founding_admin_reminder(user, tenant, participant):
    return _founding_nudge(
        user,
        tenant,
        "Rappel depuis PilotCore",
        [
            "L'équipe PilotCore vous invite à ouvrir votre tableau de bord pour "
            "poursuivre la configuration, si ce n'est pas déjà fait.",
        ],
        "PilotCore — rappel de configuration",
    )
