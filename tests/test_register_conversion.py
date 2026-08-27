"""The artisan sign-up page must stay short — and its CTA must stay tappable.

The page once took 26 visits without a single sign-up. Two things were wrong:
the cookie banner grew to half the viewport on a phone and swallowed the taps
meant for the form's submit button, and the form itself asked for ten fields
across three steps before anyone could create an account. These tests pin down
both fixes so neither can silently come back.
"""
import re
import uuid
from pathlib import Path

from app.models.user import User

STATIC = Path(__file__).resolve().parent.parent / "static"


def _register_html(client):
    return client.get("/register").get_data(as_text=True)


def _signup(**overrides):
    data = {
        "company_name": "Menuiserie Test",
        "email": f"conv-{uuid.uuid4().hex[:10]}@example.com",
        "city": "Nantes",
        "trade_type": "menuisier",
        "password": "MotDePasse123",
    }
    data.update(overrides)
    return data


# ── The form itself ──────────────────────────────────────────────────────────


def test_signup_needs_only_company_email_and_password(client, app):
    """No confirm-password, no first/last name — the four visible fields do it."""
    data = _signup()
    response = client.post("/register", data=data)
    assert response.status_code == 302

    with app.app_context():
        assert User.query.filter_by(email=data["email"]).first() is not None


def test_the_form_is_two_steps_not_three(client):
    html = _register_html(client)
    assert html.count("data-register-step") == 2


def test_the_form_no_longer_asks_for_a_password_twice(client):
    html = _register_html(client)
    assert 'name="confirm_password"' not in html
    assert 'name="first_name"' not in html
    assert 'name="last_name"' not in html


def test_phone_and_siret_are_folded_out_of_the_critical_path(client):
    """Both stay postable — they just no longer sit between the visitor and the
    submit button. Anything inside the disclosure must be optional, or a closed
    <details> would hide a required field and block submission entirely."""
    html = _register_html(client)
    assert "<details" in html

    body = html.split("<details", 1)[1].split("</details>", 1)[0]
    assert 'name="phone"' in body
    assert 'name="siret"' in body
    for field in re.findall(r"<input[^>]*>", body):
        assert "required" not in field, field


def test_a_confirm_password_posted_by_another_client_is_still_checked(client, app):
    data = _signup(confirm_password="UnAutreMotDePasse1")
    response = client.post("/register", data=data)
    assert response.status_code == 200
    assert "ne correspondent pas" in response.get_data(as_text=True)

    with app.app_context():
        assert User.query.filter_by(email=data["email"]).first() is None


def test_an_error_sends_the_visitor_back_to_the_step_that_holds_the_fields(client):
    """Step indices moved from 3 steps to 2; a stale index 2 would leave the
    wizard on a step that no longer exists and show an empty card."""
    response = client.post("/register", data=_signup(email="pas-un-email"))
    html = response.get_data(as_text=True)
    assert 'data-start-step="1"' in html


# ── The overlays that sat on top of it ───────────────────────────────────────


def test_the_stacked_cookie_banner_does_not_inherit_a_320px_flex_basis():
    """`.cookie-banner-text { flex: 1 1 320px }` is an inline-axis size while the
    banner is a row. In the mobile column layout that basis becomes a HEIGHT:
    the banner ballooned from 188px to 424px on a 390x844 phone and covered the
    sign-up CTA, so taps landed on the banner instead of the button."""
    for name in ("public.css", "pro.css"):
        css = (STATIC / "css" / name).read_text(encoding="utf-8")
        stacking = [
            m for m in re.finditer(r"[^{}]*cookie-banner-inner[^{}]*\{[^}]*\}", css)
            if "column" in m.group(0)
        ]
        assert stacking, f"{name}: no rule stacks the banner any more"

        resets = [
            m for m in re.finditer(r"[^{}]*cookie-banner-text[^{}]*\{[^}]*\}", css)
            if "flex: 1 1 auto" in m.group(0)
        ]
        # The reset must live with (or after) the rule that flips the direction,
        # so it wins the cascade over the base `flex: 1 1 320px`.
        for block in stacking:
            assert any(r.start() > block.start() for r in resets), (
                f"{name}: the banner stacks at char {block.start()} with no "
                "`.cookie-banner-text { flex: 1 1 auto }` reset after it"
            )


def test_the_banner_lifts_a_marked_cta_clear_of_itself():
    js = (STATIC / "js" / "cookie-consent.js").read_text(encoding="utf-8")
    assert "data-consent-keep-visible" in js
    # The banner slides in with a transform, so its rect is still off-screen on
    # the frame it becomes visible — the overlap has to come from offsetHeight.
    assert "banner.offsetHeight" in js


def test_the_signup_cta_is_marked_so_the_banner_moves_out_of_its_way(client):
    html = _register_html(client)
    actions = html.split('class="auth-pro__actions"', 1)
    assert len(actions) == 2
    assert "data-consent-keep-visible" in actions[1].split(">", 1)[0]


def test_the_install_prompt_stays_off_the_auth_pages():
    """A second fixed layer over the same CTA. An artisan without an account has
    no reason to install the app; the prompt is shown again on the dashboard."""
    js = (STATIC / "js" / "pwa-install.js").read_text(encoding="utf-8")
    assert 'document.querySelector(".auth-pro")' in js
