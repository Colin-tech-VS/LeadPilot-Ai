"""Conversion landing on /pro: missed-call offer, first 50, honest copy."""
from app.utils.i18n import TRANSLATIONS


NEW_KEYS = (
    "landing.hero_seats_fallback",
    "landing.hero_seats_remaining",
    "landing.flow_title",
    "landing.flow_1",
    "landing.flow_5",
    "landing.demo_audio_fallback",
    "landing.problem_wait",
    "landing.solution_title",
    "landing.dir_honesty",
    "landing.launch_1",
    "landing.trust_launch",
    "landing.roi_price_label",
    "landing.roi_hypothesis",
    "landing.pricing_paid_title",
    "landing.cta_founding",
    "landing.faq_9_q",
    "landing.faq_9_a",
)


def test_conversion_keys_exist_in_fr_and_en():
    for key in NEW_KEYS:
        assert key in TRANSLATIONS["fr"], key
        assert key in TRANSLATIONS["en"], key
        assert TRANSLATIONS["fr"][key]
        assert TRANSLATIONS["en"][key]


def test_pro_landing_is_missed_call_first(client):
    html = client.get("/pro").data.decode()
    low = html.lower()
    assert "ne ratez plus aucun appel" in low
    assert "tester gratuitement" in low
    assert "écouter une démonstration" in low
    assert 'id="demo-flow"' in html
    assert "un appel manqué peut devenir" in low
    assert "comment ça marche" in low
    assert "tout ce qui se passe quand vous êtes occupé" in low
    assert "et vos clients peuvent aussi vous trouver" in low
    assert "combien vous coûte réellement un appel manqué" in low
    assert "commencez gratuitement" in low
    assert "est-ce que je garde mon numéro" in low
    assert "parlent à une ia" in low
    assert 'data-track="cta_trial_click"' in html
    assert "data-demo-listen" in html
    assert "conversion.js" in html
    assert "8 appels sur 10" not in low
    assert "rentabilisé dès" not in low
    assert "google agenda" not in low
    directory_at = low.index("et vos clients peuvent aussi vous trouver")
    assert html.lower().index("ne ratez plus aucun appel") < directory_at


def test_pro_landing_english_copy(client):
    html = client.get("/pro?lang=en").data.decode()
    low = html.lower()
    assert "never miss another call" in low
    assert "try it free" in low
    assert "listen to a demo" in low
    assert "does not promise" in low
    assert "recruiting its first 50" in low or "first 50" in low
