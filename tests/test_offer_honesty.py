"""Advertised offers must match what the product actually does."""
import re
import uuid
from datetime import timedelta

from app.core.extensions import db
from app.models.tenant import TRIAL_DAYS, utcnow
from app.services import billing, content_studio, plan_features as pf
from app.services.signup_service import register_plumber
from app.services.twilio_provisioning import should_buy_dedicated_number
from app.utils.i18n import TRANSLATIONS


def _euros(text: str) -> int:
    digits = re.sub(r"[^\d]", "", text or "")
    return int(digits) if digits else 0


def test_published_prices_match_stripe_plans():
    fr = TRANSLATIONS["fr"]
    for key, plan in billing.PLANS.items():
        assert _euros(fr[f"landing.pricing_{key}_price"]) * 100 == plan["amount"]
        calls = fr[f"landing.pricing_{key}_calls"]
        assert str(plan["included_calls"]) in calls.replace(" ", "") or str(
            plan["included_calls"]
        ) in calls


def test_trial_length_is_fourteen_days():
    assert TRIAL_DAYS == 14
    assert "14 jours" in TRANSLATIONS["fr"]["landing.pricing_trial_period"]
    assert "14 jours" in TRANSLATIONS["fr"]["register.form_sub"]


def test_founding_copy_is_thirty_day_starter():
    fr = TRANSLATIONS["fr"]
    assert "30 jours" in fr["founding.form_sub"]
    assert "Starter" in fr["founding.duration_hint"]
    assert "14 jours" not in fr["founding.why_1"]


def test_i18n_does_not_advertise_unshipped_capabilities():
    banned = (
        "google agenda",
        "google calendar",
        "plusieurs utilisateurs",
        "multiple users",
        "plusieurs numéros",
        "several reception numbers",
        "multiple phone numbers",
        "numéro ia dédié pour vos clients",
        "dedicated ai phone line",
        "équipe de 2 à 10",
        "team of 2 to 10",
        "prénom, consignes",
        "name, instructions",
    )
    for lang, table in TRANSLATIONS.items():
        blob = " ".join(table.values()).lower()
        for phrase in banned:
            assert phrase not in blob, f"{lang}: still advertises {phrase!r}"


def test_honest_rewrite_covers_stale_admin_offers():
    class _Offer:
        key = "pro"

        def feature_list(self):
            return [
                "Synchronisation Google Agenda",
                "Plusieurs utilisateurs (jusqu'à 10)",
                "Plusieurs numéros de réception",
                "Personnalisation de l'assistant (prénom, consignes)",
            ]

    lines = " | ".join(content_studio.honest_offer_features(_Offer()))
    assert "Google Agenda" not in lines
    assert "Plusieurs utilisateurs" not in lines
    assert "Plusieurs numéros" not in lines
    assert "consignes" not in lines
    assert "Réservation en ligne" in lines


def test_classic_register_is_full_trial_without_twilio_number(app):
    with app.app_context():
        _user, tenant = register_plumber(
            email=f"trial-{uuid.uuid4().hex[:8]}@example.com",
            password="password1",
            company_name="Essai Complet",
            city="Toulouse",
        )
        remaining = (tenant.trial_end_date - utcnow()).days
        assert 13 <= remaining <= TRIAL_DAYS
        assert tenant.plan == "trial"
        assert tenant.is_paid is False
        assert pf.trial_has_all_features(tenant) is True
        assert pf.has_feature(tenant, "auto_booking") is True
        assert pf.has_feature(tenant, "crm_marketing") is True
        assert pf.call_quota(tenant) is None
        assert not tenant.ai_phone_number
        assert should_buy_dedicated_number(tenant) is False


def test_paid_plans_match_advertised_feature_split(app):
    with app.app_context():
        _user, starter = register_plumber(
            email=f"st-{uuid.uuid4().hex[:8]}@example.com",
            password="password1",
            company_name="Starter Co",
            city="Nantes",
        )
        starter.plan = "starter"
        starter.trial_ends_at = utcnow() - timedelta(days=1)

        _user, pro = register_plumber(
            email=f"pro-{uuid.uuid4().hex[:8]}@example.com",
            password="password1",
            company_name="Pro Co",
            city="Lille",
        )
        pro.plan = "pro"

        _user, premium = register_plumber(
            email=f"pre-{uuid.uuid4().hex[:8]}@example.com",
            password="password1",
            company_name="Premium Co",
            city="Bordeaux",
        )
        premium.plan = "premium"

        assert pf.has_feature(starter, "auto_booking") is False
        assert pf.has_feature(starter, "sms_email_notifications") is False
        assert pf.call_quota(starter) == 150
        out = pf.apply_booking_plan_limits(starter, {"action": "BOOK_NOW"})
        assert out["action"] == "CALL_BACK"

        assert pf.has_feature(pro, "auto_booking") is True
        assert pf.has_feature(pro, "sms_email_notifications") is True
        assert pf.has_feature(pro, "crm_marketing") is False
        assert pf.call_quota(pro) == 500

        assert pf.has_feature(premium, "crm_marketing") is True
        assert pf.has_feature(premium, "ai_customization") is True
        assert pf.call_quota(premium) == 1500


def test_public_pages_describe_offers_honestly(client):
    pro = client.get("/pro").get_data(as_text=True)
    assert "149" in pro and "349" in pro and "699" in pro
    assert "150 appels" in pro
    assert "500 appels" in pro
    assert "1 500" in pro or "1500" in pro
    assert "14 jours" in pro
    assert "Google Agenda" not in pro
    assert "Plusieurs utilisateurs" not in pro
    assert "Plusieurs numéros" not in pro
    assert "Sans prise de rendez-vous automatique" in pro

    register = client.get("/register").get_data(as_text=True)
    assert "Numéro IA dédié pour vos clients" not in register
    assert "14 jours" in register
    assert "sans carte" in register.lower() or "Sans carte" in register

    founding = client.get("/50-artisans").get_data(as_text=True)
    assert "Starter" in founding
    assert "30 jours" in founding
    assert "sans carte" in founding.lower() or "Sans carte" in founding


def test_starter_listing_does_not_offer_online_booking(client, app):
    with app.app_context():
        _user, tenant = register_plumber(
            email=f"list-{uuid.uuid4().hex[:8]}@example.com",
            password="password1",
            company_name="Listing Starter",
            city="Rennes",
        )
        tenant.plan = "starter"
        tenant.trial_ends_at = utcnow() - timedelta(days=1)
        db.session.commit()
        slug = tenant.public_slug
    html = client.get(f"/artisans/{slug}").get_data(as_text=True)
    assert "Réservation en ligne non activée" in html


def test_trial_listing_keeps_online_booking_copy(client, app):
    with app.app_context():
        _user, tenant = register_plumber(
            email=f"list-t-{uuid.uuid4().hex[:8]}@example.com",
            password="password1",
            company_name="Listing Trial",
            city="Dijon",
        )
        slug = tenant.public_slug
    html = client.get(f"/artisans/{slug}").get_data(as_text=True)
    assert "Réservation en ligne non activée" not in html


def test_founding_listing_has_no_auto_booking(client, app):
    email = f"fnd-{uuid.uuid4().hex[:8]}@example.com"
    resp = client.post(
        "/50-artisans",
        data={
            "first_name": "Marie",
            "last_name": "Martin",
            "email": email,
            "phone": f"0611{uuid.uuid4().int % 10**6:06d}",
            "city": "Cahors",
            "trade_type": "plombier",
            "company_name": "Plomberie Honesty",
            "password": "password1",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    with app.app_context():
        from app.models.user import User

        user = User.query.filter_by(email=email).first()
        tenant = user.tenant
        assert pf.founding_starter_gift_active(tenant) is True
        assert pf.has_feature(tenant, "auto_booking") is False
        slug = tenant.public_slug
    html = client.get(f"/artisans/{slug}").get_data(as_text=True)
    assert "Réservation en ligne non activée" in html
