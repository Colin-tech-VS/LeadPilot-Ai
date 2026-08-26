"""Envoi SMTP : réutilisation de connexion et gestion du « 421 too many connections ».

Le bug d'origine : chaque destinataire d'une campagne ouvrait sa propre
connexion SMTP, et LWS répondait
``421 4.7.0 mail96.lwspanel.com Error: too many connections from <ip>``
au bout de quelques messages. Ces tests verrouillent les trois garanties du
correctif — une connexion par lot, un 4xx ne condamne personne, et un refus
temporaire est réessayé.
"""
import smtplib
import uuid
from unittest.mock import patch

import pytest

from app.core.extensions import db
from app.models.email_campaign import R_PENDING, R_SENT, CampaignRecipient
from app.models.email_message import DIRECTION_OUTBOUND, EmailMessage
from app.models.outreach_prospect import OutreachProspect
from app.services import admin_email, campaigns


class FakeServer:
    """Un serveur SMTP qui compte ses connexions et ses messages."""

    def __init__(self, log, fail_at=None, fail_exc=None):
        self.log = log
        self.fail_at = fail_at
        self.fail_exc = fail_exc
        self.closed = False

    def sendmail(self, envelope_from, recipients, message):
        self.log["messages"] += 1
        if self.fail_at is not None and self.log["messages"] == self.fail_at:
            raise self.fail_exc
        self.log["delivered"].append(recipients[0])

    def quit(self):
        self.closed = True

    def close(self):
        self.closed = True


@pytest.fixture
def smtp(app):
    """SMTP « configuré », sans réseau ni temporisation."""
    app.config["SMTP_HOST"] = "mail.test"
    app.config["SMTP_USER"] = "contact@pilotcore.fr"
    app.config["SMTP_PASSWORD"] = "secret"
    app.config["SMTP_SEND_INTERVAL"] = 0
    app.config["SMTP_RETRY_BACKOFF"] = 0
    yield
    app.config["SMTP_HOST"] = ""


def _connect_factory(log, **kwargs):
    def _connect():
        log["connections"] += 1
        return FakeServer(log, **kwargs)
    return _connect


def _fresh_log():
    return {"connections": 0, "messages": 0, "delivered": []}


def _campaign_with(count):
    city = f"Ville-{uuid.uuid4().hex[:8]}"
    for i in range(count):
        db.session.add(
            OutreachProspect(
                company_name=f"Artisan {i}",
                email=f"a{i}-{uuid.uuid4().hex[:6]}@exemple-artisan.fr",
                trade_type="plombier",
                city=city,
                status="ready",
                source="rge_ademe",
            )
        )
    db.session.commit()
    campaign = campaigns.create_campaign(name="Débit", template="offre")
    campaign.subject = "Objet"
    campaign.set_segment({"cities": [city], "statuses": [], "exclude_contacted": False, "limit": 50})
    db.session.commit()
    campaigns.prepare_campaign(campaign.id)
    return campaign


def test_a_batch_uses_one_connection_for_every_recipient(app, smtp):
    """La cause du 421 : autant de connexions que de messages."""
    log = _fresh_log()
    campaign = _campaign_with(6)

    with patch.object(admin_email, "_connect", _connect_factory(log)):
        report = campaigns.send_batch(campaign.id, batch_size=6)

    assert report["sent"] == 6
    assert log["messages"] == 6
    assert log["connections"] == 1


def test_a_connection_is_recycled_after_the_configured_number_of_messages(app, smtp):
    """Certains hôtes coupent au-delà de N messages : on rouvre avant eux."""
    app.config["SMTP_MAX_PER_CONNECTION"] = 2
    log = _fresh_log()
    campaign = _campaign_with(6)

    with patch.object(admin_email, "_connect", _connect_factory(log)):
        campaigns.send_batch(campaign.id, batch_size=6)

    assert log["messages"] == 6
    assert log["connections"] == 3
    app.config["SMTP_MAX_PER_CONNECTION"] = 25


def test_too_many_connections_leaves_the_rest_pending_instead_of_failed(app, smtp):
    """Un 421 est une file d'attente, pas une adresse morte : personne n'est perdu."""
    app.config["SMTP_MAX_RETRIES"] = 0
    log = _fresh_log()
    refusal = smtplib.SMTPServerDisconnected(
        "421 4.7.0 mail96.lwspanel.com Error: too many connections from 171.33.105.206"
    )
    campaign = _campaign_with(5)

    connect = _connect_factory(log, fail_at=3, fail_exc=refusal)
    with patch.object(admin_email, "_connect", connect):
        report = campaigns.send_batch(campaign.id, batch_size=5)

    assert report["throttled"] is True
    assert report["done"] is False
    assert report["retry_after"] > 0
    assert report["sent"] == 2
    assert report["failed"] == 0

    statuses = [r.status for r in campaign.recipients.all()]
    assert statuses.count(R_SENT) == 2
    assert statuses.count(R_PENDING) == 3

    # Aucun message fantôme « échoué » dans la boîte d'envoi pour le message
    # refusé : il n'est jamais parti, il repartira au prochain lot.
    ghosts = EmailMessage.query.filter_by(direction=DIRECTION_OUTBOUND, status="failed").filter(
        EmailMessage.to_addr.in_([r.email for r in campaign.recipients.all()])
    ).count()
    assert ghosts == 0
    app.config["SMTP_MAX_RETRIES"] = 2


def test_a_refused_connection_costs_nothing(app, smtp):
    """Le symptôme de production : le 421 tombe à l'ouverture de la connexion.

    Aucun destinataire touché, aucune ligne écrite dans la boîte d'envoi — le
    lot répond « revenez plus tard » et rien n'est perdu.
    """
    campaign = _campaign_with(3)
    before = EmailMessage.query.filter_by(direction=DIRECTION_OUTBOUND).count()

    def _refuse():
        raise smtplib.SMTPConnectError(
            421, b"4.7.0 mail96.lwspanel.com Error: too many connections from 171.33.105.206"
        )

    with patch.object(admin_email, "_connect", _refuse):
        report = campaigns.send_batch(campaign.id, batch_size=3)

    assert report["throttled"] is True
    assert report["sent"] == 0 and report["failed"] == 0
    assert report["remaining"] == 3
    assert all(r.status == R_PENDING for r in campaign.recipients.all())
    assert EmailMessage.query.filter_by(direction=DIRECTION_OUTBOUND).count() == before


def test_a_throttled_campaign_resumes_where_it_stopped(app, smtp):
    app.config["SMTP_MAX_RETRIES"] = 0
    campaign = _campaign_with(4)
    refusal = smtplib.SMTPServerDisconnected("421 too many connections")

    log = _fresh_log()
    with patch.object(admin_email, "_connect", _connect_factory(log, fail_at=2, fail_exc=refusal)):
        first = campaigns.send_batch(campaign.id, batch_size=4)
    assert first["sent"] == 1 and first["throttled"] is True

    log = _fresh_log()
    with patch.object(admin_email, "_connect", _connect_factory(log)):
        second = campaigns.send_batch(campaign.id, batch_size=4)

    assert second["sent"] == 3
    assert second["done"] is True
    assert second["throttled"] is False
    assert CampaignRecipient.query.filter_by(
        campaign_id=campaign.id, status=R_PENDING
    ).count() == 0
    app.config["SMTP_MAX_RETRIES"] = 2


def test_a_temporary_refusal_is_retried_before_giving_up(app, smtp):
    """Le premier refus rouvre une connexion ; le message part quand même."""
    app.config["SMTP_MAX_RETRIES"] = 2
    log = _fresh_log()
    campaign = _campaign_with(2)
    refusal = smtplib.SMTPDataError(451, b"4.3.2 try again later")

    with patch.object(admin_email, "_connect", _connect_factory(log, fail_at=1, fail_exc=refusal)):
        report = campaigns.send_batch(campaign.id, batch_size=2)

    assert report["sent"] == 2
    assert report["throttled"] is False
    assert log["connections"] == 2  # la connexion suspecte est jetée, pas réutilisée


def test_a_permanent_refusal_is_not_retried_and_marks_the_recipient(app, smtp):
    """Un 5xx est un refus : le réessayer ne ferait qu'insister auprès de l'hôte."""
    log = _fresh_log()
    campaign = _campaign_with(2)
    refusal = smtplib.SMTPRecipientsRefused({"a@exemple-artisan.fr": (550, b"5.1.1 unknown")})

    with patch.object(admin_email, "_connect", _connect_factory(log, fail_at=1, fail_exc=refusal)):
        report = campaigns.send_batch(campaign.id, batch_size=2)

    assert report["failed"] == 1
    assert report["sent"] == 1
    assert report["throttled"] is False
    # La connexion reste ouverte : une adresse morte ne doit pas provoquer une
    # reconnexion — c'est ce cycle-là qui saturait l'hôte.
    assert log["connections"] == 1


def test_transient_and_permanent_are_told_apart(app):
    assert admin_email.transient_reason(smtplib.SMTPDataError(421, b"too many connections"))
    assert admin_email.transient_reason(smtplib.SMTPServerDisconnected("connection lost"))
    assert admin_email.transient_reason(TimeoutError("timed out"))
    assert admin_email.transient_reason(
        smtplib.SMTPRecipientsRefused({"x@y.fr": (450, b"4.2.1 mailbox busy")})
    )
    assert admin_email.transient_reason(smtplib.SMTPDataError(550, b"rejected")) is None
    assert admin_email.transient_reason(
        smtplib.SMTPRecipientsRefused({"x@y.fr": (550, b"5.1.1 unknown")})
    ) is None
    assert admin_email.transient_reason(ValueError("nothing to do with SMTP")) is None


def test_a_single_transactional_send_still_records_its_own_failure(app, smtp):
    """Hors lot, le comportement historique tient : la ligne est tracée en échec."""
    app.config["SMTP_MAX_RETRIES"] = 0
    log = _fresh_log()
    refusal = smtplib.SMTPServerDisconnected("421 too many connections")

    with patch.object(admin_email, "_connect", _connect_factory(log, fail_at=1, fail_exc=refusal)):
        row = admin_email.send_email("client@exemple.fr", "Objet", "Corps")

    assert row.status == "failed"
    assert "421" in (row.error or "")
    app.config["SMTP_MAX_RETRIES"] = 2


def test_the_cron_skips_a_campaign_the_console_is_already_sending(app, smtp):
    """Deux expéditeurs simultanés = deux connexions : exactement ce qu'on évite."""
    log = _fresh_log()
    campaign = _campaign_with(4)
    mine = {r.email for r in campaign.recipients.all()}

    with patch.object(admin_email, "_connect", _connect_factory(log)):
        campaigns.send_batch(campaign.id, batch_size=1)   # la console vient d'envoyer
        before = [a for a in log["delivered"] if a in mine]
        campaigns.run_due_campaigns(batch_size=4)         # le cron passe dans la foulée

    # Compté sur cette campagne seule : la suite partage une base, d'autres
    # campagnes en vol y traînent et le cron a le droit de les faire avancer.
    after = [a for a in log["delivered"] if a in mine]
    assert after == before
    assert CampaignRecipient.query.filter_by(
        campaign_id=campaign.id, status=R_PENDING
    ).count() == 3
