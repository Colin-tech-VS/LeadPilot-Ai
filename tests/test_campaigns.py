"""Mailing campaigns: designer rendering, audience, batched send, reporting."""
import uuid
from unittest.mock import patch

from app.core.extensions import db
from app.models.email_campaign import (
    R_PENDING,
    R_SENT,
    R_UNSUBSCRIBED,
    STATUS_SENT,
    CampaignRecipient,
)
from app.models.outreach_prospect import OutreachProspect
from app.services import campaign_render, campaigns


def _login_admin(client):
    with client.session_transaction() as sess:
        sess["admin_authenticated"] = True
        sess["admin_username"] = "admin"


def _city():
    """A city name unique to one test: the suite shares one database per run,
    so counting assertions must be scoped or they see other tests' rows."""
    return f"Ville-{uuid.uuid4().hex[:8]}"


def _prospect(**kwargs):
    defaults = {
        "company_name": "Dupont Plomberie",
        "email": f"contact-{uuid.uuid4().hex[:8]}@dupont-plomberie.fr",
        "trade_type": "plombier",
        "city": "Lyon",
        "status": "ready",
        "source": "rge_ademe",
    }
    defaults.update(kwargs)
    row = OutreachProspect(**defaults)
    db.session.add(row)
    db.session.commit()
    return row


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def test_render_produces_table_html_with_every_block(app):
    design = {
        "settings": {"accent": "#123456", "width": 600},
        "blocks": [
            {"id": "a", "type": "header", "title": "PilotCore"},
            {"id": "b", "type": "heading", "text": "Bonjour {{prenom}}"},
            {"id": "c", "type": "text", "html": "<p>Un mot pour {{entreprise}}.</p>"},
            {"id": "d", "type": "list", "items": ["Un", "Deux"]},
            {"id": "e", "type": "button", "label": "Essayer", "url": "{{lien_inscription}}"},
            {"id": "f", "type": "offer", "name": "Pro", "price": "349 €", "features": ["RDV auto"]},
            {"id": "g", "type": "stats", "items": [{"value": "24h", "label": "Appels"}]},
            {"id": "h", "type": "quote", "text": "Ça marche", "author": "Julien"},
            {"id": "i", "type": "divider"},
            {"id": "j", "type": "spacer", "height": 20},
            {"id": "k", "type": "footer", "html": "<p>Répondez à cet e-mail.</p>"},
        ],
    }
    html = campaign_render.render_html(design, ctx=campaign_render.sample_context())

    assert html.startswith("<!DOCTYPE html>")
    assert "<table" in html
    assert "Bonjour Julien" in html          # merge tag resolved
    assert "Dupont Plomberie" in html
    assert "349 €" in html
    assert "24h" in html
    assert "#123456" in html                  # accent colour applied


def test_greeting_stays_correct_when_the_contact_has_no_first_name(app):
    """Sourced registers carry a company, not a person — « Bonjour , » must
    never reach a mailbox, and neither must « Bonjour bonjour »."""
    design = {"blocks": [{"id": "a", "type": "text", "html": "<p>{{salutation}}, un mot rapide.</p>"}]}

    named = campaign_render.merge_context(first_name="Julien")
    assert "Bonjour Julien, un mot rapide." in campaign_render.render_html(design, ctx=named)

    anonymous = campaign_render.merge_context(company_name="Dupont Plomberie")
    html = campaign_render.render_html(design, ctx=anonymous)
    assert "Bonjour, un mot rapide." in html
    assert "Bonjour bonjour" not in html


def test_an_empty_tag_does_not_leave_a_hole_in_the_sentence(app):
    design = {"blocks": [{"id": "a", "type": "heading", "text": "Bonjour {{prenom}} , ça va ?"}]}
    html = campaign_render.render_html(design, ctx=campaign_render.merge_context())
    assert "Bonjour, ça va ?" in html


def test_unknown_merge_tag_is_removed_not_shown(app):
    design = {"blocks": [{"id": "a", "type": "heading", "text": "Salut {{inconnu}} !"}]}
    html = campaign_render.render_html(design, ctx=campaign_render.sample_context())
    assert "{{inconnu}}" not in html
    assert "Salut" in html


def test_unsubscribe_footer_is_always_appended(app):
    """A campaign with no footer block still ships an opt-out link."""
    design = {"blocks": [{"id": "a", "type": "heading", "text": "Coucou"}]}
    ctx = campaign_render.merge_context(unsubscribe_url="https://www.pilotcore.fr/desinscription/tok")
    html = campaign_render.render_html(design, ctx=ctx)
    assert "Se désinscrire" in html
    assert "/desinscription/tok" in html

    plain = campaign_render.render_plain(design, ctx=ctx)
    assert "/desinscription/tok" in plain


def test_active_markup_is_stripped_from_rich_text(app):
    design = {
        "blocks": [
            {
                "id": "a",
                "type": "text",
                "html": '<p onclick="steal()">Bonjour</p><script>alert(1)</script>',
            }
        ]
    }
    html = campaign_render.render_html(design, ctx=campaign_render.sample_context())
    assert "<script" not in html
    assert "onclick" not in html
    assert "Bonjour" in html


def test_a_broken_block_does_not_break_the_email(app):
    design = {
        "blocks": [
            {"id": "a", "type": "stats", "items": "pas une liste"},
            {"id": "b", "type": "heading", "text": "Le reste passe"},
        ]
    }
    html = campaign_render.render_html(design, ctx=campaign_render.sample_context())
    assert "Le reste passe" in html


# --------------------------------------------------------------------------- #
# Audience
# --------------------------------------------------------------------------- #
def test_audience_excludes_optouts_and_missing_emails(app):
    city = _city()
    keep = _prospect(city=city)
    _prospect(city=city, email=None, status="new")
    _prospect(city=city, status="unsubscribed")

    result = campaigns.preview_audience(
        {"cities": [city], "statuses": [], "exclude_contacted": False, "limit": 100}
    )
    assert result["total"] == 1
    assert result["sample"][0]["email"] == keep.email


def test_audience_filters_by_trade_and_city(app):
    # The suite shares one database within a run, so filter on a city name no
    # other test uses rather than asserting on a global count.
    city = f"Ville-{uuid.uuid4().hex[:8]}"
    _prospect(trade_type="plombier", city=city)
    _prospect(trade_type="couvreur", city=city)

    plumbers = campaigns.preview_audience(
        {"trades": ["plombier"], "cities": [city], "statuses": [], "exclude_contacted": False}
    )
    assert plumbers["total"] == 1
    assert plumbers["sample"][0]["city"] == city


def test_prepare_is_idempotent(app):
    city = _city()
    _prospect(city=city)
    _prospect(city=city)
    campaign = campaigns.create_campaign(name="Test", template="offre")
    campaign.set_segment(
        {"cities": [city], "statuses": [], "exclude_contacted": False, "limit": 100}
    )
    db.session.commit()

    first = campaigns.prepare_campaign(campaign.id)
    second = campaigns.prepare_campaign(campaign.id)
    assert first["added"] == 2
    assert second["added"] == 0
    assert second["total"] == 2


def test_prepare_respects_the_limit(app):
    city = _city()
    for _ in range(4):
        _prospect(city=city)
    campaign = campaigns.create_campaign(name="Plafond", template="offre")
    campaign.set_segment(
        {"cities": [city], "statuses": [], "exclude_contacted": False, "limit": 2}
    )
    db.session.commit()

    result = campaigns.prepare_campaign(campaign.id)
    assert result["added"] == 2


# --------------------------------------------------------------------------- #
# Sending
# --------------------------------------------------------------------------- #
def _ready_campaign(app, count=3):
    city = _city()
    for _ in range(count):
        _prospect(city=city)
    campaign = campaigns.create_campaign(name="Envoi", template="offre")
    campaign.subject = "Vos appels manqués, {{prenom}}"
    campaign.set_segment(
        {"cities": [city], "statuses": [], "exclude_contacted": False, "limit": 100}
    )
    db.session.commit()
    campaigns.prepare_campaign(campaign.id)
    return campaign


def test_send_batch_is_resumable_and_marks_done(app):
    campaign = _ready_campaign(app, count=3)

    first = campaigns.send_batch(campaign.id, batch_size=2)
    assert first["sent"] == 2
    assert first["remaining"] == 1
    assert first["done"] is False

    second = campaigns.send_batch(campaign.id, batch_size=2)
    assert second["sent"] == 1
    assert second["remaining"] == 0
    assert second["done"] is True
    assert second["status"] == STATUS_SENT


def test_send_marks_the_prospect_contacted(app):
    campaign = _ready_campaign(app, count=1)
    campaigns.send_batch(campaign.id, batch_size=5)

    recipient = campaign.recipients.first()
    assert recipient.status == R_SENT
    assert recipient.email_message_id is not None

    prospect = db.session.get(OutreachProspect, recipient.prospect_id)
    assert prospect.status == "contacted"
    assert prospect.last_contacted_at is not None


def test_each_recipient_gets_their_own_merged_body(app):
    city = _city()
    _prospect(first_name="Julien", company_name="Plomberie Julien", city=city)
    campaign = campaigns.create_campaign(name="Perso", template="offre")
    campaign.subject = "Bonjour {{prenom}}"
    campaign.set_design(
        {"blocks": [{"id": "a", "type": "text", "html": "<p>{{entreprise}} à {{ville}}</p>"}]}
    )
    campaign.set_segment(
        {"cities": [city], "statuses": [], "exclude_contacted": False, "limit": 10}
    )
    db.session.commit()
    campaigns.prepare_campaign(campaign.id)

    with patch("app.services.campaigns.admin_email.send_email") as send:
        send.return_value = type("Row", (), {"id": uuid.uuid4(), "status": "sent", "error": None})()
        campaigns.send_batch(campaign.id, batch_size=5)

    kwargs = send.call_args.kwargs
    args = send.call_args.args
    assert args[1] == "Bonjour Julien"
    assert "Plomberie Julien" in kwargs["html_body"]
    assert "desinscription/" in kwargs["html_body"]
    assert "List-Unsubscribe" not in kwargs  # passed as list_unsubscribe, not a header dict
    assert "desinscription/" in kwargs["list_unsubscribe"]


def test_send_never_mails_someone_who_used_the_unsubscribe_link(app):
    campaign = _ready_campaign(app, count=2)
    campaigns.unsubscribe(campaign.recipients.first().unsub_token)

    result = campaigns.send_batch(campaign.id, batch_size=5)
    assert result["sent"] == 1  # the opted-out row is no longer pending at all
    assert result["done"] is True


def test_send_skips_a_prospect_opted_out_through_another_channel(app):
    """The recipient row is still pending, but the prospect asked to stop —
    e.g. by replying, marked from the prospection page. The batch must catch it."""
    from app.models.email_campaign import utcnow

    campaign = _ready_campaign(app, count=2)
    recipient = campaign.recipients.first()
    prospect = db.session.get(OutreachProspect, recipient.prospect_id)
    prospect.opted_out_at = utcnow()
    prospect.status = "unsubscribed"
    db.session.commit()

    result = campaigns.send_batch(campaign.id, batch_size=5)
    assert result["sent"] == 1
    assert result["skipped"] == 1
    assert db.session.get(CampaignRecipient, recipient.id).status == R_UNSUBSCRIBED


def test_send_refuses_an_empty_or_untitled_campaign(app):
    campaign = campaigns.create_campaign(name="Vide", template="blank")
    campaign.subject = ""
    campaign.set_design({"blocks": []})
    db.session.commit()

    try:
        campaigns.send_batch(campaign.id)
        raise AssertionError("expected CampaignError")
    except campaigns.CampaignError as exc:
        assert "objet" in str(exc).lower()


# --------------------------------------------------------------------------- #
# Unsubscribe
# --------------------------------------------------------------------------- #
def test_unsubscribe_is_terminal_and_covers_every_campaign(app, client):
    city = _city()
    prospect = _prospect(city=city)
    one = campaigns.create_campaign(name="Une", template="offre")
    two = campaigns.create_campaign(name="Deux", template="offre")
    for campaign in (one, two):
        campaign.set_segment(
            {"cities": [city], "statuses": [], "exclude_contacted": False, "limit": 10}
        )
        db.session.commit()
        campaigns.prepare_campaign(campaign.id)

    token = one.recipients.first().unsub_token
    response = client.get(f"/desinscription/{token}")
    assert response.status_code == 200

    db.session.refresh(prospect)
    assert prospect.opted_out_at is not None
    assert prospect.status == "unsubscribed"

    # One click covers every list the same address sits on, not just this one.
    still_queued = CampaignRecipient.query.filter(
        CampaignRecipient.email == prospect.email,
        CampaignRecipient.status == R_PENDING,
    ).count()
    assert still_queued == 0
    assert two.recipients.first().status == R_UNSUBSCRIBED


def test_unknown_unsubscribe_token_is_a_404_page_not_a_crash(client):
    response = client.get("/desinscription/nimporte-quoi")
    assert response.status_code == 404
    assert "plus valide" in response.get_data(as_text=True)


# --------------------------------------------------------------------------- #
# Reporting & console
# --------------------------------------------------------------------------- #
def test_stats_count_opens_and_clicks(app):
    from app.services import email_tracking

    campaign = _ready_campaign(app, count=2)
    campaigns.send_batch(campaign.id, batch_size=5)

    recipient = campaign.recipients.first()
    message = db.session.get(
        __import__("app.models.email_message", fromlist=["EmailMessage"]).EmailMessage,
        recipient.email_message_id,
    )
    email_tracking.record_open(message.track_token)
    email_tracking.record_click(message.track_token, "https://www.pilotcore.fr/register")

    stats = campaigns.campaign_stats(campaign.id)
    assert stats["sent"] == 2
    assert stats["unique_opens"] == 1
    assert stats["unique_clicks"] == 1
    assert stats["top_links"]
    assert stats["top_links"][0]["url"].startswith("https://www.pilotcore.fr/register")


def test_a_sent_campaign_can_no_longer_be_edited(app):
    campaign = _ready_campaign(app, count=1)
    campaigns.send_batch(campaign.id, batch_size=5)

    try:
        campaigns.save_campaign(campaign.id, subject="Trop tard")
        raise AssertionError("expected CampaignError")
    except campaigns.CampaignError as exc:
        assert "dupliquez" in str(exc).lower()


def test_duplicate_reopens_an_editable_copy(app):
    campaign = _ready_campaign(app, count=1)
    campaigns.send_batch(campaign.id, batch_size=5)

    copy = campaigns.duplicate_campaign(campaign.id)
    assert copy.is_editable
    assert copy.design() == campaign.design()
    assert copy.recipients.count() == 0


def test_admin_campaign_pages_render(app, client):
    _login_admin(client)
    campaign = campaigns.create_campaign(name="Rendu", template="offre")

    assert client.get("/admin/campagnes").status_code == 200
    assert client.get(f"/admin/campagnes/{campaign.id}").status_code == 200
    assert client.get(f"/admin/campagnes/{campaign.id}/rapport").status_code == 200

    preview = client.get(f"/admin/campagnes/{campaign.id}/apercu")
    assert preview.status_code == 200
    assert b"<table" in preview.data


def test_campaign_pages_require_admin(client):
    assert client.get("/admin/campagnes").status_code in (302, 401, 403)


def test_live_preview_endpoint_renders_an_unsaved_design(app, client):
    _login_admin(client)
    response = client.post(
        "/admin/api/campaigns/preview",
        json={"design": {"blocks": [{"id": "a", "type": "heading", "text": "Pas encore enregistré"}]}},
    )
    assert response.status_code == 200
    assert "Pas encore enregistré" in response.get_json()["html"]
