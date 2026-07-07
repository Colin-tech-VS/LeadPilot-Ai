"""Tests for traffic conversion metrics and GA4-style aggregates."""
from datetime import datetime, timezone

import pytest

from app.core.extensions import db
from app.models.page_view import PageView
from app.models.user import User
from app.services.traffic import (
    acquisition_funnel,
    channel_breakdown,
    conversions,
    timeseries,
    utm_breakdown,
)

FIXED_NOW = datetime(2099, 6, 15, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def traffic_clock(monkeypatch):
    monkeypatch.setattr("app.services.traffic._utcnow", lambda: FIXED_NOW)


def _user(email, role="user", tenant_id=None):
    u = User(email=email, role=role, tenant_id=tenant_id)
    u.set_password("secret123")
    return u


def test_conversions_and_funnel(app, traffic_clock):
    with app.app_context():
        for i, vid in enumerate(["v1", "v2", "v3", "v4"]):
            db.session.add(
                PageView(
                    visitor_id=vid,
                    session_id="s" + vid,
                    path="/" if i < 3 else "/register",
                    created_at=FIXED_NOW,
                )
            )
        u1 = _user("artisan2099@test.com", role="user")
        u2 = _user("client2099@test.com", role="customer")
        u1.created_at = FIXED_NOW
        u2.created_at = FIXED_NOW
        db.session.add(u1)
        db.session.add(u2)
        db.session.commit()

        conv = conversions(days=30)
        assert conv["unique_visitors"] == 4
        assert conv["signups_total"] == 2
        assert conv["signups_artisan"] == 1
        assert conv["signups_customer"] == 1
        assert conv["register_visitors"] == 1
        assert conv["visitor_to_signup_rate"] == 50.0

        funnel = acquisition_funnel(days=30)
        assert funnel[0]["count"] == 4
        assert funnel[1]["count"] == 1
        assert funnel[2]["count"] == 2


def test_channel_breakdown(app, traffic_clock):
    with app.app_context():
        db.session.add(
            PageView(
                visitor_id="v-ch-1",
                session_id="s-ch-1",
                path="/",
                referrer_host="www.google.com",
                created_at=FIXED_NOW,
            )
        )
        db.session.add(
            PageView(
                visitor_id="v-ch-2",
                session_id="s-ch-2",
                path="/register",
                utm_source="facebook",
                utm_medium="social",
                created_at=FIXED_NOW,
            )
        )
        db.session.commit()

        channels = channel_breakdown(days=30)
        labels = {c["label"] for c in channels}
        assert "Organic Search" in labels
        assert "Social" in labels


def test_timeseries_includes_signups(app, traffic_clock):
    with app.app_context():
        db.session.add(
            PageView(
                visitor_id="v-ts-1",
                session_id="s-ts-1",
                path="/",
                created_at=FIXED_NOW,
            )
        )
        u = _user("new2099@test.com", role="customer")
        u.created_at = FIXED_NOW
        db.session.add(u)
        db.session.commit()

        series = timeseries(days=7)
        today = FIXED_NOW.date().isoformat()
        row = next((r for r in series if r["date"] == today), None)
        assert row is not None
        assert row["views"] >= 1
        assert row["signups"] >= 1


def test_utm_register_rate(app, traffic_clock):
    with app.app_context():
        db.session.add(
            PageView(
                visitor_id="v-utm-1",
                session_id="s-utm-1",
                path="/",
                utm_source="facebook",
                utm_medium="social",
                utm_campaign="pro_test",
                created_at=FIXED_NOW,
            )
        )
        db.session.add(
            PageView(
                visitor_id="v-utm-1",
                session_id="s-utm-1",
                path="/register",
                utm_source="facebook",
                utm_medium="social",
                utm_campaign="pro_test",
                created_at=FIXED_NOW,
            )
        )
        db.session.commit()

        utm = utm_breakdown(days=30)
        row = next((r for r in utm if r.get("utm_campaign") == "pro_test"), None)
        assert row is not None
        assert row["register_visitors"] == 1
        assert row["register_rate"] == 100.0
