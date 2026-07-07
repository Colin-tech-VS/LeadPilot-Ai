"""Tests for IP geolocation and traffic location aggregates."""
from datetime import datetime, timezone

from app.core.extensions import db
from app.models.page_view import PageView
from app.services.geoip import format_location, is_public_ip, lookup_ip
from app.services.traffic import top_locations, utm_breakdown


def test_is_public_ip():
    assert is_public_ip("8.8.8.8") is True
    assert is_public_ip("127.0.0.1") is False
    assert is_public_ip("192.168.1.1") is False


def test_format_location_full():
    label = format_location(
        {
            "city": "Paris",
            "postal_code": "75015",
            "region": "Île-de-France",
            "country_code": "FR",
        }
    )
    assert "Paris" in label
    assert "75015" in label
    assert "FR" in label


def test_lookup_ip_uses_cache(app, monkeypatch):
    def fake_fetch(ip):
        return {
            "country_code": "FR",
            "country": "France",
            "region": "Île-de-France",
            "city": "Paris",
            "postal_code": "75001",
            "latitude": 48.86,
            "longitude": 2.35,
        }

    monkeypatch.setattr("app.services.geoip._fetch_remote", fake_fetch)

    with app.app_context():
        g1 = lookup_ip("8.8.4.4")
        g2 = lookup_ip("8.8.4.4")
    assert g1 and g2
    assert g1["city"] == "Paris"
    assert g2["city"] == "Paris"


def test_traffic_location_and_utm(app):
    now = datetime.now(timezone.utc)
    with app.app_context():
        db.session.add(
            PageView(
                visitor_id="v1",
                session_id="s1",
                path="/pro",
                geo_city="Lyon",
                geo_postal_code="69003",
                geo_region="Auvergne-Rhône-Alpes",
                geo_country_code="FR",
                utm_source="facebook",
                utm_medium="social",
                utm_campaign="pro_landing",
                created_at=now,
            )
        )
        db.session.add(
            PageView(
                visitor_id="v2",
                session_id="s2",
                path="/",
                geo_city="Paris",
                geo_postal_code="75015",
                geo_country_code="FR",
                utm_source="facebook",
                utm_medium="social",
                utm_campaign="particuliers_home",
                created_at=now,
            )
        )
        db.session.commit()

        locs = top_locations(days=30)
        assert any("Lyon" in row["label"] for row in locs)
        utm = utm_breakdown(days=30)
        assert any(row["utm_source"] == "facebook" for row in utm)
