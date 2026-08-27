"""« Se connecter avec Google » on the artisan login and sign-up pages.

Google can vouch for an e-mail and a name. It cannot know a company name or a
trade, and an artisan account without a tenant is useless — so sign-in short-
circuits the password, never the business questions.
"""
import uuid
from unittest.mock import patch

import pytest

from app.core.extensions import db
from app.models.tenant import Tenant
from app.models.user import User
from app.services import google_login
from app.services.signup_service import register_plumber


@pytest.fixture
def google_app(app):
    app.config["GOOGLE_OAUTH_CLIENT_ID"] = "test-client-id"
    app.config["GOOGLE_OAUTH_CLIENT_SECRET"] = "test-client-secret"
    app.config["PUBLIC_BASE_URL"] = "https://www.pilotcore.fr"
    return app


def _identity(**overrides):
    data = {
        "sub": "google-" + uuid.uuid4().hex[:12],
        "email": f"g-{uuid.uuid4().hex[:8]}@gmail.com",
        "email_verified": True,
        "first_name": "Jean",
        "last_name": "Dupont",
    }
    data.update(overrides)
    return google_login.GoogleIdentity(**data)


def _start(client):
    """Begin the round trip and return the state Google will echo back."""
    client.get("/auth/google/start")
    with client.session_transaction() as sess:
        return sess.get("google_oauth_state")


def _callback(client, identity, state):
    with patch.object(google_login, "exchange_code", return_value=identity):
        return client.get(f"/auth/google/callback?state={state}&code=whatever")


# ── It stays optional ────────────────────────────────────────────────────────


def test_without_credentials_the_button_is_absent_and_the_routes_404(client):
    html = client.get("/register").get_data(as_text=True)
    assert "btn-google" not in html
    assert client.get("/auth/google/start").status_code == 404
    assert client.get("/auth/google/callback").status_code == 404


def test_with_credentials_the_button_appears_on_login_and_register(client, google_app):
    for path in ("/register", "/login"):
        html = client.get(path).get_data(as_text=True)
        assert "btn-google" in html, path
        assert "/auth/google/start" in html, path


def test_start_redirects_to_google_with_the_configured_callback(client, google_app):
    response = client.get("/auth/google/start")
    assert response.status_code == 302
    location = response.headers["Location"]
    assert location.startswith("https://accounts.google.com/o/oauth2/v2/auth")
    assert "client_id=test-client-id" in location
    assert "www.pilotcore.fr%2Fauth%2Fgoogle%2Fcallback" in location


# ── The round trip is not forgeable ──────────────────────────────────────────


def test_a_callback_with_the_wrong_state_creates_nothing(client, google_app):
    _start(client)
    with patch.object(google_login, "exchange_code") as exchange:
        response = client.get("/auth/google/callback?state=forged&code=x")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
    exchange.assert_not_called()


def test_a_callback_with_no_state_at_all_creates_nothing(client, google_app):
    with patch.object(google_login, "exchange_code") as exchange:
        client.get("/auth/google/callback?state=&code=x")
    exchange.assert_not_called()


def test_a_state_is_single_use(client, google_app):
    state = _start(client)
    identity = _identity()
    _callback(client, identity, state)
    # Replaying the same callback must not walk back into the flow.
    with patch.object(google_login, "exchange_code") as exchange:
        client.get(f"/auth/google/callback?state={state}&code=x")
    exchange.assert_not_called()


# ── New artisan: Google fills the identity, the form still asks the business ──


def test_a_new_google_user_lands_on_register_without_an_account_yet(client, google_app, app):
    identity = _identity()
    response = _callback(client, identity, _start(client))

    assert response.status_code == 302
    assert "/register" in response.headers["Location"]
    with app.app_context():
        assert User.query.filter_by(email=identity.email).first() is None


def test_the_register_form_drops_the_password_once_google_vouched(client, google_app):
    _callback(client, _identity(email="artisan@gmail.com"), _start(client))
    html = client.get("/register").get_data(as_text=True)

    assert "artisan@gmail.com" in html
    assert 'name="password"' not in html
    assert 'name="email"' not in html
    # Company and trade are still asked — Google cannot supply them.
    assert 'name="company_name"' in html
    assert 'name="trade_type"' in html


def test_signing_up_through_google_needs_no_password(client, google_app, app):
    identity = _identity()
    _callback(client, identity, _start(client))

    response = client.post(
        "/register",
        data={"company_name": "Menuiserie Dupont", "city": "Nantes", "trade_type": "menuisier"},
    )
    assert response.status_code == 302

    with app.app_context():
        user = User.query.filter_by(email=identity.email).first()
        assert user is not None
        assert user.google_sub == identity.sub
        assert user.tenant_id is not None
        # No password was ever chosen, so none may work.
        assert not user.check_password("")
        assert not user.check_password("password")


def test_a_posted_email_cannot_override_the_one_google_verified(client, google_app, app):
    """The e-mail field is gone from the form, but the POST is still open to
    anyone. The account must carry Google's address, not the attacker's."""
    identity = _identity()
    _callback(client, identity, _start(client))

    client.post(
        "/register",
        data={
            "company_name": "Menuiserie Dupont",
            "city": "Nantes",
            "trade_type": "menuisier",
            "email": "victime@example.com",
        },
    )

    with app.app_context():
        assert User.query.filter_by(email="victime@example.com").first() is None
        assert User.query.filter_by(email=identity.email).first() is not None


def test_forgetting_the_identity_restores_the_normal_form(client, google_app):
    _callback(client, _identity(), _start(client))
    client.get("/auth/google/forget")

    html = client.get("/register").get_data(as_text=True)
    assert 'name="password"' in html
    assert 'name="email"' in html


# ── Returning artisan ────────────────────────────────────────────────────────


def test_a_known_google_account_signs_straight_in(client, google_app, app):
    identity = _identity()
    _callback(client, identity, _start(client))
    client.post("/register", data={"company_name": "Elec Dupont", "trade_type": "electricien"})
    client.get("/logout")

    response = _callback(client, identity, _start(client))
    assert response.status_code == 302
    assert "/dashboard" in response.headers["Location"]


def test_a_verified_email_adopts_an_existing_password_account(client, google_app, app):
    """The artisan signed up with a password months ago and now presses the
    Google button. Same person — link the two, do not make a second account."""
    email = f"legacy-{uuid.uuid4().hex[:8]}@gmail.com"
    with app.app_context():
        register_plumber(email=email, password="MotDePasse123", company_name="Plomberie Legacy")

    identity = _identity(email=email)
    response = _callback(client, identity, _start(client))

    assert "/dashboard" in response.headers["Location"]
    with app.app_context():
        assert User.query.filter_by(email=email).count() == 1
        assert User.query.filter_by(email=email).one().google_sub == identity.sub


def test_an_unverified_google_email_never_takes_over_an_account(client, google_app, app):
    """Anyone can put any address on a fresh Google profile. Without the
    verified flag, matching by e-mail would hand over someone else's account."""
    email = f"target-{uuid.uuid4().hex[:8]}@example.com"
    with app.app_context():
        register_plumber(email=email, password="MotDePasse123", company_name="Plomberie Cible")

    response = _callback(client, _identity(email=email, email_verified=False), _start(client))

    assert "/register" in response.headers["Location"]
    with app.app_context():
        assert User.query.filter_by(email=email).one().google_sub is None


def test_a_customer_account_is_not_let_into_the_artisan_dashboard(client, google_app, app):
    email = f"client-{uuid.uuid4().hex[:8]}@gmail.com"
    with app.app_context():
        user = User(email=email, role="customer", first_name="Marie")
        user.set_password("MotDePasse123")
        db.session.add(user)
        db.session.commit()

    response = _callback(client, _identity(email=email), _start(client))
    assert "/dashboard" not in response.headers["Location"]

    with client.session_transaction() as sess:
        assert sess.get("tenant_id") is None


# ── Failures degrade quietly ─────────────────────────────────────────────────


def test_a_google_outage_sends_the_artisan_back_to_the_password_form(client, google_app):
    state = _start(client)
    with patch.object(
        google_login, "exchange_code", side_effect=google_login.GoogleLoginError("boom")
    ):
        response = client.get(f"/auth/google/callback?state={state}&code=x")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
    assert "Google" in client.get("/login").get_data(as_text=True)


def test_pressing_cancel_on_googles_screen_is_not_an_error(client, google_app):
    state = _start(client)
    response = client.get(f"/auth/google/callback?state={state}&error=access_denied")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_an_identity_without_an_email_is_refused(google_app):
    with google_app.app_context():
        with patch("app.services.google_login.requests.get") as get:
            get.return_value.status_code = 200
            get.return_value.json.return_value = {"id": "123"}
            with pytest.raises(google_login.GoogleLoginError):
                google_login._fetch_identity("token")
