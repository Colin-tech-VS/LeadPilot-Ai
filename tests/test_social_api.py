"""API social generate endpoint."""
import json
from unittest.mock import patch


def test_api_social_generate_full_flow(client, app):
    fake = json.dumps(
        {
            "message": "📞 Réception 24/7 pour artisans ! #PilotCore #Artisan",
            "image_headline": "Réception 24/7",
            "visual_brief": "artisan avec téléphone en intervention",
        }
    )
    with patch("app.services.content_ai._complete", return_value=fake):
        with client.session_transaction() as sess:
            sess["admin_authenticated"] = True
            sess["admin_username"] = "admin"
        app.config["PUBLIC_BASE_URL"] = "https://www.pilotcore.fr"
        resp = client.post(
            "/admin/api/social/generate",
            json={
                "prompt": "Vendre la reception 24/7 aux artisan",
                "tone": "engageant",
                "target_key": "pro",
            },
        )
    assert resp.status_code == 200, resp.get_json()
    data = resp.get_json()
    assert data["message"]
    assert data["image_path"]
    assert data["image_url"]
    assert "utm_" in data["link"]
