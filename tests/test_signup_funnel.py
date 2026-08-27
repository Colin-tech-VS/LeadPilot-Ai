"""The step between « la page s'affiche » and « le compte existe ».

The register page has twice now reported dozens of visits and zero sign-ups,
with nothing in the data to say why. These tests pin down the two measurements
that tell the three possible stories apart: traffic that was never a browser,
forms that were submitted and refused, and a page nobody ever acted on.

Nothing is rolled back between tests in this suite (see ``conftest``), so the
traffic tests each pick their own instant, far enough apart that one test's
rows fall outside the 30-day window of the next.
"""
import uuid
from datetime import datetime, timezone

import pytest

from app.core.extensions import db
from app.models.heatmap_event import TYPE_PAGEVIEW, HeatmapEvent
from app.models.page_view import PageView
from app.services import signup_funnel
from app.services.traffic import acquisition_funnel, conversions


@pytest.fixture
def at_instant(monkeypatch):
    """Freeze traffic's clock at a chosen moment, and hand back the writer that
    stamps rows with it."""

    def _freeze(now):
        monkeypatch.setattr("app.services.traffic._utcnow", lambda: now)

        def _hit(path, visitor, with_js=False):
            db.session.add(
                PageView(
                    visitor_id=visitor,
                    session_id="s" + visitor,
                    path=path,
                    created_at=now,
                )
            )
            if with_js:
                db.session.add(
                    HeatmapEvent(
                        visitor_id=visitor,
                        session_id="s" + visitor,
                        event_type=TYPE_PAGEVIEW,
                        path=path,
                        created_at=now,
                    )
                )

        return _hit

    return _freeze


def _signup(**overrides):
    data = {
        "company_name": "Plomberie Test",
        "email": f"funnel-{uuid.uuid4().hex[:10]}@example.com",
        "city": "Lyon",
        "trade_type": "plombier",
        "password": "MotDePasse123",
    }
    data.update(overrides)
    return data


EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def attempt_log(app):
    """Start from an empty attempt log.

    Tests in this suite share one database and run milliseconds apart, so a
    « everything since a moment ago » window would still catch the previous
    test's submissions. Clearing the rows is the only unambiguous boundary.
    """
    with app.app_context():
        from app.models.event import Event

        Event.query.filter(Event.action == signup_funnel.ACTION).delete()
        db.session.commit()
    return lambda **kw: signup_funnel.summary(EPOCH, **kw)


# ── Traffic that was never a browser ─────────────────────────────────────────


def test_a_visit_with_no_javascript_is_separated_from_a_real_one(app, at_instant):
    """A client that keeps no cookie is a new unique visitor on every hit, so a
    scanner reads as N visitors who all failed to convert. Only the ones whose
    browser ran the page count as somebody who could have signed up."""
    with app.app_context():
        hit = at_instant(datetime(2090, 1, 10, 9, 0, tzinfo=timezone.utc))
        hit("/register", "nojs-real-1", with_js=True)
        for i in range(5):
            hit("/register", f"nojs-scanner-{i}")
        db.session.commit()

        conv = conversions(days=30)
        assert conv["register_visitors"] == 6
        assert conv["register_real_visitors"] == 1
        assert conv["register_no_js_visitors"] == 5
        assert conv["register_no_js_share"] == pytest.approx(83.33, abs=0.01)


def test_the_funnel_shows_the_browser_and_submission_steps(app, at_instant):
    with app.app_context():
        hit = at_instant(datetime(2091, 4, 12, 9, 0, tzinfo=timezone.utc))
        hit("/", "step-home")
        hit("/register", "step-real", with_js=True)
        hit("/register", "step-bot")
        db.session.commit()

        funnel = acquisition_funnel(days=30)
        assert [s["label"] for s in funnel] == [
            "Visiteurs uniques",
            "Page inscription servie",
            "Affichée dans un navigateur",
            "Formulaire envoyé",
            "Inscriptions confirmées",
        ]
        counts = {s["label"]: s["count"] for s in funnel}
        assert counts["Page inscription servie"] == 2
        assert counts["Affichée dans un navigateur"] == 1
        assert counts["Formulaire envoyé"] == 0


def test_the_founding_landing_counts_as_a_register_page(app, at_instant):
    """/50-artisans carries a full sign-up form. Leaving it out of the register
    paths credited its sign-ups to visitors of a page they never opened."""
    with app.app_context():
        hit = at_instant(datetime(2092, 7, 14, 9, 0, tzinfo=timezone.utc))
        hit("/50-artisans", "founding-visitor", with_js=True)
        db.session.commit()

        assert conversions(days=30)["register_visitors"] == 1


# ── Submissions that were refused ────────────────────────────────────────────


def test_a_refused_signup_is_recorded_with_its_reason(client, app, attempt_log):
    client.post("/register", data=_signup(email="pas-un-email"))

    with app.app_context():
        report = attempt_log()
        assert report["attempts"] == 1
        assert report["failed"] == 1
        assert report["by_reason"][0]["reason"] == "invalid_email"


def test_a_successful_signup_is_recorded_too(client, app, attempt_log):
    assert client.post("/register", data=_signup()).status_code == 302

    with app.app_context():
        report = attempt_log()
        assert report["succeeded"] == 1
        assert report["failed"] == 0
        assert report["by_reason"] == []


def test_a_second_signup_on_a_taken_email_names_the_conflict(client, app, attempt_log):
    """The artisan who already has an account must be told so — « e-mail
    invalide » would send them back to the form instead of to the login."""
    data = _signup()
    assert client.post("/register", data=data).status_code == 302

    # A fresh client: the first one is now logged in, and /register would just
    # bounce it to the dashboard without ever reading the form.
    response = app.test_client().post("/register", data=data)
    assert "déjà utilisé" in response.get_data(as_text=True)

    with app.app_context():
        assert [r["reason"] for r in attempt_log()["by_reason"]] == ["email_taken"]


def test_an_attempt_never_stores_the_email_or_the_password(client, app, attempt_log):
    """The log is read in the admin console: it must say what happened without
    carrying the credentials that were posted."""
    client.post("/register", data=_signup(email="prive@example.com", password="MonSecret2099"))

    with app.app_context():
        from app.models.event import Event

        events = Event.query.filter(Event.action == signup_funnel.ACTION).all()
        assert events
        for event in events:
            blob = f"{event.summary} {event.meta}"
            assert "prive@example.com" not in blob
            assert "MonSecret2099" not in blob


def test_a_fiche_match_is_a_note_on_a_success_not_a_failure(client, app, attempt_log):
    """The registry match used to send the artisan back to the form, so it was
    recorded as a refusal. The account is created now and the question waits
    for the dashboard — one event, and « pourquoi les envois ont échoué » stays
    a list of things that actually went wrong."""
    from app.models.registry_listing import STATUS_LISTED, RegistryListing

    with app.app_context():
        db.session.add(
            RegistryListing(
                siren="314159265",
                name="PLOMBERIE CHAVILLOISE",
                city_slug="chaville",
                city="Chaville",
                postal_code="92370",
                dept_code="92",
                trade_key="plombier",
                status=STATUS_LISTED,
            )
        )
        db.session.commit()

    response = client.post(
        "/register",
        data=_signup(company_name="Plomberie Chavilloise", city="Chaville"),
    )
    assert response.status_code == 302

    with app.app_context():
        report = attempt_log()
        assert report["attempts"] == 1  # one submission, one event
        assert report["succeeded"] == 1
        assert report["failed"] == 0
        assert report["by_reason"] == []
        assert report["listing_prompts"] == 1


def test_the_customer_form_records_its_attempts_as_well(client, app, attempt_log):
    client.post(
        "/client/register",
        data={"first_name": "Alex", "email": "", "password": "x", "confirm_password": "x"},
    )

    with app.app_context():
        report = attempt_log(forms=[signup_funnel.FORM_CUSTOMER])
        assert report["failed"] == 1
        assert report["by_reason"][0]["reason"] == "required"


def test_recording_an_attempt_never_breaks_the_signup(client, app, monkeypatch):
    """Instrumentation sits on the critical path of the form. If it ever throws,
    the visitor must still get their account."""
    monkeypatch.setattr(
        "app.services.signup_funnel.log_event",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("log is down")),
    )
    data = _signup()
    assert client.post("/register", data=data).status_code == 302

    with app.app_context():
        from app.models.user import User

        assert User.query.filter_by(email=data["email"]).first() is not None
