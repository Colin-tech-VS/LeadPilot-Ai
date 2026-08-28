"""Joining the two halves of artisan acquisition.

On one side, 12 000+ registry fiches — a public page per company, carrying its
name, its town and its trade, and no e-mail: INSEE does not publish one. On the
other, prospects sourced from the ADEME RGE register — an e-mail, and a SIRET
that used to be written into a free-text note and forgotten.

Nothing connected them, so the outreach could only ever talk about PilotCore.
It can now talk about *their* company: « votre fiche X est en ligne, voici le
lien ». These tests hold the join together, and — more important — hold the two
promises that make it safe to send: the mail only goes to someone who has a
fiche, and it never links to one that has since been claimed or withdrawn.
"""
import uuid
from unittest.mock import patch

import pytest

from app.core.extensions import db
from app.models.email_campaign import CampaignRecipient
from app.models.outreach_prospect import OutreachProspect
from app.models.registry_listing import (
    STATUS_CLAIMED,
    STATUS_LISTED,
    STATUS_OPTED_OUT,
    RegistryListing,
)
from app.services import artisan_sourcing, campaign_render, campaigns


def _siren():
    return str(uuid.uuid4().int)[:9]


# Deliberately not plombier × Lyon. Nothing is rolled back between tests in
# this suite, and a registry listing counts as substance in
# ``indexability.city_page_robots`` — a Lyon plumber fiche created here would
# make ``test_seo`` see an indexable page where it asserts a thin one.
def _listing(siren, *, status=STATUS_LISTED, name="PEINTURE DURAND"):
    listing = RegistryListing(
        siren=siren,
        siret=siren + "00019",
        name=name,
        trade_key="peintre",
        city="Vierzon",
        city_slug="vierzon",
        postal_code="18100",
        dept_code="18",
        status=status,
    )
    db.session.add(listing)
    return listing


def _prospect(siren=None, *, status="ready", contacted=None):
    prospect = OutreachProspect(
        company_name="PEINTURE DURAND",
        email=f"camp-{uuid.uuid4().hex[:10]}@exemple.fr",
        trade_type="peintre",
        city="Vierzon",
        siren=siren,
        source=artisan_sourcing._SOURCE,
        status=status,
        last_contacted_at=contacted,
    )
    db.session.add(prospect)
    return prospect


# ── The identifier survives the import ───────────────────────────────────────


def test_a_sourced_prospect_keeps_the_siren_of_its_company(app):
    row = {
        "siret": "98765432100019",
        "nom_entreprise": "DURAND COUVERTURE",
        "email": f"src-{uuid.uuid4().hex[:8]}@exemple.fr",
        "code_postal": "18100",
        "commune": "VIERZON",
        "domaine": "Pompe à chaleur : chauffage",
        "particulier": True,
        "lien_date_fin": "2099-01-01",
    }
    with app.app_context():
        with patch("app.services.artisan_sourcing._iter_rows", return_value=iter([row])):
            result = artisan_sourcing.source_artisans(target=5)
        assert result["imported"] == 1
        prospect = OutreachProspect.query.filter_by(email=row["email"]).one()
        assert prospect.siren == "987654321"


def test_the_backfill_recovers_the_siren_from_the_old_note(app):
    """Every import before this feature wrote the SIRET into ``notes`` and
    nowhere else. Reading it back is what makes this work on the base that
    already exists rather than only on future imports."""
    with app.app_context():
        p = _prospect()
        p.notes = "Registre RGE ADEME · Chauffage · SIRET 55510203000018 · particuliers."
        noise = _prospect()
        noise.notes = "Trouvé via recherche web, pas de SIRET connu"
        db.session.commit()

        report = artisan_sourcing.backfill_sirens()
        assert report["filled"] >= 1
        db.session.refresh(p)
        db.session.refresh(noise)
        assert p.siren == "555102030"
        assert noise.siren is None


def test_the_backfill_leaves_an_already_matched_prospect_alone(app):
    with app.app_context():
        p = _prospect(siren="111222333")
        p.notes = "Registre RGE ADEME · SIRET 99988877700011 ·"
        db.session.commit()

        artisan_sourcing.backfill_sirens()
        db.session.refresh(p)
        assert p.siren == "111222333"


# ── The audience only holds people who have a fiche ──────────────────────────


def _segment(**overrides):
    segment = campaigns.default_segment()
    segment.update(overrides)
    return segment


def test_the_audience_can_be_limited_to_companies_with_a_live_fiche(app):
    with app.app_context():
        siren = _siren()
        _listing(siren)
        with_fiche = _prospect(siren)
        without = _prospect(None)
        db.session.commit()

        emails = {
            p.email for p in campaigns.audience_query(_segment(with_listing=True)).all()
        }
        assert with_fiche.email in emails
        assert without.email not in emails

        # Unfiltered, both are still reachable — this is an opt-in narrowing.
        everyone = {p.email for p in campaigns.audience_query(_segment()).all()}
        assert {with_fiche.email, without.email} <= everyone


@pytest.mark.parametrize("status", [STATUS_CLAIMED, STATUS_OPTED_OUT])
def test_a_claimed_or_withdrawn_fiche_takes_its_prospect_out(app, status):
    """A claimed fiche belongs to someone who already has an account; a
    withdrawn one answers 410. Neither may be mailed about."""
    with app.app_context():
        siren = _siren()
        _listing(siren, status=status)
        prospect = _prospect(siren)
        db.session.commit()

        emails = {
            p.email for p in campaigns.audience_query(_segment(with_listing=True)).all()
        }
        assert prospect.email not in emails


def test_the_preview_says_which_prospects_have_one(app):
    with app.app_context():
        siren = _siren()
        _listing(siren)
        _prospect(siren)
        db.session.commit()

        preview = campaigns.preview_audience(_segment(with_listing=True), sample=5)
        assert preview["total"] >= 1
        assert all(row["has_listing"] for row in preview["sample"])


# ── The link is frozen, then re-checked before it is sent ────────────────────


def test_the_mail_links_to_the_companys_own_page(app):
    with app.app_context():
        siren = _siren()
        _listing(siren)
        _prospect(siren)
        db.session.commit()

        campaign = campaigns.create_campaign(name="Fiches", template="fiche")
        campaigns.save_campaign(campaign.id, segment=_segment(with_listing=True))
        campaigns.prepare_campaign(campaign.id)

        recipient = CampaignRecipient.query.filter_by(campaign_id=campaign.id).first()
        assert recipient is not None
        assert recipient.listing_siren == siren

        _subject, html, plain = campaigns._render_for(
            campaigns.get_campaign(campaign.id), recipient
        )
        assert f"/artisans/entreprise/{siren}" in html
        assert f"/artisans/entreprise/{siren}" in plain


def test_a_fiche_withdrawn_after_preparing_is_never_linked_to(app):
    """Days can pass between « préparer l'audience » and the batch going out.
    A removal request in that window has to win."""
    with app.app_context():
        siren = _siren()
        listing = _listing(siren)
        _prospect(siren)
        db.session.commit()

        campaign = campaigns.create_campaign(name="Fiches retirées", template="fiche")
        campaigns.save_campaign(campaign.id, segment=_segment(with_listing=True))
        campaigns.prepare_campaign(campaign.id)
        recipient = CampaignRecipient.query.filter_by(campaign_id=campaign.id).first()
        assert recipient.listing_siren == siren

        listing.status = STATUS_OPTED_OUT
        db.session.commit()

        _subject, html, _plain = campaigns._render_for(
            campaigns.get_campaign(campaign.id), recipient
        )
        assert f"/artisans/entreprise/{siren}" not in html
        # Degrades to the page that searches the register — never to a dead link.
        assert "/artisans/ma-fiche" in html


def test_the_signup_link_carries_the_recipients_trade_town_and_source(app):
    """The form prefills from the query string and records the source. A
    campaign is the one place that knows both for every recipient."""
    with app.app_context():
        ctx = campaign_render.merge_context(
            company_name="Peinture Durand", city="Vierzon", trade_type="peintre"
        )
        assert "trade=peintre" in ctx["lien_inscription"]
        assert "city=Vierzon" in ctx["lien_inscription"]
        assert "src=campagne" in ctx["lien_inscription"]

        # An unknown town or trade must not leave an empty parameter behind.
        bare = campaign_render.merge_context(company_name="X")
        assert bare["lien_inscription"].endswith("/register?src=campagne")


def test_the_campaign_source_has_a_label_in_the_dashboard(app):
    from app.services import signup_funnel

    assert signup_funnel.SOURCE_LABELS["campagne"]


def test_the_tag_never_resolves_to_nothing(app):
    """An unknown merge tag is deleted, so a missing fallback would silently
    produce ``href=""`` — a button that goes nowhere."""
    with app.app_context():
        ctx = campaign_render.merge_context(company_name="X", listing_url=None)
        assert ctx["lien_fiche"].endswith("/artisans/ma-fiche")
        assert campaign_render.sample_context()["lien_fiche"]


# ── The template arrives ready to send ───────────────────────────────────────


def test_the_fiche_template_comes_with_its_subject_and_its_filter(app):
    with app.app_context():
        campaign = campaigns.create_campaign(name="Sa fiche", template="fiche")

        assert "{{entreprise}}" in campaign.subject
        # The body says « voici votre fiche »: the narrowing ships with it
        # rather than being something to remember.
        assert campaign.segment()["with_listing"] is True
        design = campaign.design()
        urls = [b.get("url") for b in design["blocks"] if b.get("type") == "button"]
        assert "{{lien_fiche}}" in urls
        assert "{{lien_inscription}}" in urls


def test_the_fiche_mail_says_where_the_page_comes_from_and_how_to_leave(app):
    """We publish a page about a company that never asked. The mail that points
    at it has to carry the source and the way out."""
    with app.app_context():
        campaign = campaigns.create_campaign(name="Source", template="fiche")
        html = campaign_render.render_html(
            campaign.design(), ctx=campaign_render.sample_context()
        )
        assert "registre officiel des entreprises" in html
        assert "/artisans/retrait-fiche" in html


def test_the_other_templates_keep_their_own_subject(app):
    with app.app_context():
        offre = campaigns.create_campaign(name="Offre", template="offre")
        assert "appels manqués" in offre.subject.lower()
        assert offre.segment()["with_listing"] is False
