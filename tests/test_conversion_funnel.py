"""Paid traffic, the homepage fork, and the sign-up first step.

14-day funnel on production: 205 paid (Facebook) visitors, 0 % reached
``/register``. 60 people did open the form; none created an account. The ads
were landing on the consumer homepage, the form pre-selected Plombier so the
chip tap that auto-advances never fired, and a plumber had to press Continuer.
These tests pin the doors we opened so they cannot close silently.
"""
from pathlib import Path

from app.models.user import User
from app.utils.i18n import TRANSLATIONS

STATIC = Path(__file__).resolve().parent.parent / "static"


def _signup(**overrides):
    import uuid

    data = {
        "company_name": "Menuiserie Funnel",
        "email": f"funnel-{uuid.uuid4().hex[:10]}@example.com",
        "city": "Nantes",
        "trade_type": "menuisier",
        "password": "MotDePasse123",
    }
    data.update(overrides)
    return data


def test_a_plain_homepage_stays_the_customer_search(client):
    response = client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Trouvez un artisan près de chez vous" in html
    assert "Vous êtes artisan ? Tester gratuitement" in html
    assert "/register?src=accueil-hero" in html
    assert "/register?src=accueil-fork" in html
    assert 'class="pro-hook"' in html


def test_facebook_ads_on_the_homepage_land_on_pro(client):
    response = client.get("/?utm_source=facebook&utm_medium=paid&utm_campaign=pro")
    assert response.status_code == 302
    location = response.headers["Location"]
    assert "/pro" in location
    assert "utm_source=facebook" in location
    assert "utm_campaign=pro" in location


def test_a_facebook_click_id_alone_sends_them_to_pro(client):
    response = client.get("/?fbclid=IwAR0test")
    assert response.status_code == 302
    assert "/pro" in response.headers["Location"]
    assert "fbclid=IwAR0test" in response.headers["Location"]


def test_a_facebook_referrer_on_the_homepage_goes_to_pro(client):
    response = client.get("/", headers={"Referer": "https://m.facebook.com/"})
    assert response.status_code == 302
    assert "/pro" in response.headers["Location"]


def test_a_customer_search_ad_is_not_hijacked(client):
    response = client.get("/?utm_source=facebook&utm_medium=paid&q=fuite")
    assert response.status_code == 200
    assert "Trouvez un artisan près de chez vous" in response.get_data(as_text=True)


def test_audience_client_keeps_the_homepage(client):
    response = client.get("/?utm_source=facebook&audience=client")
    assert response.status_code == 200


def test_google_organic_on_the_homepage_is_not_redirected(client):
    response = client.get("/", headers={"Referer": "https://www.google.com/"})
    assert response.status_code == 200


def test_the_trade_is_not_pre_selected(client):
    """A pre-selected Plombier skipped the chip tap that advances the wizard,
    so plumbers (and anyone who left the default) had to press Continuer."""
    html = client.get("/register").get_data(as_text=True)
    assert "Choisir un métier" in html
    assert 'option value="plombier" data-icon="🔧" selected>' not in html
    assert "trade-picker-chip is-active" not in html


def test_signup_without_a_trade_is_rejected(client, app):
    data = _signup(trade_type="")
    response = client.post("/register", data=data)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'data-start-step="0"' in html
    with app.app_context():
        assert User.query.filter_by(email=data["email"]).first() is None


def test_picking_a_trade_from_the_dropdown_advances_the_wizard():
    js = (STATIC / "js" / "auth.js").read_text(encoding="utf-8")
    assert "advanceFromTrade" in js
    assert "data-trade-select" in js


def test_the_pro_landing_keeps_a_mobile_sticky_cta(client):
    html = client.get("/pro").get_data(as_text=True)
    assert 'class="pro-sticky-bar"' in html
    assert "Tester gratuitement" in html
    css = (STATIC / "css" / "pro.css").read_text(encoding="utf-8")
    assert "body.page-landing:has(#cookie-banner.is-visible) .pro-sticky-bar" in css


def test_new_conversion_keys_exist_in_fr_and_en():
    for key in (
        "home.hero_pro_link",
        "register.trade_placeholder",
        "home.fork_pro_cta",
    ):
        assert key in TRANSLATIONS["fr"], key
        assert key in TRANSLATIONS["en"], key
        assert TRANSLATIONS["fr"][key]
        assert TRANSLATIONS["en"][key]
