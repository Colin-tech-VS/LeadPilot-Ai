"""Branded social post images."""
from unittest.mock import MagicMock


def test_branded_fallback_creates_png(app, monkeypatch):
    monkeypatch.setattr(
        "app.services.social_image._image_brief",
        lambda subject, tone: {"headline": "RDV en ligne", "visual_brief": subject},
    )
    monkeypatch.setattr("app.services.social_image._try_dalle", lambda brief: None)

    with app.app_context():
        from app.services import social_image

        result = social_image.generate_for_post("Promouvoir l'annuaire artisans", "engageant")
        assert result["image_path"].startswith("uploads/social/")
        assert result["image_url"].endswith(result["image_path"].split("/", 1)[-1])
        path = social_image.resolve_image_path(result["image_path"])
        assert path is not None
        assert path.stat().st_size > 1000
        assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_resolve_image_path_rejects_traversal(app):
    with app.app_context():
        from app.services import social_image

        assert social_image.resolve_image_path("../etc/passwd") is None
        assert social_image.resolve_image_path("uploads/social/../../secret.png") is None
        assert social_image.resolve_image_path("uploads/social/missing.png") is None


def test_publish_post_requires_image_file(app, monkeypatch):
    monkeypatch.setattr(
        "app.services.social.get_config",
        lambda: {"page_id": "123", "page_name": "PilotCore", "token": "tok"},
    )

    with app.app_context():
        from app.services import social

        post = social.publish_post("Hello", image_path="uploads/social/nonexistent.png")
        assert post.status == "failed"
        assert "Image requise" in (post.error or "")


def test_publish_post_uploads_photo(app, monkeypatch, tmp_path):
    img = tmp_path / "post.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 64)
    rel = "uploads/social/test-post.png"
    static_root = tmp_path / "static"
    (static_root / "uploads" / "social").mkdir(parents=True)
    (static_root / "uploads" / "social" / "test-post.png").write_bytes(img.read_bytes())

    monkeypatch.setattr(
        "app.services.social.get_config",
        lambda: {"page_id": "page1", "page_name": "PilotCore", "token": "tok"},
    )

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {"id": "photo_1", "post_id": "page1_2"}
    monkeypatch.setattr("app.services.social.requests.post", lambda *a, **k: mock_resp)

    with app.app_context():
        app.static_folder = str(static_root)
        from app.services import social
        from app.services import social_image

        monkeypatch.setattr(
            social_image,
            "resolve_image_path",
            lambda p: static_root / p if p else None,
        )

        post = social.publish_post("Bonjour", link="https://www.pilotcore.fr/pro", image_path=rel)
        assert post.status == "published", post.error
        assert post.image_path == rel
        assert post.permalink


def test_generate_payload_includes_image_fields(app, monkeypatch):
    monkeypatch.setattr("app.services.content_ai._complete", lambda *a, **k: "Post test #PilotCore")
    monkeypatch.setattr(
        "app.services.social_image._image_brief",
        lambda subject, tone: {"headline": "Essai gratuit", "visual_brief": subject},
    )
    monkeypatch.setattr("app.services.social_image._try_dalle", lambda brief: None)

    with app.app_context():
        from app.services.content_ai import generate_social_post
        from app.services import social_image

        payload = generate_social_post("Essai gratuit 14 jours", target_key="pro")
        payload.update(social_image.generate_for_post("Essai gratuit 14 jours", "engageant"))

    assert payload["message"]
    assert payload["image_path"].startswith("uploads/social/")
    assert "pilotcore.fr/static/" in payload["image_url"]
