"""AI copywriting for campaigns — grounding, schema, and refusal to invent."""
import json
from unittest.mock import patch

from app.core.extensions import db
from app.models.offer import Offer
from app.services import campaign_ai, campaign_render


def _login_admin(client):
    with client.session_transaction() as sess:
        sess["admin_authenticated"] = True
        sess["admin_username"] = "admin"


def test_knowledge_carries_the_live_offers_not_a_hardcoded_price(app):
    """The copywriter must read today's prices from the database.

    The row is removed again: the suite shares one database per run, and a
    stray offer changes what the public pricing page renders for other tests.
    """
    offer = Offer(key="pro-test", name="Pro", price="1234 €", period="HT / mois",
                  description="RDV automatiques", active=True, sort_order=99)
    offer.set_features(["Prise d'appel 24/7", "Rendez-vous posés"])
    db.session.add(offer)
    db.session.commit()
    try:
        knowledge = campaign_ai.site_knowledge()
        prices = {o["price"] for o in knowledge["offers"]}
        assert "1234 €" in prices
        assert knowledge["factsheet"]          # the site's own /llms-full.txt
        assert knowledge["site"].startswith("http")
    finally:
        db.session.delete(offer)
        db.session.commit()


def test_the_prompt_hands_the_model_the_site_facts(app):
    captured = {}

    def fake_complete(system, user, **kwargs):
        captured["system"] = system
        captured["user"] = user
        return json.dumps({
            "name": "Test", "subject": "Un objet", "preheader": "Un pré-en-tête",
            "blocks": [{"type": "heading", "text": "Titre"}],
        })

    with patch("app.services.campaign_ai.content_ai.is_available", return_value=True), \
         patch("app.services.campaign_ai.content_ai._complete", side_effect=fake_complete):
        campaign_ai.generate_campaign(brief="Présenter l'offre aux plombiers")

    assert "FAITS_SITE" in captured["user"]
    assert "OFFRES_EN_LIGNE" in captured["user"]
    # The model is told, in the system prompt, that it may not invent anything.
    assert "Aucun prix" in captured["system"]
    assert "{{salutation}}" in captured["system"]


def test_generation_returns_a_design_the_renderer_accepts(app):
    payload = json.dumps({
        "name": "Prospection plombiers",
        "subject": "Qui répond quand vous êtes sur le chantier ?",
        "preheader": "Un standard qui décroche",
        "blocks": [
            {"type": "heading", "text": "Bonjour"},
            {"type": "text", "html": "<p>{{salutation}}, un mot.</p>"},
            {"type": "button", "label": "Essayer", "url": "{{lien_inscription}}"},
        ],
    })
    with patch("app.services.campaign_ai.content_ai.is_available", return_value=True), \
         patch("app.services.campaign_ai.content_ai._complete", return_value=payload):
        result = campaign_ai.generate_campaign(brief="Prospection")

    assert result["subject"].startswith("Qui répond")
    blocks = campaign_render.blocks_of(result["design"])
    assert len(blocks) == 3
    assert all(b.get("id") for b in blocks)          # ids assigned server-side

    html = campaign_render.render_html(result["design"], ctx=campaign_render.sample_context())
    assert "Essayer" in html
    assert "{{" not in html


def test_invented_block_types_are_discarded(app):
    payload = json.dumps({
        "name": "X", "subject": "Y",
        "blocks": [
            {"type": "carousel", "slides": ["a", "b"]},
            {"type": "video", "url": "https://x"},
            {"type": "heading", "text": "Le seul bloc valide"},
        ],
    })
    with patch("app.services.campaign_ai.content_ai.is_available", return_value=True), \
         patch("app.services.campaign_ai.content_ai._complete", return_value=payload):
        result = campaign_ai.generate_campaign(brief="Prospection")

    blocks = result["design"]["blocks"]
    assert len(blocks) == 1
    assert blocks[0]["type"] == "heading"


def test_an_unusable_answer_raises_a_readable_error(app):
    with patch("app.services.campaign_ai.content_ai.is_available", return_value=True), \
         patch("app.services.campaign_ai.content_ai._complete", return_value="pas du json"):
        try:
            campaign_ai.generate_campaign(brief="Prospection")
            raise AssertionError("expected CampaignAIError")
        except campaign_ai.CampaignAIError as exc:
            assert "réessayez" in str(exc).lower()


def test_generation_without_an_api_key_says_so(app):
    with patch("app.services.campaign_ai.content_ai.is_available", return_value=False):
        try:
            campaign_ai.generate_campaign(brief="Prospection")
            raise AssertionError("expected CampaignAIError")
        except campaign_ai.CampaignAIError as exc:
            assert "MISTRAL_API_KEY" in str(exc)


def test_rewrite_keeps_the_block_type_and_id(app):
    block = {"id": "abc123", "type": "text", "html": "<p>Version initiale.</p>"}
    payload = json.dumps({"block": {"type": "text", "html": "<p>Version réécrite.</p>"}})
    with patch("app.services.campaign_ai.content_ai.is_available", return_value=True), \
         patch("app.services.campaign_ai.content_ai._complete", return_value=payload):
        result = campaign_ai.rewrite_block(block=block, instruction="Plus court")

    assert result["id"] == "abc123"
    assert result["type"] == "text"
    assert "réécrite" in result["html"]


def test_rewrite_refuses_a_block_type_swap(app):
    block = {"id": "abc123", "type": "text", "html": "<p>Texte.</p>"}
    payload = json.dumps({"block": {"type": "button", "label": "Clic", "url": "#"}})
    with patch("app.services.campaign_ai.content_ai.is_available", return_value=True), \
         patch("app.services.campaign_ai.content_ai._complete", return_value=payload):
        try:
            campaign_ai.rewrite_block(block=block, instruction="Plus court")
            raise AssertionError("expected CampaignAIError")
        except campaign_ai.CampaignAIError as exc:
            assert "type de bloc" in str(exc)


def test_generate_endpoint_needs_a_brief(app, client):
    _login_admin(client)
    response = client.post("/admin/api/campaigns/generate", json={"brief": ""})
    assert response.status_code == 400


def test_generate_endpoint_returns_the_design(app, client):
    _login_admin(client)
    payload = json.dumps({
        "name": "Via API", "subject": "Objet API",
        "blocks": [{"type": "heading", "text": "Titre API"}],
    })
    with patch("app.services.campaign_ai.content_ai.is_available", return_value=True), \
         patch("app.services.campaign_ai.content_ai._complete", return_value=payload):
        response = client.post("/admin/api/campaigns/generate", json={"brief": "Prospection plombiers"})

    assert response.status_code == 200
    data = response.get_json()
    assert data["subject"] == "Objet API"
    assert data["design"]["blocks"][0]["type"] == "heading"


def test_generate_endpoint_surfaces_an_ai_outage_as_502_not_500(app, client):
    _login_admin(client)
    with patch("app.services.campaign_ai.content_ai.is_available", return_value=True), \
         patch("app.services.campaign_ai.content_ai._complete", side_effect=RuntimeError("boom")):
        response = client.post("/admin/api/campaigns/generate", json={"brief": "Prospection"})
    assert response.status_code == 502
    assert "error" in response.get_json()
