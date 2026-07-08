"""Anti-spam / délivrabilité : en-têtes et parité texte-HTML du MIME sortant.

Le filtre sortant de LWS (SpamAssassin) bloque les messages dont le score est
trop élevé. Ces tests verrouillent les correctifs qui font baisser ce score :
en-tête Date (MISSING_DATE), partie texte alignée sur le HTML (MPART_ALT_DIFF)
et List-Unsubscribe pour la prospection.
"""
from email.utils import parsedate_to_datetime, parseaddr

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
    assert parsedate_to_datetime(mime["Date"]) is not None


def test_mime_has_message_id_with_domain():
    mime = _build_mime("contact@pilotcore.fr", "client@example.com", "Sujet", "corps")
    assert mime["Message-ID"].endswith("@pilotcore.fr>")


def test_multipart_plain_always_derived_from_html():
    mime = _build_mime(
        "contact@pilotcore.fr",
        "client@example.com",
        "Votre devis",
        "Bonjour.",  # corps brut différent du HTML
        is_html=True,
        html_body=BIG_HTML,
    )
    plain = mime.get_payload(0).get_payload(decode=True).decode("utf-8")
    assert "Votre devis DV-2026-001" in plain
    assert "https://www.pilotcore.fr/sign/abc" in plain
    assert "<" not in plain
    assert "Bonjour." not in plain  # pas le corps brut divergent


def test_multipart_plain_derived_even_when_long_plain_body():
    plain_body = (
        "Bonjour,\n\nTexte volontairement différent du HTML pour simuler la prospection.\n"
        "Cette version ne doit PAS être utilisée telle quelle dans le MIME.\n"
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
    assert "Votre devis DV-2026-001" in plain
    assert "simuler la prospection" not in plain


def test_reply_to_defaults_to_from():
    mime = _build_mime("contact@pilotcore.fr", "client@example.com", "Sujet", "corps")
    assert parseaddr(mime["Reply-To"])[1] == "contact@pilotcore.fr"


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
    assert mime["Precedence"] == "bulk"


def test_no_list_unsubscribe_by_default():
    mime = _build_mime("contact@pilotcore.fr", "client@example.com", "Sujet", "corps")
    assert mime["List-Unsubscribe"] is None


def test_html_to_text_strips_styles_and_keeps_links():
    text = _html_to_text(BIG_HTML)
    assert "color:red" not in text
    assert "Voir et signer le devis (https://www.pilotcore.fr/sign/abc)" in text
    assert "réglez l'acompte" in text
