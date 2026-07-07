"""UTM links for Facebook social posts."""
from app.services.social_links import (
    build_tracked_url,
    build_tracked_url_for_target,
    display_url,
    ensure_tracked,
    get_target,
)


def test_build_tracked_url_adds_utm_params(app):
    with app.app_context():
        url = build_tracked_url("/pro", campaign="pro_landing", content="ai_post")
    assert "utm_source=facebook" in url
    assert "utm_medium=social" in url
    assert "utm_campaign=pro_landing" in url
    assert "utm_content=ai_post" in url
    assert url.startswith("http")
    assert "/pro?" in url or url.endswith("/pro") is False


def test_display_url_strips_utm(app):
    with app.app_context():
        tracked = build_tracked_url_for_target("home", content="ai_post")
        clean = display_url(tracked)
    assert "utm_" not in clean
    assert "pilotcore" in clean or "localhost" in clean


def test_ensure_tracked_on_clean_url(app):
    with app.app_context():
        from app.utils.seo import site_base_url

        base = site_base_url()
        out = ensure_tracked(f"{base}/pro", target_key="pro", content="manual_post")
    assert "utm_source=facebook" in out
    assert "utm_campaign=pro_landing" in out


def test_get_target_pro_audience():
    t = get_target("pro")
    assert t is not None
    assert t["path"] == "/pro"
    assert "artisan" in t["audience"].lower()


def test_generate_social_post_shape(app, monkeypatch):
    def fake_complete(system, user, **kwargs):
        assert "PilotCore" in system
        return (
            '{"message": "🔧 Test post\\n\\n#PilotCore #Artisan", '
            '"image_headline": "Réception 24/7", '
            '"visual_brief": "artisan au téléphone"}'
        )

    monkeypatch.setattr("app.services.content_ai._complete", fake_complete)

    with app.app_context():
        from app.services.content_ai import generate_social_post

        result = generate_social_post("Promouvoir l'essai gratuit", target_key="pro")
    assert "message" in result
    assert result["message"]
    assert result["image_headline"]
    assert result["visual_brief"]
    assert result["link"] and "utm_" in result["link"]
    assert result["display_link"] and "utm_" not in result["display_link"]
    assert result["target_key"] == "pro"
