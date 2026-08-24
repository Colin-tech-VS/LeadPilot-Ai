"""Shared ledger template for every outbound PilotCore email."""
from app.services.transactional_email import (
    BORDER_STRONG,
    INK,
    INK_DEEP,
    PAPER,
    SURFACE,
    render_email,
    wrap_plain_as_html,
)
from app.services.admin_email import send_email


def test_ledger_template_uses_paper_ink(app):
    with app.app_context():
        html = render_email(
            "Votre devis DV-1",
            "Bonjour Marie,",
            kicker="Devis",
            lines=["Consultez votre devis en ligne."],
            cta_label="Voir et signer le devis",
            cta_url="https://www.pilotcore.fr/sign/abc",
        )
    assert PAPER in html
    assert SURFACE in html
    assert INK in html
    assert INK_DEEP in html
    assert BORDER_STRONG in html
    assert "background:#F8FAFC" not in html
    assert "#334155" not in html
    assert "#0F172A" not in html
    assert "border-radius:12px" not in html
    assert "Devis" in html
    assert "Voir et signer le devis" in html
    assert "/static/images/logo-512.png" in html


def test_wrap_plain_as_html_splits_paragraphs(app):
    with app.app_context():
        html = wrap_plain_as_html(
            "[Contact] Demande de devis",
            "Bonjour,\n\nJe souhaite un plombier à Lyon.\nMerci.",
        )
    assert "Contact" in html
    assert "Demande de devis" in html
    assert "Je souhaite un plombier à Lyon." in html
    assert PAPER in html


def test_plain_send_stores_branded_html(app):
    app.config["SMTP_HOST"] = ""
    with app.app_context():
        row = send_email("client@example.com", "Rappel entretien", "Bonjour,\n\nPensez à votre chaudière.")
        assert row.is_html
        assert row.html_body
        assert PAPER in row.html_body
        assert "/t/o/" not in row.html_body
        assert "Pensez à votre chaudière." in row.html_body
        assert row.body.startswith("Bonjour")
