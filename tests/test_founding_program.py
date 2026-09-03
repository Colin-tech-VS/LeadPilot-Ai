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


def test_founding_landing_sells_the_longer_trial_without_an_empty_counter(client):
    """« 0 / 50 artisans inscrits » above the fold is not scarcity — it is a
    public statement that nobody signed up. The counter appears only once there
    are enough members for it to argue in our favour."""
    html = client.get("/50-artisans").data.decode()
    assert "50 premiers artisans" in html
    assert "essai 30 jours" in html.lower() or "30 jours" in html
    assert "Rejoindre les 50 premiers artisans" in html or "est complet" in html
    assert "30 jours" in html
    assert "0 / 50 artisans inscrits" not in html
    assert "/ 50 artisans inscrits" not in html
    assert "des milliers" not in html.lower()


def test_founding_city_field_uses_places_autocomplete(client):
    html = client.get("/50-artisans").data.decode()
    assert "address-autocomplete.js" in html
    assert "data-places-city" in html
    assert "maps.googleapis.com/maps/api" not in html


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
        tenant = user.tenant
        assert tenant.plan == "trial"
        assert tenant.is_paid is False
        remaining = (tenant.trial_end_date - utcnow()).days
        assert 29 <= remaining <= 30
        assert (row.ends_at - row.started_at).days == founding_program.STARTER_GIFT_DAYS
        from app.services import plan_features as pf
        from app.services.twilio_provisioning import should_buy_dedicated_number

        # A founding seat buys length and hands-on setup, not a smaller product:
        # it used to silently withhold the automatic booking the landing page
        # led with, which made the « better » offer the worse one.
        assert pf.trial_has_all_features(tenant) is True
        assert pf.has_feature(tenant, "auto_booking") is True
        assert pf.call_quota(tenant) is None
        # The line is bought when the artisan asks for it, not at signup.
        assert should_buy_dedicated_number(tenant) is False


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


def test_founding_tick_extends_short_gift_to_one_month(app):
    with app.app_context():
        from app.services.signup_service import register_plumber

        user, tenant = register_plumber(
            email=_mail("short"),
            password="password1",
            company_name="Court SARL",
            phone=f"07{uuid.uuid4().int % 10**8:08d}",
            city="Lille",
            first_name="Lea",
            last_name="Court",
            send_welcome=False,
        )
        started = utcnow() - timedelta(days=2)
        row = FoundingParticipant(
            place_number=(db.session.query(db.func.max(FoundingParticipant.place_number)).scalar() or 0) + 1,
            tenant_id=tenant.id,
            user_id=user.id,
            status="active",
            referral_code=uuid.uuid4().hex[:8].upper(),
            started_at=started,
            ends_at=started + timedelta(days=14),
        )
        tenant.trial_ends_at = started + timedelta(days=14)
        db.session.add(row)
        db.session.commit()
        founding_program.tick()
        db.session.refresh(row)
        db.session.refresh(tenant)
        assert (row.ends_at.replace(tzinfo=None) - row.started_at.replace(tzinfo=None)).days >= 29
        assert (tenant.trial_end_date - utcnow()).days >= 27
        assert row.status == "active"


def test_founding_admin_kpis_start_empty(client, app):
    with client.session_transaction() as sess:
        sess["admin_authenticated"] = True
        sess["admin_username"] = "admin"
    resp = client.get("/admin/promo")
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "50 artisans" in html
    assert "Promo" in html
    assert "1 mois de Starter offert" in html
    assert "Durée du Starter offert" in html
    assert 'class="nvx promo-page"' in html
    assert "promo-stats" in html
    assert "Registre" in html
    assert "Inscriptions ouvertes" in html
    assert "En attente" in html
    assert "À risque" in html
    assert "Toutes les sources" in html


def test_founding_admin_registre_shows_participant(client, app):
    with app.app_context():
        founding_program.enroll(
            email=_mail("promo"),
            password="password1",
            first_name="Marie",
            last_name="Martin",
            phone=f"0612{uuid.uuid4().int % 10**6:06d}",
            city="Cahors",
            trade_type="plombier",
            company_name="Plomberie Martin",
            source="direct",
        )
    with client.session_transaction() as sess:
        sess["admin_authenticated"] = True
        sess["admin_username"] = "admin"
    html = client.get("/admin/promo").data.decode()
    assert "Plomberie Martin" in html
    assert "Marie Martin" in html
    assert "Cahors" in html
    assert "promo-table" in html
    assert "Direct" in html
    assert "promo-status-select" in html
    assert "Compte" in html
    assert "Rappel" in html


def test_pro_homepage_states_the_launch_terms_without_a_second_offer(client):
    """The launch programme is what the single trial is worth right now — not a
    rival page with a rival free trial. Two « gratuit » buttons pointing at two
    different signups made the visitor choose between them instead of choosing
    us, so /pro carries one destination."""
    html = client.get("/pro").data.decode()
    assert 'class="founding-spotlight"' in html
    assert "30 jours" in html
    assert 'href="/register"' in html
    assert "/ 50 artisans inscrits" not in html
    assert "places restantes" not in html
    assert html.count('href="/50-artisans"') <= 1  # the footer link only
    assert "des milliers" not in html.lower()
    assert "clients garantis" not in html.lower()


def test_sitemap_includes_founding_page(client):
    body = client.get("/sitemap-core.xml").data.decode()
    assert "/50-artisans</loc>" in body
