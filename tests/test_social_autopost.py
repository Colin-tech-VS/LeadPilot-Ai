"""Facebook token health + autopost queue (preview before send)."""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from app.core.extensions import db
from app.models.social_post import SocialPost
from app.services import content_studio
from app.services import social
from app.services import social_autopost


def _connect_page():
    content_studio.set_setting(social.SETTING_PAGE_ID, "1246135508572421")
    content_studio.set_setting(social.SETTING_TOKEN, "page-token")
    content_studio.set_setting(social.SETTING_PAGE_NAME, "PilotCore")


def _wipe_posts():
    SocialPost.query.delete()
    db.session.commit()


def _wipe_fb_settings():
    for key in (
        social.SETTING_PAGE_ID,
        social.SETTING_TOKEN,
        social.SETTING_PAGE_NAME,
        social.SETTING_USER_TOKEN,
        social.SETTING_TOKEN_EXPIRES,
    ):
        content_studio.set_setting(key, "")


def test_inspect_token_never_expires(app, monkeypatch):
    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {
        "data": {
            "is_valid": True,
            "type": "PAGE",
            "expires_at": 0,
            "scopes": ["pages_manage_posts"],
        }
    }
    monkeypatch.setattr("app.services.social.requests.get", lambda *a, **k: mock_resp)

    with app.app_context():
        app.config["FACEBOOK_APP_ID"] = "app"
        app.config["FACEBOOK_APP_SECRET"] = "secret"
        info = social.inspect_token("page-token")
        assert info["is_valid"] is True
        assert info["never_expires"] is True
        assert info["expires_at"] == 0


def test_inspect_token_with_expiry(app, monkeypatch):
    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {
        "data": {"is_valid": True, "type": "USER", "expires_at": 1_900_000_000, "scopes": []}
    }
    monkeypatch.setattr("app.services.social.requests.get", lambda *a, **k: mock_resp)

    with app.app_context():
        info = social.inspect_token("user-token")
        assert info["is_valid"] is True
        assert info["never_expires"] is False
        assert info["expires_at"] == 1_900_000_000


def test_tick_waits_until_due(app, monkeypatch):
    with app.app_context():
        _wipe_posts()
        _connect_page()
        content_studio.set_setting(social.SETTING_AUTOPOST, "1")
        content_studio.set_setting(social.SETTING_INTERVAL, "6")
        due = datetime.now(timezone.utc) + timedelta(hours=5)
        post = SocialPost(
            platform="facebook",
            message="Aperçu en attente",
            link="https://www.pilotcore.fr/?utm_source=facebook",
            image_path="uploads/social/preview.png",
            status="queued",
            scheduled_for=due,
        )
        db.session.add(post)
        db.session.commit()

        published = []
        monkeypatch.setattr(
            "app.services.social.publish_post",
            lambda *a, **k: published.append(1) or post,
        )
        result = social_autopost.tick()
        assert result["action"] == "waiting"
        assert published == []
        assert social_autopost.queued_preview().id == post.id


def test_tick_publishes_due_then_creates_next_preview(app, monkeypatch):
    with app.app_context():
        _wipe_posts()
        _connect_page()
        content_studio.set_setting(social.SETTING_AUTOPOST, "1")
        content_studio.set_setting(social.SETTING_INTERVAL, "12")
        queued = SocialPost(
            platform="facebook",
            message="Post dû",
            link="https://www.pilotcore.fr/pro",
            image_path="uploads/social/due.png",
            status="queued",
            scheduled_for=datetime.now(timezone.utc) - timedelta(minutes=2),
        )
        db.session.add(queued)
        db.session.commit()
        queued_id = queued.id

        duplicate = SocialPost(
            platform="facebook",
            message="Post dû",
            link="https://www.pilotcore.fr/pro",
            image_path="uploads/social/due.png",
            status="published",
            external_id="123_456",
            permalink="https://www.facebook.com/123_456",
            published_at=datetime.now(timezone.utc),
        )

        def fake_publish(*args, **kwargs):
            db.session.add(duplicate)
            db.session.commit()
            return duplicate

        next_preview = SocialPost(
            platform="facebook",
            message="Prochain aperçu",
            status="queued",
            scheduled_for=datetime.now(timezone.utc) + timedelta(hours=12),
        )

        monkeypatch.setattr("app.services.social.publish_post", fake_publish)
        monkeypatch.setattr(
            "app.services.social_autopost.generate_preview",
            lambda **k: (db.session.add(next_preview) or db.session.commit() or next_preview),
        )

        result = social_autopost.tick()
        assert result["action"] == "published"
        refreshed = db.session.get(SocialPost, queued_id)
        assert refreshed.status == "published"
        assert db.session.get(SocialPost, duplicate.id) is None
        waiting = social_autopost.queued_preview()
        assert waiting is not None
        assert waiting.message == "Prochain aperçu"


def test_publish_queued_now_uses_blob_when_disk_file_is_gone(app, monkeypatch, tmp_path):
    png = b"\x89PNG\r\n\x1a\n" + b"queued-visual" * 40
    with app.app_context():
        _wipe_posts()
        _connect_page()
        content_studio.set_setting(social.SETTING_AUTOPOST, "1")
        content_studio.set_setting(social.SETTING_INTERVAL, "12")
        app.config["SOCIAL_UPLOAD_DIR"] = str(tmp_path)
        queued = SocialPost(
            platform="facebook",
            message="Un client appelle en urgence pour un dépannage",
            link="https://www.pilotcore.fr/pro",
            image_path="uploads/social/missing-on-disk.png",
            image_blob=png,
            generated_by_ai=True,
            status="queued",
            target_key="pro",
            scheduled_for=datetime.now(timezone.utc) - timedelta(minutes=2),
        )
        db.session.add(queued)
        db.session.commit()
        queued_id = queued.id

        monkeypatch.setattr(
            "app.services.social.get_config",
            lambda: {"page_id": "page1", "page_name": "PilotCore", "token": "tok"},
        )
        monkeypatch.setattr(
            "app.services.social.ensure_publish_config",
            lambda: {"page_id": "page1", "page_name": "PilotCore", "token": "tok"},
        )

        def fake_post(url, data=None, files=None, timeout=None):
            mock_resp = MagicMock()
            mock_resp.ok = True
            mock_resp.json.return_value = {"id": "page1_auto"}
            return mock_resp

        monkeypatch.setattr("app.services.social.requests.post", fake_post)
        monkeypatch.setattr(
            "app.services.social_autopost.generate_preview",
            lambda **k: queued,
        )

        published = social_autopost.publish_queued_now()
        assert published is not None
        assert published.id == queued_id
        assert published.status == "published", published.error
        assert "Image requise" not in (published.error or "")


def test_enable_autopost_does_not_publish(app, monkeypatch):
    with app.app_context():
        _wipe_posts()
        _connect_page()
        published = []
        monkeypatch.setattr(
            "app.services.social.publish_post",
            lambda *a, **k: published.append(True),
        )
        monkeypatch.setattr(
            "app.services.content_ai.generate_social_post",
            lambda *a, **k: {
                "message": "Texte aperçu",
                "image_headline": "RDV",
                "visual_brief": "artisan",
                "link": "https://www.pilotcore.fr/",
            },
        )
        monkeypatch.setattr(
            "app.services.social_image.generate_for_post",
            lambda *a, **k: {"image_path": "uploads/social/auto.png", "image_url": "/x.png"},
        )
        monkeypatch.setattr(
            "app.services.social_links.ensure_tracked",
            lambda link, **k: link or "https://www.pilotcore.fr/?utm_source=facebook",
        )

        post = social_autopost.enable_autopost(6)
        assert published == []
        assert post.status == "queued"
        due = post.scheduled_for
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        assert due > datetime.now(timezone.utc)
        assert social_autopost.is_enabled()


def test_generate_preview_falls_back_when_ai_fails(app, monkeypatch):
    with app.app_context():
        _wipe_posts()
        _connect_page()
        monkeypatch.setattr(
            "app.services.content_ai.generate_social_post",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("mistral down")),
        )
        monkeypatch.setattr("app.services.social_image._try_dalle", lambda brief: None)
        post = social_autopost.generate_preview()
        assert post.status == "queued"
        assert post.message
        assert "garantit" not in (post.message or "").lower()
        assert post.image_path


def test_autopost_save_returns_redirect_when_preview_would_fail(client, app, monkeypatch):
    with app.app_context():
        _wipe_posts()
        _connect_page()
    monkeypatch.setattr(
        "app.services.content_ai.generate_social_post",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr("app.services.social_image._try_dalle", lambda brief: None)
    with client.session_transaction() as sess:
        sess["admin_authenticated"] = True
        sess["admin_username"] = "admin"
    resp = client.post(
        "/admin/social/autopost",
        data={"enabled": "1", "interval": "24"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/admin/social")
    with app.app_context():
        assert social_autopost.is_enabled()
        assert social_autopost.queued_preview() is not None


def test_manual_publish_requires_preview_confirm(client, app):
    with app.app_context():
        _wipe_posts()
        before = SocialPost.query.count()
    with client.session_transaction() as sess:
        sess["admin_authenticated"] = True
        sess["admin_username"] = "admin"
    resp = client.post(
        "/admin/social/publish",
        data={"message": "Hello", "image_path": "uploads/social/x.png", "confirmed": "0"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    with app.app_context():
        assert SocialPost.query.count() == before


def test_social_page_shows_autopost_controls(client):
    with client.session_transaction() as sess:
        sess["admin_authenticated"] = True
        sess["admin_username"] = "admin"
    resp = client.get("/admin/social")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Autopublication" in html
    assert "6 h" in html
    assert "12 h" in html
    assert "24 h" in html
    assert "Aperçu du post" in html or "Aperçu" in html
    assert "confirmed" in html
    assert "token utilisateur" in html.lower()
    assert "Échanger en token illimité" in html or "Coller un token utilisateur" in html
    assert "LinkedIn" in html
    assert "Community Management" in html


def test_facebook_oauth_url_includes_callback(app):
    with app.app_context():
        app.config["FACEBOOK_APP_ID"] = "999"
        app.config["FACEBOOK_APP_SECRET"] = "s"
        url = social.facebook_oauth_url(
            "st",
            "https://www.pilotcore.fr/admin/social/facebook/callback",
        )
        assert "client_id=999" in url
        assert "state=st" in url
        assert "pages_manage_posts" in url
        assert "facebook.com" in url
        assert "callback" in url


def test_facebook_login_redirects_to_meta(client, app):
    app.config["FACEBOOK_APP_ID"] = "111"
    app.config["FACEBOOK_APP_SECRET"] = "secret"
    with client.session_transaction() as sess:
        sess["admin_authenticated"] = True
        sess["admin_username"] = "admin"
    resp = client.get(
        "/admin/social/facebook/login?page_id=1246135508572421",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    loc = resp.headers["Location"]
    assert "facebook.com" in loc
    assert "client_id=111" in loc
    with client.session_transaction() as sess:
        assert sess.get("fb_oauth_page_id") == "1246135508572421"
        assert sess.get("fb_oauth_state")


def test_connect_page_user_token_becomes_never_expiring(app, monkeypatch):
    with app.app_context():
        app.config["FACEBOOK_APP_ID"] = "111"
        app.config["FACEBOOK_APP_SECRET"] = "secret"
        _wipe_fb_settings()
        monkeypatch.setattr(
            social,
            "token_identity",
            lambda t: ("user-1", "Colin") if t in ("short-user", "long-user") else (None, None),
        )
        monkeypatch.setattr(
            social,
            "inspect_token",
            lambda t: {
                "is_valid": True,
                "type": "USER",
                "never_expires": False,
                "expires_at": 99,
                "scopes": [],
                "error": None,
            },
        )
        monkeypatch.setattr(
            social,
            "exchange_long_lived_user_token",
            lambda t: "long-user" if t == "short-user" else None,
        )
        monkeypatch.setattr(
            social,
            "resolve_page_access_token",
            lambda t, pid: ("page-never-token", "PilotCore") if t == "long-user" and pid == "page-1" else (None, None),
        )
        monkeypatch.setattr(social, "verify_connection", lambda **k: (True, "PilotCore"))
        monkeypatch.setattr(
            social,
            "inspect_stored_token",
            lambda: {
                "is_valid": True,
                "never_expires": True,
                "expires_at": 0,
                "type": "PAGE",
                "error": None,
            },
        )

        result = social.connect_page("page-1", "short-user")
        assert result["ok"] is True
        assert result["health"]["never_expires"] is True
        assert "illimité" in result["detail"].lower()
        assert content_studio.get_setting(social.SETTING_TOKEN) == "page-never-token"
        assert content_studio.get_setting(social.SETTING_USER_TOKEN) == "long-user"
        assert content_studio.get_setting(social.SETTING_PAGE_ID) == "page-1"


def test_connect_page_does_not_save_when_exchange_fails(app, monkeypatch):
    with app.app_context():
        app.config["FACEBOOK_APP_ID"] = "111"
        app.config["FACEBOOK_APP_SECRET"] = "secret"
        _wipe_fb_settings()
        monkeypatch.setattr(social, "token_identity", lambda t: ("user-1", "Colin"))
        monkeypatch.setattr(
            social,
            "inspect_token",
            lambda t: {"is_valid": True, "type": "USER", "never_expires": False, "expires_at": 1, "scopes": [], "error": None},
        )
        monkeypatch.setattr(social, "exchange_long_lived_user_token", lambda t: None)

        result = social.connect_page("page-1", "short-user")
        assert result["ok"] is False
        assert "échanger" in result["message"].lower()
        assert not (content_studio.get_setting(social.SETTING_TOKEN) or "").strip()
        assert not (content_studio.get_setting(social.SETTING_USER_TOKEN) or "").strip()


def test_connect_page_requires_app_credentials_for_user_token(app, monkeypatch):
    with app.app_context():
        app.config["FACEBOOK_APP_ID"] = ""
        app.config["FACEBOOK_APP_SECRET"] = ""
        _wipe_fb_settings()
        monkeypatch.setattr(social, "token_identity", lambda t: ("user-1", "Colin"))
        monkeypatch.setattr(
            social,
            "inspect_token",
            lambda t: {"is_valid": True, "type": "USER", "never_expires": False, "expires_at": 1, "scopes": [], "error": None},
        )
        result = social.connect_page("page-1", "short-user")
        assert result["ok"] is False
        assert "FACEBOOK_APP_ID" in result["message"]
        assert not (content_studio.get_setting(social.SETTING_TOKEN) or "").strip()


def test_connect_page_rejects_expiring_page_token(app, monkeypatch):
    with app.app_context():
        _wipe_fb_settings()
        monkeypatch.setattr(social, "token_identity", lambda t: ("page-1", "PilotCore"))
        monkeypatch.setattr(
            social,
            "inspect_token",
            lambda t: {
                "is_valid": True,
                "type": "PAGE",
                "never_expires": False,
                "expires_at": 1_900_000_000,
                "scopes": [],
                "error": None,
            },
        )
        result = social.connect_page("page-1", "expiring-page")
        assert result["ok"] is False
        assert "token de page" in result["message"].lower()
        assert "user token" in result["message"].lower()
        assert not (content_studio.get_setting(social.SETTING_TOKEN) or "").strip()


def test_connect_page_keeps_never_expiring_page_token(app, monkeypatch):
    with app.app_context():
        _wipe_fb_settings()
        monkeypatch.setattr(social, "token_identity", lambda t: ("page-1", "PilotCore"))
        monkeypatch.setattr(
            social,
            "inspect_token",
            lambda t: {
                "is_valid": True,
                "type": "PAGE",
                "never_expires": True,
                "expires_at": 0,
                "scopes": [],
                "error": None,
            },
        )
        monkeypatch.setattr(social, "verify_connection", lambda **k: (True, "PilotCore"))
        monkeypatch.setattr(
            social,
            "inspect_stored_token",
            lambda: {
                "is_valid": True,
                "type": "PAGE",
                "never_expires": True,
                "expires_at": 0,
                "error": None,
            },
        )
        result = social.connect_page("page-1", "never-page")
        assert result["ok"] is True
        assert content_studio.get_setting(social.SETTING_TOKEN) == "never-page"


def test_refresh_extends_user_token_when_page_never_expires(app, monkeypatch):
    with app.app_context():
        app.config["FACEBOOK_APP_ID"] = "111"
        app.config["FACEBOOK_APP_SECRET"] = "secret"
        content_studio.set_setting(social.SETTING_PAGE_ID, "page-1")
        content_studio.set_setting(social.SETTING_TOKEN, "old-page")
        content_studio.set_setting(social.SETTING_USER_TOKEN, "old-user")
        monkeypatch.setattr(social, "exchange_long_lived_user_token", lambda t: "new-user")
        monkeypatch.setattr(social, "resolve_page_access_token", lambda t, pid: ("new-page", "PilotCore"))
        monkeypatch.setattr(
            social,
            "inspect_stored_token",
            lambda: {"is_valid": True, "never_expires": True, "expires_at": 0, "type": "PAGE", "error": None},
        )
        health = social.refresh_never_expiring_token()
        assert health["never_expires"] is True
        assert content_studio.get_setting(social.SETTING_USER_TOKEN) == "new-user"
        assert content_studio.get_setting(social.SETTING_TOKEN) == "new-page"


def test_connect_page_asks_to_pick_when_several_pages(app, monkeypatch):
    with app.app_context():
        app.config["FACEBOOK_APP_ID"] = "1251548163615019"
        app.config["FACEBOOK_APP_SECRET"] = "secret"
        _wipe_fb_settings()
        monkeypatch.setattr(social, "token_identity", lambda t: ("user-1", "Colin"))
        monkeypatch.setattr(
            social,
            "inspect_token",
            lambda t: {
                "is_valid": True,
                "type": "USER",
                "never_expires": False,
                "expires_at": 1,
                "scopes": [],
                "error": None,
            },
        )
        monkeypatch.setattr(social, "exchange_long_lived_user_token", lambda t: "long-user")
        monkeypatch.setattr(social, "resolve_page_access_token", lambda t, pid: (None, None))
        monkeypatch.setattr(
            social,
            "list_user_pages",
            lambda t: [
                {"id": "1246135508572421", "name": "PilotCore"},
                {"id": "999", "name": "Autre page"},
            ],
        )
        result = social.connect_page("1251548163615019", "short-user")
        assert result["ok"] is False
        assert result["needs_page_choice"] is True
        assert len(result["pages"]) == 2
        assert "App ID" in result["message"]
        assert content_studio.get_setting(social.SETTING_USER_TOKEN) == "long-user"
        assert not (content_studio.get_setting(social.SETTING_TOKEN) or "").strip()
