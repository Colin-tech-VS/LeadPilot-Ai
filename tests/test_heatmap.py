"""Tests for the visitor-journey / click heatmap tracking and admin views."""
import json

import pytest

from app.core.admin_auth import ADMIN_SESSION_KEY, ADMIN_USER_KEY
from app.core.extensions import db
from app.models.heatmap_event import HeatmapEvent
from app.models.session_recording import SessionRecording
from app.services import heatmap as heatmap_service


@pytest.fixture(autouse=True)
def _clean_events(app):
    """The test DB is a shared file — start each test with an empty table."""
    with app.app_context():
        HeatmapEvent.query.delete()
        SessionRecording.query.delete()
        db.session.commit()
    yield

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120 Safari/537.36"
)
BOT_UA = "python-requests/2.31"


def _login_admin(client):
    with client.session_transaction() as s:
        s[ADMIN_SESSION_KEY] = True
        s[ADMIN_USER_KEY] = "tester"


def _collect(client, events):
    return client.post(
        "/api/heatmap/collect",
        data=json.dumps({"events": events}),
        headers={"User-Agent": UA, "Content-Type": "application/json"},
    )


def test_tracker_injected_on_public_page_not_admin(client):
    body = client.get("/", headers={"User-Agent": UA}).get_data(as_text=True)
    assert "heatmap.js" in body


def test_collect_requires_visitor_cookie(client, app):
    # No prior page view → no lp_vid cookie → nothing stored, still 204.
    r = _collect(client, [{"t": "click", "p": "/", "x": 0.5, "y": 10}])
    assert r.status_code == 204
    with app.app_context():
        assert HeatmapEvent.query.count() == 0


def test_collect_stores_events_with_server_side_ids(client, app):
    client.get("/", headers={"User-Agent": UA})  # sets lp_vid / lp_sid
    r = _collect(
        client,
        [
            {"t": "pageview", "p": "/", "vw": 1440, "vh": 900, "dw": 1440, "dh": 2400},
            {"t": "click", "p": "/", "x": 0.5, "y": 600, "dw": 1440, "dh": 2400,
             "s": "a.cta", "txt": "Devis"},
            {"t": "rageclick", "p": "/", "x": 0.5, "y": 610, "s": "button#go"},
        ],
    )
    assert r.status_code == 204
    with app.app_context():
        assert HeatmapEvent.query.count() == 3
        click = HeatmapEvent.query.filter_by(event_type="click").first()
        assert click.visitor_id  # attached from cookie, not client
        assert click.el_selector == "a.cta"
        assert 0.0 <= click.x_ratio <= 1.0


def test_bot_events_dropped(client, app):
    client.get("/", headers={"User-Agent": UA})
    r = client.post(
        "/api/heatmap/collect",
        data=json.dumps({"events": [{"t": "click", "p": "/", "x": 0.1, "y": 1}]}),
        headers={"User-Agent": BOT_UA, "Content-Type": "application/json"},
    )
    assert r.status_code == 204
    with app.app_context():
        assert HeatmapEvent.query.count() == 0


def test_single_visitor_journey_spans_sessions(client, app):
    client.get("/", headers={"User-Agent": UA})
    _collect(client, [{"t": "click", "p": "/", "x": 0.3, "y": 100, "s": "a.one"}])
    # New session for the SAME visitor: drop only the session cookie.
    client.delete_cookie("lp_sid")
    client.get("/contact", headers={"User-Agent": UA})
    _collect(client, [{"t": "click", "p": "/contact", "x": 0.6, "y": 200, "s": "a.two"}])

    with app.app_context():
        rows = heatmap_service.journeys(days=30)
        assert len(rows) == 1  # one unique visitor, not one per session
        assert rows[0]["sessions"] == 2
        detail = heatmap_service.journey_detail(rows[0]["visitor_id"])
        assert {e["session_no"] for e in detail["timeline"]} == {1, 2}
        assert detail["pages"] == ["/", "/contact"]


def test_admin_heatmap_apis(client, app):
    client.get("/", headers={"User-Agent": UA})
    _collect(
        client,
        [
            {"t": "click", "p": "/", "x": 0.5, "y": 300, "dw": 1440, "dh": 2000,
             "s": "a.cta", "txt": "Devis"},
            {"t": "click", "p": "/", "x": 0.5, "y": 320, "dw": 1440, "dh": 2000,
             "s": "a.cta", "txt": "Devis"},
        ],
    )
    _login_admin(client)

    assert client.get("/admin/heatmap").status_code == 200
    ov = client.get("/admin/api/heatmap/overview").get_json()
    assert ov["clicks"] == 2
    assert ov["tracked_visitors"] == 1
    assert any(p["path"] == "/" for p in ov["pages"])

    pts = client.get("/admin/api/heatmap/points?path=/").get_json()
    assert pts["count"] == 2
    assert pts["doc_w"] == 1440
    assert any(e["selector"] == "a.cta" and e["clicks"] == 2 for e in pts["elements"])


# ── Session recordings (cursor replay) ──────────────────────────────────────
def _record(client, payload, ua=UA):
    return client.post(
        "/api/heatmap/record",
        data=json.dumps(payload),
        headers={"User-Agent": ua, "Content-Type": "application/json"},
    )


def _sample_track():
    return {
        "rec_id": "rec-test-0001",
        "p": "/",
        "vw": 1280, "vh": 800, "dw": 1280, "dh": 2600, "dur": 4200,
        "track": {
            "m": [[0, 0.1, 40], [500, 0.3, 200], [1200, 0.5, 900], [2500, 0.7, 1700]],
            "c": [[1300, 0.5, 910]],
            "s": [[600, 0], [1300, 800], [2600, 1600]],
        },
    }


def test_record_requires_visitor_cookie(client, app):
    r = _record(client, _sample_track())
    assert r.status_code == 204
    with app.app_context():
        assert SessionRecording.query.count() == 0


def test_record_bot_dropped(client, app):
    client.get("/", headers={"User-Agent": UA})
    r = _record(client, _sample_track(), ua=BOT_UA)
    assert r.status_code == 204
    with app.app_context():
        assert SessionRecording.query.count() == 0


def test_record_stores_and_upserts_track(client, app):
    client.get("/", headers={"User-Agent": UA})
    assert _record(client, _sample_track()).status_code == 204
    with app.app_context():
        rec = SessionRecording.query.one()
        assert rec.visitor_id  # attached server-side from the cookie
        assert rec.samples == 8  # 4 moves + 1 click + 3 scrolls
        assert rec.click_count == 1

    # Re-send the same rec_id with an extra move → upsert in place (no new row).
    payload = _sample_track()
    payload["track"]["m"].append([3200, 0.9, 2100])
    assert _record(client, payload).status_code == 204
    with app.app_context():
        assert SessionRecording.query.count() == 1
        assert SessionRecording.query.one().samples == 9


def test_admin_recording_apis(client, app):
    client.get("/", headers={"User-Agent": UA})
    _record(client, _sample_track())
    _login_admin(client)

    lst = client.get("/admin/api/heatmap/recordings").get_json()
    assert len(lst["recordings"]) == 1
    assert lst["recordings"][0]["rec_id"] == "rec-test-0001"

    detail = client.get("/admin/api/heatmap/recording/rec-test-0001").get_json()
    assert set(detail["track"].keys()) == {"m", "c", "s"}
    assert len(detail["track"]["m"]) == 4
    assert detail["click_count"] == 1

    assert client.get("/admin/api/heatmap/recording/nope").status_code == 404
