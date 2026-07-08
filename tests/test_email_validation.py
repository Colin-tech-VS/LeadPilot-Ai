"""Recipient validation: reject harvested asset refs so they never get sent.

``logo@2x.png`` and friends match the address shape but are retina image
references, not mailboxes. Sending to them is a guaranteed "Bounced by relay"
that, en volume, poisons the sending domain's reputation.
"""
import uuid
from unittest.mock import patch

import pytest

from app.models.outreach_prospect import OutreachProspect
from app.services import prospecting
from app.services.email_validation import check_recipient, is_valid_recipient
from app.services.prospect_search import extract_emails_from_html


@pytest.mark.parametrize(
    "addr",
    [
        "logo@2x.png",
        "sprite@3x.jpg",
        "icon@2x.svg",
        "bg@1.5x.webp",
        "hero@2x.jpeg",
        "bundle@main.js",
        "styles@theme.css",
        "brochure@catalogue.pdf",
    ],
)
def test_asset_references_are_rejected(addr):
    ok, reason = check_recipient(addr)
    assert ok is False
    assert reason


@pytest.mark.parametrize(
    "addr",
    ["", "contact", "contact@", "@pilotcore.fr", "a@b", "two@@at.fr"],
)
def test_malformed_addresses_are_rejected(addr):
    assert is_valid_recipient(addr) is False


@pytest.mark.parametrize(
    "addr",
    [
        "contact@lba-climatisation-chaville.fr",
        "jean.martin@plomberie-lyon.fr",
        "info@artisan.com",
        "Contact@Pilotcore.FR",
    ],
)
def test_real_addresses_pass(addr):
    assert is_valid_recipient(addr) is True


def test_harvesting_drops_retina_asset_but_keeps_real_email():
    html = """
    <img src="assets/logo@2x.png" srcset="assets/logo@3x.png 3x">
    <a href="mailto:contact@plomberie-martin-lyon.fr">Nous écrire</a>
    """
    emails = extract_emails_from_html(html)
    assert "contact@plomberie-martin-lyon.fr" in emails
    assert all("@2x" not in e and "@3x" not in e for e in emails)
    assert not any(e.endswith(".png") for e in emails)


def test_send_skips_undeliverable_prospect_without_hitting_smtp(app):
    with app.app_context():
        from app.core.extensions import db

        prospect = OutreachProspect(
            id=uuid.uuid4(),
            company_name="Asset Corp",
            email="logo@2x.png",
            trade_type="plombier",
            city="Lyon",
            status="contacted",
            outreach_subject="Bonjour",
            outreach_body="Un message.",
        )
        db.session.add(prospect)
        db.session.commit()
        pid = prospect.id

        with patch("app.services.prospecting.admin_email.send_email") as send_mock:
            with pytest.raises(prospecting.ProspectingError):
                prospecting.send_outreach_email(pid)
            send_mock.assert_not_called()

        row = db.session.get(OutreachProspect, pid)
        assert row.status == "skipped"
        assert "Non délivrable" in (row.notes or "")
