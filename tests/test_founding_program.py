"""First-50 artisans programme: landing, seats, waitlist, admin."""
import uuid
from datetime import timedelta

from app.core.extensions import db
from app.models.founding import FoundingParticipant, FoundingWaitlist
from app.models.user import User
from app.models.tenant import utcnow
from app.services import content_studio, founding_program


def _mail(prefix="art"):
    return f"{prefix}-{uuid.uuid4().hex[:10]}@example.com"


def _signup(client, **overrides):
    data = {
        "first_name": "Marie",
        "last_name": "Martin",
        "email": _mail("marie"),
        "phone": f"0611{uuid.uuid4().int % 10**6:06d}",
        "city": "Cahors",
        "trade_type": "plombier",
        "company_name": "Plomberie Martin",
        "password": "password1",
    }
    data.update(overrides)
    return client.post("/50-artisans", data=data, follow_redirects=False)


def test_founding_landing_seo_and_counter(client):
    html = client.get("/50-artisans").data.decode()
    assert "Programme des 50 premiers artisans" in html
    assert "/ 50 artisans inscrits" in html
    assert "Rejoindre les 50 premiers artisans" in html or "est complet" in html
    assert "des milliers" not in html.lower()


def test_founding_signup_creates_real_artisan_account(client, app):
    email = _mail("marie")
    resp = _signup(client, email=email, phone="0611223344")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/dashboard")
    with app.app_context():
        user = User.query.filter_by(email=email).first()
        assert user is not None
        assert user.tenant_id is not None
        row = FoundingParticipant.query.filter_by(user_id=user.id).first()
        assert row is not None
        assert row.place_number >= 1
        assert row.status == "active"


def test_founding_duplicate_email_rejected(client, app):
    email = _mail("dup")
    assert _signup(client, email=email, phone="0611000001").status_code == 302
    resp = _signup(client, email=email, phone="0611000002")
    assert resp.status_code == 200
    assert "déjà un compte" in resp.data.decode()


def test_founding_closes_at_max_and_waitlist(client, app):
    with app.app_context():
        before = founding_program.occupied_count()
        content_studio.set_setting(founding_program.SETTING_MAX, str(before + 1))
    try:
        assert _signup(client, email=_mail("last"), phone="0611999001").status_code == 302
        html = client.get("/50-artisans").data.decode()
        assert "est complet" in html
        wait_email = _mail("wait")
        wait = client.post(
            "/50-artisans",
            data={
                "waitlist": "1",
                "first_name": "Paul",
                "last_name": "Durand",
                "email": wait_email,
                "city": "Nantes",
                "trade_type": "serrurier",
            },
            follow_redirects=False,
        )
        assert wait.status_code == 302
        with app.app_context():
            assert FoundingWaitlist.query.filter_by(email=wait_email).first()
            assert User.query.filter_by(email=wait_email).first() is None
    finally:
        with app.app_context():
            content_studio.set_setting(founding_program.SETTING_MAX, "50")


def test_founding_tick_expires_and_skips_converted(app):
    with app.app_context():
        from app.services.signup_service import register_plumber

        user, tenant = register_plumber(
            email=_mail("exp"),
            password="password1",
            company_name="Exp SARL",
            phone=f"07{uuid.uuid4().int % 10**8:08d}",
            city="Lille",
            first_name="Eva",
            last_name="Expire",
            send_welcome=False,
        )
        row = FoundingParticipant(
            place_number=(db.session.query(db.func.max(FoundingParticipant.place_number)).scalar() or 0) + 1,
            tenant_id=tenant.id,
            user_id=user.id,
            status="active",
            referral_code=uuid.uuid4().hex[:8].upper(),
            started_at=utcnow() - timedelta(days=20),
            ends_at=utcnow() - timedelta(days=1),
        )
        db.session.add(row)
        db.session.commit()
        founding_program.tick()
        db.session.refresh(row)
        assert row.status == "expired"

        tenant.plan = "starter"
        db.session.commit()
        founding_program.mark_converted(tenant.id)
        db.session.refresh(row)
        assert row.status == "converted"


def test_founding_admin_kpis_start_empty(client, app):
    with client.session_transaction() as sess:
        sess["admin_authenticated"] = True
        sess["admin_username"] = "admin"
    resp = client.get("/admin/promo")
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "50 artisans" in html
    assert "Promo" in html


def test_pro_homepage_features_founding_programme(client):
    html = client.get("/pro").data.decode()
    assert 'class="founding-spotlight"' in html
    assert "/50-artisans" in html
    assert "Les 50 premiers" in html
    assert "Rejoindre les 50" in html
    assert "/ 50 artisans inscrits" in html
    assert "Commencer gratuitement" in html
    assert 'href="/register"' in html or "/register" in html
    assert "des milliers" not in html.lower()
    assert "clients garantis" not in html.lower()


def test_sitemap_includes_founding_page(client):
    body = client.get("/sitemap-core.xml").data.decode()
    assert "/50-artisans</loc>" in body
