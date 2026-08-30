"""Conversion landing on /pro: missed-call offer, first 50, honest copy."""
from app.utils.i18n import TRANSLATIONS


NEW_KEYS = (
    # The hero states the trial. It used to state the seat count instead
    # (« 50 places restantes sur 50 »), which announced an empty room.
    "landing.hero_trial",
    "landing.hero_trial_launch",
    "landing.flow_title",
    "landing.flow_1",
    "landing.flow_5",
    "landing.demo_audio_fallback",
    "landing.demo_audio_hint",
    "landing.demo_audio_pause",
    "landing.demo_clip_1",
    "landing.demo_clip_4",
    "landing.demo_dash_hint",
    "landing.demo_dash_idle",
    "landing.problem_wait",
    "landing.solution_title",
    "landing.dir_honesty",
    "landing.launch_1",
    "landing.trust_launch",
    "landing.roi_price_label",
    "landing.roi_hypothesis",
    "landing.pricing_paid_title",
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
    assert "recorded sample call" in low
    assert "not a live account" in low
    assert "/static/audio/demo/en/01-ai.mp3" in html
    assert "does not promise" in low
    assert "recruiting its first 50" in low or "first 50" in low


def test_pro_landing_plays_a_sample_call(client):
    html = client.get("/pro").data.decode()
    assert "PRO_DEMO_AUDIO" in html
    assert "Léa" in html
    assert "fuite" in html.lower()
    assert "enregistr" in html.lower()
    assert "standard pilotcore" in html.lower()
    assert "pro-demo-transcript" in html
    assert html.count("data-demo-turn=") == 4
    assert "Mettre en pause" in html
    assert html.count('"role": "ai"') == 2
    assert html.count('"role": "client"') == 2
    assert "/static/audio/demo/fr/01-ai.mp3" in html
    assert "pro-demo-dash" in html
    assert "pro-demo-map" in html
    assert "Sophie Martin" in html
    assert "Champs-Élysées" in html or "Champs-Elysées" in html
    assert "exemple" in html.lower()
    assert "pas un compte" in html.lower() or "not a live account" in html.lower()


def test_pro_demo_playlist_uses_recorded_clips(app):
    from app.routes.web import _pro_demo_playlist

    with app.test_request_context("/pro"):
        fr = _pro_demo_playlist("fr")
        en = _pro_demo_playlist("en")
    assert len(fr) == 4 and len(en) == 4
    assert all(clip["text"] for clip in fr + en)
    # Static URLs now carry a ?v=<content digest>, so compare the path only.
    assert fr[0]["src"].split("?")[0].endswith("/static/audio/demo/fr/01-ai.mp3")
    assert en[0]["src"].split("?")[0].endswith("/static/audio/demo/en/01-ai.mp3")
    assert TRANSLATIONS["en"]["landing.demo_audio_hint"].lower().startswith("a recorded sample call")
