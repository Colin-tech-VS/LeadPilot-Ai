"""Anti-spam / délivrabilité : en-têtes et parité texte-HTML du MIME sortant.

Le filtre sortant de LWS (SpamAssassin) bloque les messages dont le score est
trop élevé. Ces tests verrouillent les correctifs qui font baisser ce score :
en-tête Date (MISSING_DATE), partie texte alignée sur le HTML (MPART_ALT_DIFF)
et List-Unsubscribe pour la prospection.
"""
from email.utils import parsedate_to_datetime

from app.services.admin_email import _build_mime, _html_to_text

BIG_HTML = """<!DOCTYPE html><html><head><style>p{color:red}</style></head>
<body><h1>Votre devis DV-2026-001</h1>
<p>Bonjour, consultez votre devis en ligne, signez-le et r&eacute;glez l'acompte.</p>
<p><a href="https://www.pilotcore.fr/sign/abc">Voir et signer le devis</a></p>
<p>PilotCore — Réceptionniste IA &amp; prise de RDV pour artisans.</p>
</body></html>"""


def test_mime_has_date_header():
    mime = _build_mime("contact@pilotcore.fr", "client@example.com", "Sujet", "corps")
    assert mime["Date"]
    # La date doit être parsable au format RFC 2822.
    assert parsedate_to_datetime(mime["Date"]) is not None


def test_mime_has_message_id_with_domain():
    mime = _build_mime("contact@pilotcore.fr", "client@example.com", "Sujet", "corps")
    assert mime["Message-ID"].endswith("@pilotcore.fr>")


def test_short_plain_body_is_replaced_by_html_derived_text():
    mime = _build_mime(
        "contact@pilotcore.fr",
        "client@example.com",
        "Votre devis",
        "Bonjour.",  # une ligne, très loin du contenu HTML
        is_html=True,
        html_body=BIG_HTML,
    )
    plain = mime.get_payload(0).get_payload(decode=True).decode("utf-8")
    assert "Votre devis DV-2026-001" in plain
    assert "https://www.pilotcore.fr/sign/abc" in plain
    assert "<" not in plain  # aucun tag HTML dans la partie texte


def test_substantial_plain_body_is_kept():
    plain_body = (
        "Bonjour,\n\nVotre devis DV-2026-001 est disponible en ligne.\n"
        "Consultez-le et signez-le : https://www.pilotcore.fr/sign/abc\n"
        "Réglez l'acompte si nécessaire.\n"
        "PilotCore — Réceptionniste IA et prise de RDV pour artisans."
    )
    mime = _build_mime(
        "contact@pilotcore.fr",
        "client@example.com",
        "Votre devis",
        plain_body,
        is_html=True,
        html_body=BIG_HTML,
    )
    plain = mime.get_payload(0).get_payload(decode=True).decode("utf-8")
    assert plain == plain_body


def test_list_unsubscribe_headers():
    lu = "<mailto:contact@pilotcore.fr?subject=desinscription>, <https://www.pilotcore.fr/contact>"
    mime = _build_mime(
        "contact@pilotcore.fr",
        "prospect@example.com",
        "Sujet",
        "corps",
        list_unsubscribe=lu,
    )
    assert mime["List-Unsubscribe"] == lu
    assert mime["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


def test_no_list_unsubscribe_by_default():
    mime = _build_mime("contact@pilotcore.fr", "client@example.com", "Sujet", "corps")
    assert mime["List-Unsubscribe"] is None


def test_html_to_text_strips_styles_and_keeps_links():
    text = _html_to_text(BIG_HTML)
    assert "color:red" not in text
    assert "Voir et signer le devis (https://www.pilotcore.fr/sign/abc)" in text
    assert "réglez l'acompte" in text  # entités HTML décodées
