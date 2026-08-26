"""Bounce handling — the guard that keeps the sending domain deliverable.

Twenty-nine delivery reports had accumulated unread in production while the
addresses that produced them stayed mailable. These tests pin the behaviour that
fixes it, using the exact report bodies the mail server actually sent.
"""
import uuid

from app.core.extensions import db
from app.models.email_message import DIRECTION_INBOUND, DIRECTION_OUTBOUND, EmailMessage
from app.models.outreach_prospect import OutreachProspect
from app.services import bounce_processing, campaigns


def _login_admin(client):
    with client.session_transaction() as sess:
        sess["admin_authenticated"] = True
        sess["admin_username"] = "admin"


def _prospect(email, **kw):
    row = OutreachProspect(
        email=email, company_name="Artisan Test", trade_type="plombier",
        city=kw.pop("city", "Rebond-Ville"), status=kw.pop("status", "ready"),
        source="web_search", **kw,
    )
    db.session.add(row)
    db.session.commit()
    return row


def _report(body, *, subject="Undelivered Mail Returned to Sender",
            sender="MAILER-DAEMON@mail96.lwspanel.com"):
    row = EmailMessage(
        direction=DIRECTION_INBOUND, status="received",
        from_addr=sender, to_addr="contact@pilotcore.fr",
        subject=subject, body=body,
    )
    db.session.add(row)
    db.session.commit()
    return row


# The shape production actually received.
POSTFIX_RELAY_DENIED = """This is the mail system at host mail96.lwspanel.com.

I'm sorry to have to inform you that your message could not be delivered.

<{addr}>: 554 5.7.1 <{addr}>: Relay access denied
"""

DSN_REPORT = """Content-Type: message/delivery-status

Reporting-MTA: dns; mail96.lwspanel.com
Final-Recipient: rfc822; {addr}
Action: failed
Status: 5.1.1
Diagnostic-Code: smtp; 550 5.1.1 <{addr}>: Recipient address rejected: User unknown
"""

SOFT_BOUNCE = """Final-Recipient: rfc822; {addr}
Action: delayed
Status: 4.2.2
Diagnostic-Code: smtp; 452 4.2.2 Mailbox full, retrying
"""


# --------------------------------------------------------------------------- #
# Detection and parsing
# --------------------------------------------------------------------------- #
def test_recognises_a_delivery_report(app):
    assert bounce_processing.looks_like_bounce(_report("peu importe"))
    assert bounce_processing.looks_like_bounce(
        _report("x", subject="Delivery Status Notification (Failure)", sender="postmaster@x.fr")
    )


def test_a_normal_reply_is_not_a_bounce(app):
    reply = _report("Bonjour, merci pour votre message.",
                    subject="Re: Votre proposition", sender="julien@artisan.fr")
    assert not bounce_processing.looks_like_bounce(reply)


def test_parses_the_postfix_plain_text_form(app):
    addr = f"contact-{uuid.uuid4().hex[:6]}@lba-plombier-lyon.fr"
    failures = bounce_processing.parse_bounce(_report(POSTFIX_RELAY_DENIED.format(addr=addr)))
    assert len(failures) == 1
    assert failures[0]["email"] == addr
    assert failures[0]["permanent"] is True
    assert "Relay access denied" in failures[0]["reason"]


def test_parses_the_machine_readable_report(app):
    addr = f"contact-{uuid.uuid4().hex[:6]}@expert-climatisation.fr"
    failures = bounce_processing.parse_bounce(_report(DSN_REPORT.format(addr=addr)))
    assert len(failures) == 1
    assert failures[0]["email"] == addr
    assert failures[0]["permanent"] is True
    assert "User unknown" in failures[0]["reason"]


def test_an_outbound_message_is_never_read_as_a_bounce(app):
    row = EmailMessage(direction=DIRECTION_OUTBOUND, status="sent",
                       from_addr="contact@pilotcore.fr", to_addr="x@y.fr",
                       subject="Undelivered Mail Returned to Sender", body="…")
    db.session.add(row)
    db.session.commit()
    assert not bounce_processing.looks_like_bounce(row)


# --------------------------------------------------------------------------- #
# Effect on the prospect
# --------------------------------------------------------------------------- #
def test_a_hard_bounce_takes_the_address_out_of_the_sending_pool(app):
    addr = f"dead-{uuid.uuid4().hex[:6]}@artisan-inexistant.fr"
    prospect = _prospect(addr)
    _report(POSTFIX_RELAY_DENIED.format(addr=addr))

    result = bounce_processing.process_bounces()
    assert result["marked"] == 1

    db.session.refresh(prospect)
    assert prospect.status == "skipped"
    assert prospect.email_confidence == "low"
    assert "[Rebond]" in prospect.notes
    assert "Relay access denied" in prospect.notes


def test_a_temporary_failure_leaves_the_address_alone(app):
    """A full mailbox works again tomorrow — punishing it would lose a real lead."""
    addr = f"full-{uuid.uuid4().hex[:6]}@artisan-occupe.fr"
    prospect = _prospect(addr)
    _report(SOFT_BOUNCE.format(addr=addr))

    result = bounce_processing.process_bounces()
    assert result["temporary_ignored"] >= 1

    db.session.refresh(prospect)
    assert prospect.status == "ready"
    assert "[Rebond]" not in (prospect.notes or "")


def test_processing_twice_does_not_double_count(app):
    addr = f"twice-{uuid.uuid4().hex[:6]}@artisan-inexistant.fr"
    prospect = _prospect(addr)
    _report(POSTFIX_RELAY_DENIED.format(addr=addr))

    first = bounce_processing.process_bounces()
    second = bounce_processing.process_bounces()
    assert first["marked"] == 1
    assert second["marked"] == 0
    assert second["already_marked"] >= 1

    db.session.refresh(prospect)
    assert prospect.notes.count("[Rebond]") == 1


def test_a_bounce_for_an_unknown_address_is_counted_not_crashed(app):
    _report(POSTFIX_RELAY_DENIED.format(addr=f"ghost-{uuid.uuid4().hex[:6]}@nulle-part.fr"))
    result = bounce_processing.process_bounces()
    assert result["unknown_recipient"] >= 1


# --------------------------------------------------------------------------- #
# The reason it matters: no campaign may retry a dead address
# --------------------------------------------------------------------------- #
def test_a_bounced_address_is_excluded_even_when_the_segment_asks_for_everyone(app):
    """The defect: an empty status filter meant "all statuses", dead ones included."""
    city = f"Ville-{uuid.uuid4().hex[:8]}"
    alive = _prospect(f"ok-{uuid.uuid4().hex[:6]}@artisan-valide.fr", city=city)
    dead = _prospect(f"ko-{uuid.uuid4().hex[:6]}@artisan-inexistant.fr", city=city)
    _report(POSTFIX_RELAY_DENIED.format(addr=dead.email))
    bounce_processing.process_bounces()

    audience = campaigns.preview_audience(
        {"cities": [city], "statuses": [], "exclude_contacted": False, "limit": 100}
    )
    emails = {row["email"] for row in audience["sample"]}
    assert audience["total"] == 1
    assert alive.email in emails
    assert dead.email not in emails


def test_a_bounced_address_is_never_picked_up_when_preparing_a_campaign(app):
    city = f"Ville-{uuid.uuid4().hex[:8]}"
    _prospect(f"ok2-{uuid.uuid4().hex[:6]}@artisan-valide.fr", city=city)
    dead = _prospect(f"ko2-{uuid.uuid4().hex[:6]}@artisan-inexistant.fr", city=city)
    _report(POSTFIX_RELAY_DENIED.format(addr=dead.email))
    bounce_processing.process_bounces()

    campaign = campaigns.create_campaign(name="Après rebond", template="offre")
    campaign.subject = "Un objet"
    campaign.set_segment({"cities": [city], "statuses": [], "exclude_contacted": False, "limit": 50})
    db.session.commit()
    campaigns.prepare_campaign(campaign.id)

    recipients = {r.email for r in campaign.recipients}
    assert dead.email not in recipients
    assert len(recipients) == 1


# --------------------------------------------------------------------------- #
# Console
# --------------------------------------------------------------------------- #
def test_admin_can_process_bounces_from_the_inbox(app, client):
    _login_admin(client)
    addr = f"admin-{uuid.uuid4().hex[:6]}@artisan-inexistant.fr"
    prospect = _prospect(addr)
    _report(POSTFIX_RELAY_DENIED.format(addr=addr))

    response = client.post("/admin/emails/process-bounces", follow_redirects=True)
    assert response.status_code == 200

    db.session.refresh(prospect)
    assert prospect.status == "skipped"


def test_bounce_action_requires_admin(client):
    assert client.post("/admin/emails/process-bounces").status_code in (302, 401, 403)


def test_campaign_tables_are_browsable_in_the_console(app, client):
    _login_admin(client)
    for table in ("email_campaigns", "campaign_recipients"):
        assert client.get(f"/admin/database/{table}").status_code == 200
