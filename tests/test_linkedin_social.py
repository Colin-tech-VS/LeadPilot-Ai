"""LinkedIn OAuth (Share on LinkedIn) + publish."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.core.extensions import db
from app.models.social_post import SocialPost
from app.services import content_studio
from app.services import linkedin_social
from app.services import social
from app.services import social_autopost


def _wipe_li():
    for key in (
        linkedin_social.SETTING_ORG_ID,
        linkedin_social.SETTING_ORG_NAME,
        linkedin_social.SETTING_ACCESS_TOKEN,
        linkedin_social.SETTING_REFRESH_TOKEN,
        linkedin_social.SETTING_TOKEN_EXPIRES,
        linkedin_social.SETTING_MEMBER_ID,
        linkedin_social.SETTING_MEMBER_NAME,
    ):
        content_studio.set_setting(key, "")


@pytest.fixture(autouse=True)
def _clean_linkedin_settings(app):
    _wipe_li()
    yield
    _wipe_li()


def _connect_linkedin():
    content_studio.set_setting(linkedin_social.SETTING_ORG_ID, "5515715")
    content_studio.set_setting(linkedin_social.SETTING_ORG_NAME, "PilotCore")
    content_studio.set_setting(linkedin_social.SETTING_ACCESS_TOKEN, "li-token")
    later = datetime.now(timezone.utc) + timedelta(days=20)
    content_studio.set_setting(linkedin_social.SETTING_TOKEN_EXPIRES, later.isoformat())


def _wipe_posts():
    SocialPost.query.delete()
    db.session.commit()


def test_normalize_org_id():
    assert linkedin_social.normalize_org_id("5515715") == "5515715"
    assert linkedin_social.normalize_org_id("urn:li:organization:5515715") == "5515715"
    assert linkedin_social.normalize_org_id("https://www.linkedin.com/company/5515715/admin/") == "5515715"
    assert linkedin_social.normalize_org_id("pilotcore") == ""


def test_oauth_url_includes_callback_and_share_scopes(app):
    with app.app_context():
        app.config["LINKEDIN_CLIENT_ID"] = "li-app"
        app.config["LINKEDIN_CLIENT_SECRET"] = "secret"
        url = linkedin_social.oauth_url(
            "st",
            "https://www.pilotcore.fr/admin/social/linkedin/callback",
        )
        assert "client_id=li-app" in url
        assert "state=st" in url
        assert "w_member_social" in url
        assert "profile" in url
        assert "email" in url
        assert "openid" not in url
        assert "linkedin.com/oauth" in url
        assert "callback" in url
        assert "w_organization_social" not in url
        assert "rw_organization_admin" not in url


def test_oauth_scopes_env_override(app):
    with app.app_context():
        app.config["LINKEDIN_OAUTH_SCOPES"] = "openid profile email w_member_social"
        url = linkedin_social.oauth_url("st", "https://www.pilotcore.fr/admin/social/linkedin/callback")
        assert "openid" in url
        assert "w_member_social" in url


def test_linkedin_login_redirects(client, app):
    app.config["LINKEDIN_CLIENT_ID"] = "li-app"
    app.config["LINKEDIN_CLIENT_SECRET"] = "secret"
    with client.session_transaction() as sess:
        sess["admin_authenticated"] = True
        sess["admin_username"] = "admin"
    resp = client.get("/admin/social/linkedin/login", follow_redirects=False)
    assert resp.status_code == 302
    loc = resp.headers["Location"]
    assert "linkedin.com/oauth" in loc
    assert "client_id=li-app" in loc
    with client.session_transaction() as sess:
        assert sess.get("li_oauth_state")


def test_publish_post_article_success(app, monkeypatch, tmp_path):
    png = tmp_path / "post.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 40)

    def fake_create(token, payload):
        assert payload["author"] == "urn:li:organization:5515715"
        assert "article" in payload.get("content", {})
        return 201, {}, "urn:li:share:99"

    with app.app_context():
        _wipe_li()
        _wipe_posts()
        _connect_linkedin()
        monkeypatch.setattr("app.services.social_image.resolve_image_path", lambda p: png)
        monkeypatch.setattr(linkedin_social, "_upload_image", lambda *a, **k: "urn:li:image:abc")
        monkeypatch.setattr(linkedin_social, "_create_post", fake_create)

        post = linkedin_social.publish_post(
            "Essai 14 jours",
            link="https://www.pilotcore.fr/pro?utm_source=linkedin",
            image_path="uploads/social/post.png",
            target_key="pro",
        )
        assert post.status == "published"
        assert post.platform == "linkedin"
        assert post.external_id == "urn:li:share:99"
        assert "linkedin.com/feed/update" in (post.permalink or "")


def test_publish_post_requires_connection(app, monkeypatch, tmp_path):
    png = tmp_path / "post.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 40)
    with app.app_context():
        _wipe_li()
        _wipe_posts()
        monkeypatch.setattr("app.services.social_image.resolve_image_path", lambda p: png)
        post = linkedin_social.publish_post(
            "Hello",
            link="https://www.pilotcore.fr/",
            image_path="uploads/social/post.png",
        )
        assert post.status == "failed"
        assert "non connectée" in (post.error or "").lower()


def test_complete_oauth_picks_single_org(app, monkeypatch):
    with app.app_context():
        _wipe_li()
        monkeypatch.setattr(
            linkedin_social,
            "exchange_oauth_code",
            lambda code, uri: ({"access_token": "tok", "expires_in": 3600, "refresh_token": "ref"}, None),
        )
        monkeypatch.setattr(
            linkedin_social,
            "fetch_member_profile",
            lambda t: {"id": "mem1", "name": "Colin"},
        )
        monkeypatch.setattr(
            linkedin_social,
            "list_admin_orgs",
            lambda token=None: ([{"id": "5515715", "name": "PilotCore"}], None),
        )
        result = linkedin_social.complete_oauth("code", "https://www.pilotcore.fr/admin/social/linkedin/callback")
        assert result["ok"] is True
        assert content_studio.get_setting(linkedin_social.SETTING_ORG_ID) == "5515715"
        assert content_studio.get_setting(linkedin_social.SETTING_REFRESH_TOKEN) == "ref"


def test_complete_oauth_connects_member_without_org(app, monkeypatch):
    with app.app_context():
        _wipe_li()
        monkeypatch.setattr(
            linkedin_social,
            "exchange_oauth_code",
            lambda code, uri: ({"access_token": "tok", "expires_in": 3600, "refresh_token": "ref"}, None),
        )
        monkeypatch.setattr(
            linkedin_social,
            "fetch_member_profile",
            lambda t: {"id": "abc123", "name": "Colin"},
        )
        monkeypatch.setattr(
            linkedin_social,
            "list_admin_orgs",
            lambda token=None: ([], "ACCESS_DENIED"),
        )
        result = linkedin_social.complete_oauth(
            "code", "https://www.pilotcore.fr/admin/social/linkedin/callback"
        )
        assert result["ok"] is True
        assert result["publish_as"] == "member"
        assert content_studio.get_setting(linkedin_social.SETTING_MEMBER_ID) == "abc123"
        assert not (content_studio.get_setting(linkedin_social.SETTING_ORG_ID) or "")
        assert content_studio.get_setting(linkedin_social.SETTING_REFRESH_TOKEN) == "ref"
        assert linkedin_social.is_configured()


def test_publish_post_member_uses_person_urn(app, monkeypatch, tmp_path):
    png = tmp_path / "post.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 40)

    def fake_create(token, payload):
        assert payload["author"] == "urn:li:person:abc123"
        return 201, {}, "urn:li:share:77"

    with app.app_context():
        _wipe_li()
        _wipe_posts()
        content_studio.set_setting(linkedin_social.SETTING_MEMBER_ID, "abc123")
        content_studio.set_setting(linkedin_social.SETTING_MEMBER_NAME, "Colin")
        content_studio.set_setting(linkedin_social.SETTING_ACCESS_TOKEN, "li-token")
        later = datetime.now(timezone.utc) + timedelta(days=20)
        content_studio.set_setting(linkedin_social.SETTING_TOKEN_EXPIRES, later.isoformat())
        monkeypatch.setattr("app.services.social_image.resolve_image_path", lambda p: png)
        monkeypatch.setattr(linkedin_social, "_upload_image", lambda *a, **k: "urn:li:image:abc")
        monkeypatch.setattr(linkedin_social, "_create_post", fake_create)

        post = linkedin_social.publish_post(
            "Essai 14 jours",
            link="https://www.pilotcore.fr/pro?utm_source=linkedin",
            image_path="uploads/social/post.png",
            target_key="pro",
        )
        assert post.status == "published"
        assert post.external_id == "urn:li:share:77"


def test_publish_post_falls_back_to_ugc_when_images_api_unavailable(app, monkeypatch, tmp_path):
    png = tmp_path / "post.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 40)

    def fake_ugc(token, author, message, link, title):
        assert author == "urn:li:person:abc123"
        assert "pilotcore.fr" in (link or "")
        return 201, {"id": "urn:li:share:ugc"}, "urn:li:share:ugc"

    with app.app_context():
        _wipe_li()
        _wipe_posts()
        content_studio.set_setting(linkedin_social.SETTING_MEMBER_ID, "abc123")
        content_studio.set_setting(linkedin_social.SETTING_ACCESS_TOKEN, "li-token")
        later = datetime.now(timezone.utc) + timedelta(days=20)
        content_studio.set_setting(linkedin_social.SETTING_TOKEN_EXPIRES, later.isoformat())
        monkeypatch.setattr("app.services.social_image.resolve_image_path", lambda p: png)
        monkeypatch.setattr(linkedin_social, "_upload_image", lambda *a, **k: None)
        monkeypatch.setattr(linkedin_social, "_create_ugc_share", fake_ugc)

        post = linkedin_social.publish_post(
            "Essai 14 jours",
            link="https://www.pilotcore.fr/pro?utm_source=linkedin",
            image_path="uploads/social/post.png",
            target_key="pro",
        )
        assert post.status == "published"
        assert post.external_id == "urn:li:share:ugc"


def test_linkedin_verification_page(client):
    resp = client.get("/verification-linkedin")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "a7910099-0f14-415c-b7a4-9850c46a4380" in html
    assert "linkedin.com/developers/apps/verification" in html
    home = client.get("/").data.decode()
    assert "verification-linkedin" in home
    alias = client.get("/linkedin-verification", follow_redirects=False)
    assert alias.status_code == 301


def test_tick_not_configured_without_any_network(app):
    with app.app_context():
        _wipe_li()
        for key in (social.SETTING_PAGE_ID, social.SETTING_TOKEN):
            content_studio.set_setting(key, "")
        content_studio.set_setting(social.SETTING_AUTOPOST, "1")
        result = social_autopost.tick()
        assert result["action"] == "not_configured"


def test_tick_linkedin_only_publishes(app, monkeypatch):
    with app.app_context():
        _wipe_posts()
        _wipe_li()
        _connect_linkedin()
        for key in (social.SETTING_PAGE_ID, social.SETTING_TOKEN):
            content_studio.set_setting(key, "")
        content_studio.set_setting(social.SETTING_AUTOPOST, "1")
        content_studio.set_setting(social.SETTING_INTERVAL, "12")
        queued = SocialPost(
            platform="linkedin",
            message="Post dû LI",
            link="https://www.pilotcore.fr/pro?utm_source=facebook",
            image_path="uploads/social/due.png",
            status="queued",
            scheduled_for=datetime.now(timezone.utc) - timedelta(minutes=2),
        )
        db.session.add(queued)
        db.session.commit()
        queued_id = queued.id

        published_row = SocialPost(
            platform="linkedin",
            message="Post dû LI",
            link="https://www.pilotcore.fr/pro?utm_source=linkedin",
            status="published",
            external_id="urn:li:share:1",
            permalink="https://www.linkedin.com/feed/update/urn:li:share:1",
            published_at=datetime.now(timezone.utc),
        )

        def fake_li(*args, **kwargs):
            assert "utm_source=linkedin" in (kwargs.get("link") or "")
            db.session.add(published_row)
            db.session.commit()
            return published_row

        next_preview = SocialPost(
            platform="linkedin",
            message="Prochain",
            status="queued",
            scheduled_for=datetime.now(timezone.utc) + timedelta(hours=12),
        )
        monkeypatch.setattr("app.services.linkedin_social.publish_post", fake_li)
        monkeypatch.setattr(
            "app.services.social_autopost.generate_preview",
            lambda **k: (db.session.add(next_preview) or db.session.commit() or next_preview),
        )
        monkeypatch.setattr("app.services.social_image.ensure_post_visual", lambda *a, **k: queued)
        monkeypatch.setattr("app.services.social_image.materialize_post_image", lambda p: Path("x"))

        result = social_autopost.tick()
        assert result["action"] == "published"
        refreshed = db.session.get(SocialPost, queued_id)
        assert refreshed.status == "published"
        assert refreshed.platform == "linkedin"


def test_publish_queued_fans_out_facebook_and_linkedin(app, monkeypatch):
    with app.app_context():
        _wipe_posts()
        _wipe_li()
        _connect_linkedin()
        content_studio.set_setting(social.SETTING_PAGE_ID, "1246135508572421")
        content_studio.set_setting(social.SETTING_TOKEN, "page-token")
        content_studio.set_setting(social.SETTING_AUTOPOST, "0")
        queued = SocialPost(
            platform="facebook",
            message="Fan-out",
            link="https://www.pilotcore.fr/pro?utm_source=facebook",
            image_path="uploads/social/due.png",
            status="queued",
            scheduled_for=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        db.session.add(queued)
        db.session.commit()

        fb_row = SocialPost(
            platform="facebook",
            message="Fan-out",
            status="published",
            external_id="fb-1",
            permalink="https://www.facebook.com/fb-1",
            published_at=datetime.now(timezone.utc),
        )
        li_row = SocialPost(
            platform="linkedin",
            message="Fan-out",
            status="published",
            external_id="urn:li:share:2",
            permalink="https://www.linkedin.com/feed/update/x",
            published_at=datetime.now(timezone.utc),
        )
        li_links = []

        def fake_fb(*a, **k):
            db.session.add(fb_row)
            db.session.commit()
            return fb_row

        def fake_li(*a, **k):
            li_links.append(k.get("link") or "")
            db.session.add(li_row)
            db.session.commit()
            return li_row

        monkeypatch.setattr("app.services.social.publish_post", fake_fb)
        monkeypatch.setattr("app.services.linkedin_social.publish_post", fake_li)
        monkeypatch.setattr("app.services.social_image.ensure_post_visual", lambda *a, **k: queued)
        monkeypatch.setattr("app.services.social_image.materialize_post_image", lambda p: Path("x"))

        post = social_autopost.publish_queued_now()
        assert post.status == "published"
        assert post.platform == "facebook"
        assert li_links and "utm_source=linkedin" in li_links[0]
        remaining_li = SocialPost.query.filter_by(platform="linkedin", status="published").count()
        assert remaining_li == 1
