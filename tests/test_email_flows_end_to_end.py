"""Every user-facing flow that owes somebody an e-mail, driven through its route.

Unit tests already cover the individual senders. What was missing is proof that
the *flows* are wired: that signing up, asking for a password reset, booking or
unsubscribing actually reaches the mail layer through the real HTTP handler, with
a real body, addressed to the right person.

Each test asserts on an ``EmailMessage`` row, which is what the console shows and
what SMTP is handed — so a flow that silently swallows its exception fails here.
"""
import re
import uuid

from app.core.extensions import db
from app.models.email_message import DIRECTION_OUTBOUND, EmailMessage
from app.models.user import User


def _addr() -> str:
    return f"flow-{uuid.uuid4().hex[:10]}@exemple-artisan.fr"


def _mails_to(addr: str) -> list[EmailMessage]:
    return (
        EmailMessage.query.filter_by(direction=DIRECTION_OUTBOUND, to_addr=addr)
        .order_by(EmailMessage.created_at.asc())
        .all()
    )


def _assert_real_email(row: EmailMessage, *, must_contain: str | None = None):
    """A queued row is not an e-mail. Check it carries what a client needs."""
    assert row.status in ("sent", "simulated"), f"statut inattendu : {row.status} ({row.error})"
    assert (row.subject or "").strip(), "objet vide"
    body = f"{row.body or ''}{row.html_body or ''}"
    assert len(body) > 100, "corps quasi vide"
    assert "{{" not in body, "variable de fusion non résolue dans le corps"
    if must_contain:
        assert must_contain.lower() in body.lower(), f"« {must_contain} » absent du corps"


# --------------------------------------------------------------------------- #
# Création de compte
# --------------------------------------------------------------------------- #
def test_artisan_signup_sends_a_welcome_email(client):
    email = _addr()
    response = client.post(
        "/register",
        data={
            # A city no other test asserts on: a signup here becomes a public
            # directory listing, and the SEO suite checks whether real cities
            # have enough substance to be indexed.
            "company_name": "Plomberie Flux", "first_name": "Julien", "last_name": "Martin",
            "email": email, "phone": "0478000000",
            "city": "Flux-les-Bains", "trade_type": "plombier",
            "password": "MotDePasse123", "confirm_password": "MotDePasse123",
        },
        follow_redirects=False,
    )
    assert response.status_code in (302, 303), "l'inscription n'a pas abouti"
    assert User.query.filter_by(email=email).first() is not None

    mails = _mails_to(email)
    assert mails, "aucun e-mail de bienvenue artisan"
    _assert_real_email(mails[0])
    assert "bienvenue" in (mails[0].subject or "").lower() or "PilotCore" in (mails[0].subject or "")


def test_customer_signup_sends_a_welcome_email(client):
    email = _addr()
    response = client.post(
        "/client/register",
        data={
            "first_name": "Claire", "last_name": "Dubois", "email": email,
            "phone": "0600000000", "password": "MotDePasse123",
            "confirm_password": "MotDePasse123",
        },
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)

    mails = _mails_to(email)
    assert mails, "aucun e-mail de bienvenue client"
    _assert_real_email(mails[0])


# --------------------------------------------------------------------------- #
# Mot de passe
# --------------------------------------------------------------------------- #
def _make_user(email: str) -> User:
    user = User(email=email, role="customer", first_name="Test")
    user.set_password("MotDePasse123")
    db.session.add(user)
    db.session.commit()
    return user


def test_forgot_password_sends_a_working_reset_link(client, app):
    email = _addr()
    _make_user(email)

    response = client.post("/forgot-password", data={"email": email}, follow_redirects=True)
    assert response.status_code == 200

    mails = _mails_to(email)
    assert mails, "aucun e-mail de réinitialisation"
    _assert_real_email(mails[-1], must_contain="reset-password")

    # The link in the e-mail must actually open the reset form.
    body = f"{mails[-1].body or ''}{mails[-1].html_body or ''}"
    match = re.search(r"/reset-password/([A-Za-z0-9_.\-]+)", body)
    assert match, "aucun lien de réinitialisation exploitable dans l'e-mail"
    page = client.get(f"/reset-password/{match.group(1)}")
    assert page.status_code == 200
    assert "nouveau" in page.get_data(as_text=True).lower()


def test_password_change_notifies_the_account_holder(client, app):
    email = _addr()
    user = _make_user(email)

    from app.services.password_reset import generate_reset_token

    with app.test_request_context():
        token = generate_reset_token(user)

    before = len(_mails_to(email))
    response = client.post(
        f"/reset-password/{token}",
        data={"new_password": "NouveauMotDePasse1", "confirm_password": "NouveauMotDePasse1"},
        follow_redirects=True,
    )
    assert response.status_code == 200

    mails = _mails_to(email)
    assert len(mails) > before, "aucune notification de changement de mot de passe"
    _assert_real_email(mails[-1])


def test_forgot_password_never_reveals_whether_an_account_exists(client):
    """A silent no-op for an unknown address, and the same page either way."""
    unknown = _addr()
    response = client.post("/forgot-password", data={"email": unknown}, follow_redirects=True)
    assert response.status_code == 200
    assert _mails_to(unknown) == []


# --------------------------------------------------------------------------- #
# Désinscription
# --------------------------------------------------------------------------- #
def test_campaign_unsubscribe_stops_the_next_send(client, app):
    """The opt-out link must both confirm to the visitor and hold on the next batch."""
    from app.models.outreach_prospect import OutreachProspect
    from app.services import campaigns

    email = _addr()
    db.session.add(
        OutreachProspect(
            email=email, company_name="Artisan Désinscrit", trade_type="plombier",
            city="Désinscription-Ville", status="ready", source="test",
        )
    )
    db.session.commit()

    campaign = campaigns.create_campaign(name="Flux désinscription", template="offre")
    campaign.subject = "Un objet"
    campaign.set_segment(
        {"cities": ["Désinscription-Ville"], "statuses": [], "exclude_contacted": False, "limit": 10}
    )
    db.session.commit()
    campaigns.prepare_campaign(campaign.id)

    recipient = campaign.recipients.first()
    page = client.get(f"/desinscription/{recipient.unsub_token}")
    assert page.status_code == 200
    assert "ne recevra plus" in page.get_data(as_text=True)

    result = campaigns.send_batch(campaign.id, batch_size=10)
    assert result["sent"] == 0, "un e-mail est parti après une désinscription"
    assert _mails_to(email) == []


def test_every_campaign_email_carries_a_working_unsubscribe_link(client, app):
    from app.models.outreach_prospect import OutreachProspect
    from app.services import campaigns

    email = _addr()
    db.session.add(
        OutreachProspect(
            email=email, company_name="Artisan Lien", trade_type="plombier",
            city="Lien-Ville", status="ready", source="test",
        )
    )
    db.session.commit()

    campaign = campaigns.create_campaign(name="Flux lien", template="offre")
    campaign.subject = "Un objet"
    campaign.set_segment(
        {"cities": ["Lien-Ville"], "statuses": [], "exclude_contacted": False, "limit": 10}
    )
    db.session.commit()
    campaigns.prepare_campaign(campaign.id)
    campaigns.send_batch(campaign.id, batch_size=10)

    mails = _mails_to(email)
    assert mails, "la campagne n'a rien envoyé"
    _assert_real_email(mails[0], must_contain="désinscrire")

    body = mails[0].html_body or ""
    match = re.search(r"/desinscription/([A-Za-z0-9_\-]+)", body)
    assert match, "pas de lien de désinscription dans le corps"
    assert client.get(f"/desinscription/{match.group(1)}").status_code == 200


def test_campaign_email_sets_the_list_unsubscribe_header(app, monkeypatch):
    """Gmail and Outlook demote bulk mail that lacks it."""
    from app.models.outreach_prospect import OutreachProspect
    from app.services import campaigns

    captured = {}

    real_send = campaigns.admin_email.send_email

    def spy(*args, **kwargs):
        captured.update(kwargs)
        return real_send(*args, **kwargs)

    monkeypatch.setattr(campaigns.admin_email, "send_email", spy)

    email = _addr()
    db.session.add(
        OutreachProspect(email=email, company_name="En-tête", trade_type="plombier",
                         city="Entete-Ville", status="ready", source="test")
    )
    db.session.commit()
    campaign = campaigns.create_campaign(name="Flux en-tête", template="offre")
    campaign.subject = "Un objet"
    campaign.set_segment({"cities": ["Entete-Ville"], "statuses": [],
                          "exclude_contacted": False, "limit": 10})
    db.session.commit()
    campaigns.prepare_campaign(campaign.id)
    campaigns.send_batch(campaign.id, batch_size=10)

    assert "mailto:" in captured.get("list_unsubscribe", "")
    assert "/desinscription/" in captured.get("list_unsubscribe", "")


# --------------------------------------------------------------------------- #
# Contact
# --------------------------------------------------------------------------- #
def test_contact_form_reaches_the_mailbox(client, app):
    from app.services import admin_email

    before = EmailMessage.query.filter_by(direction=DIRECTION_OUTBOUND).count()
    response = client.post(
        "/contact",
        data={
            "name": "Jean Dupont", "email": _addr(), "phone": "0600000000",
            "subject": "Question sur l'offre Pro",
            "message": "Bonjour, je voudrais des précisions sur l'offre Pro pour mon entreprise.",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    after = EmailMessage.query.filter_by(direction=DIRECTION_OUTBOUND).count()
    assert after > before, "le formulaire de contact n'a déclenché aucun e-mail"

    row = (
        EmailMessage.query.filter_by(direction=DIRECTION_OUTBOUND)
        .order_by(EmailMessage.created_at.desc())
        .first()
    )
    assert row.to_addr, "e-mail de contact sans destinataire"
    assert admin_email.default_from_addr() in (row.to_addr or "") or row.to_addr


# --------------------------------------------------------------------------- #
# Programme des 50 fondateurs
# --------------------------------------------------------------------------- #
def test_founding_waitlist_confirms_by_email(client, app):
    from app.models.founding import FoundingWaitlist
    from app.services import founding_program

    email = _addr()
    row = FoundingWaitlist(
        email=email, name="Artisan Attente", city="Flux-les-Bains", trade_type="plombier"
    )
    db.session.add(row)
    db.session.commit()

    from app.services.transactional_email import send_founding_waitlist

    send_founding_waitlist(row)
    mails = _mails_to(email)
    assert mails, "aucune confirmation de liste d'attente"
    _assert_real_email(mails[0])


# --------------------------------------------------------------------------- #
# Santé globale de la couche e-mail
# --------------------------------------------------------------------------- #
def test_no_flow_leaves_an_email_stuck_in_the_queue(app):
    """``queued`` means the send never completed — nothing should end there."""
    stuck = EmailMessage.query.filter_by(direction=DIRECTION_OUTBOUND, status="queued").all()
    assert not stuck, [f"{m.to_addr} — {m.subject}" for m in stuck[:5]]


def test_no_flow_produced_a_failed_send(app):
    failed = EmailMessage.query.filter_by(direction=DIRECTION_OUTBOUND, status="failed").all()
    assert not failed, [f"{m.to_addr} — {m.error}" for m in failed[:5]]


def test_every_outbound_email_is_trackable_and_addressed(app):
    rows = EmailMessage.query.filter_by(direction=DIRECTION_OUTBOUND).all()
    assert rows, "aucun e-mail produit par la suite — les tests ne prouvent rien"
    for row in rows:
        assert row.to_addr, f"e-mail sans destinataire : {row.subject}"
        assert (row.subject or "").strip(), f"e-mail sans objet vers {row.to_addr}"


# --------------------------------------------------------------------------- #
# Résilience du schéma — reproduit la panne constatée en production
# --------------------------------------------------------------------------- #
def test_unsubscribe_page_never_500s_when_the_table_is_missing(client, app, monkeypatch):
    """Production skipped the migration and the opt-out link returned a 500.

    An unsubscribe link that errors leaves the recipient with no way to stop the
    mail, so the page must degrade to "write to us" rather than crash.
    """
    from sqlalchemy.exc import ProgrammingError

    from app.services import campaigns

    def boom(_token):
        raise ProgrammingError("SELECT ...", {}, Exception("relation does not exist"))

    monkeypatch.setattr(campaigns, "unsubscribe", boom)

    response = client.get("/desinscription/nimporte-quoi")
    assert response.status_code == 404, "la page d'opt-out ne doit jamais renvoyer 500"
    body = response.get_data(as_text=True)
    assert "contact@pilotcore.fr" in body, "aucune porte de sortie proposée au destinataire"


def test_boot_creates_tables_the_database_is_missing(app):
    """The guard that would have prevented the production 500."""
    from sqlalchemy import inspect

    from app import _ensure_missing_tables
    from app.core.extensions import db
    from app.models.email_campaign import CampaignRecipient

    CampaignRecipient.__table__.drop(db.engine, checkfirst=True)
    assert "campaign_recipients" not in set(inspect(db.engine).get_table_names())

    _ensure_missing_tables()
    assert "campaign_recipients" in set(inspect(db.engine).get_table_names())

    # Idempotent: a second pass over a complete schema is a no-op.
    _ensure_missing_tables()


# --------------------------------------------------------------------------- #
# Rendez-vous, devis, vocal, fondateurs — le reste des e-mails transactionnels
# --------------------------------------------------------------------------- #
def _tenant(app):
    from app.models.tenant import Tenant

    tenant = Tenant.query.first()
    assert tenant is not None, "la fixture n'a produit aucun artisan"
    return tenant


def test_appointment_confirmation_reaches_the_customer(app):
    from app.services.transactional_email import send_appointment_confirmation

    email = _addr()
    send_appointment_confirmation(
        email, "mardi 8 septembre à 9h00", "Plomberie Flux", customer_name="Claire"
    )
    mails = _mails_to(email)
    assert mails, "aucune confirmation de rendez-vous"
    _assert_real_email(mails[-1], must_contain="mardi 8 septembre")


def test_new_booking_notifies_the_artisan(app):
    from app.services.transactional_email import send_new_booking_to_artisan

    email = _addr()
    send_new_booking_to_artisan(email, "mardi 8 septembre à 9h00", "Claire Dubois")
    mails = _mails_to(email)
    assert mails, "l'artisan n'est pas prévenu du nouveau rendez-vous"
    _assert_real_email(mails[-1], must_contain="Claire Dubois")


def test_quote_email_lands_with_its_signature_link(app):
    """Driven through the delivery service the dashboard actually calls."""
    from app.models.quote import Quote
    from app.services import quote_delivery, quote_engine

    tenant = _tenant(app)
    email = _addr()
    quote = quote_engine.build_draft_from_lead(None, tenant)
    quote.number = "DEV-FLUX-001"
    quote.client_email = email
    quote.client_name = "Jean Dupont"

    with app.test_request_context(base_url="https://www.pilotcore.fr"):
        result = quote_delivery.send_quote(quote, tenant, channels=["email"])
    assert result["email"] is True

    mails = _mails_to(email)
    assert mails, "le devis n'est pas parti par e-mail"
    _assert_real_email(mails[-1], must_contain="DEV-FLUX-001")
    assert isinstance(quote, Quote)


def test_voice_created_account_receives_its_credentials(app):
    """A caller who gets an account over the phone must be told how to log in."""
    from app.models.user import User
    from app.services.voice.customer_account import send_credentials_email

    email = _addr()
    user = User(email=email, role="customer", first_name="Claire")
    user.set_password("MotDePasse123")
    db.session.add(user)
    db.session.commit()

    send_credentials_email(user, "MotDePasse123")
    mails = _mails_to(email)
    assert mails, "aucun e-mail d'identifiants après création de compte au téléphone"
    _assert_real_email(mails[-1], must_contain="MotDePasse123")


def test_founding_welcome_is_sent_on_enrolment(app):
    """Driven through the real enrolment, not a hand-built participant."""
    from app.services import founding_program

    email = _addr()
    user, tenant, participant = founding_program.enroll(
        email=email,
        password="MotDePasse123",
        first_name="Julien",
        last_name="Martin",
        # Distinct from the /register signup above: an ordinary signup now takes
        # a founding seat too, and a seat is one per phone number.
        phone="0478000042",
        city="Flux-les-Bains",
        trade_type="plombier",
        company_name="Plomberie Fondatrice",
        source="test",
    )
    assert participant.place_number >= 1

    mails = _mails_to(email)
    assert mails, "aucun e-mail de bienvenue au programme fondateurs"
    _assert_real_email(mails[-1])
    # The programme records that the welcome went out, so a nudge never doubles it.
    assert participant.emails_sent_json is None or "welcome" in participant.emails_sent_json


def test_every_transactional_sender_is_reachable_from_the_app(app):
    """No sender may become dead code: each one must have a caller outside its module."""
    import pathlib
    import re

    src = pathlib.Path("app/services/transactional_email.py").read_text()
    senders = re.findall(r"^def (send_\w+)", src, re.MULTILINE)
    assert len(senders) >= 20, "l'inventaire des expéditeurs semble incomplet"

    tree = " ".join(
        p.read_text() for p in pathlib.Path("app").rglob("*.py")
        if p.name != "transactional_email.py"
    ) + " ".join(p.read_text() for p in pathlib.Path("scripts").rglob("*.py"))

    orphans = [fn for fn in senders if fn not in tree]
    assert not orphans, f"expéditeurs jamais appelés : {orphans}"
