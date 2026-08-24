"""Google Places wiring: proxy behaviour, fallback, and key confinement."""
import json

import pytest

from app.services import address_lookup, google_places


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = json.dumps(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


AUTOCOMPLETE_OK = {
    "suggestions": [
        {
            "placePrediction": {
                "placeId": "PID-1",
                "text": {"text": "Chaville, France"},
                "structuredFormat": {
                    "mainText": {"text": "Chaville"},
                    "secondaryText": {"text": "France"},
                },
            }
        },
        {
            "placePrediction": {
                "placeId": "PID-2",
                "text": {"text": "12 Rue de la Paix, 75002 Paris, France"},
                "structuredFormat": {"mainText": {"text": "12 Rue de la Paix"}},
            }
        },
        # A query prediction carries no place — it must never reach a field.
        {"queryPrediction": {"text": {"text": "plombier"}}},
    ]
}

DETAILS_OK = {
    "id": "PID-1",
    "formattedAddress": "12 Rue de la Paix, 75002 Paris, France",
    "shortFormattedAddress": "12 Rue de la Paix, Paris",
    "displayName": {"text": "Quelque part"},
    "location": {"latitude": 48.8691, "longitude": 2.3313},
    "addressComponents": [
        {"types": ["street_number"], "longText": "12"},
        {"types": ["route"], "longText": "Rue de la Paix"},
        {"types": ["locality", "political"], "longText": "Paris"},
        {"types": ["postal_code"], "longText": "75002"},
    ],
}


@pytest.fixture(autouse=True)
def _clean_cache():
    google_places.reset_cache()
    yield
    google_places.reset_cache()


@pytest.fixture
def places_app(app):
    app.config["GOOGLE_PLACES_API_KEY"] = "test-key"
    return app


# --------------------------------------------------------------------- service


def test_autocomplete_city_returns_bare_commune_name(places_app, monkeypatch):
    """A city field must hold « Chaville », not « Chaville, France » — our own
    city lookups match on the bare commune name."""
    monkeypatch.setattr(
        google_places.requests, "post", lambda *a, **k: _Resp(AUTOCOMPLETE_OK)
    )
    with places_app.app_context():
        out = google_places.autocomplete("chavi", kind="city")
    assert out[0]["value"] == "Chaville"
    assert out[0]["id"] == "PID-1"
    # Query predictions are dropped: only two of the three entries are places.
    assert len(out) == 2


def test_autocomplete_address_strips_country_suffix(places_app, monkeypatch):
    monkeypatch.setattr(
        google_places.requests, "post", lambda *a, **k: _Resp(AUTOCOMPLETE_OK)
    )
    with places_app.app_context():
        out = google_places.autocomplete("12 rue", kind="address")
    assert out[1]["value"] == "12 Rue de la Paix, 75002 Paris"


def test_autocomplete_disabled_without_key(app, monkeypatch):
    app.config["GOOGLE_PLACES_API_KEY"] = ""
    called = []
    monkeypatch.setattr(
        google_places.requests, "post", lambda *a, **k: called.append(1) or _Resp({})
    )
    with app.app_context():
        assert google_places.autocomplete("paris") == []
    assert not called, "no key must mean no billed request"


def test_autocomplete_is_cached(places_app, monkeypatch):
    """Places bills per request — the same prefix must not be bought twice."""
    calls = []

    def _post(*a, **k):
        calls.append(1)
        return _Resp(AUTOCOMPLETE_OK)

    monkeypatch.setattr(google_places.requests, "post", _post)
    with places_app.app_context():
        google_places.autocomplete("chavi", kind="city")
        google_places.autocomplete("CHAVI", kind="city")
    assert len(calls) == 1


def test_http_error_degrades_to_empty(places_app, monkeypatch):
    monkeypatch.setattr(
        google_places.requests, "post", lambda *a, **k: _Resp({"error": "denied"}, status=403)
    )
    with places_app.app_context():
        assert google_places.autocomplete("chavi") == []
        assert google_places.stats()["errors"] == 1


def test_network_failure_degrades_to_empty(places_app, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(google_places.requests, "post", _boom)
    with places_app.app_context():
        assert google_places.autocomplete("chavi") == []


def test_resolve_extracts_postcode_and_coordinates(places_app, monkeypatch):
    monkeypatch.setattr(google_places.requests, "get", lambda *a, **k: _Resp(DETAILS_OK))
    with places_app.app_context():
        out = google_places.resolve("PID-1")
    assert out["postcode"] == "75002"
    assert out["city"] == "Paris"
    assert out["street"] == "12 Rue de la Paix"
    assert out["latitude"] == pytest.approx(48.8691)
    assert out["address"].endswith("75002 Paris")


def test_geocode_returns_coordinates(places_app, monkeypatch):
    payload = {"status": "OK", "results": [{"geometry": {"location": {"lat": 48.8, "lng": 2.3}}}]}
    monkeypatch.setattr(google_places.requests, "get", lambda *a, **k: _Resp(payload))
    with places_app.app_context():
        assert google_places.geocode("Chaville") == (48.8, 2.3)


def test_geocode_zero_results_is_not_an_error(places_app, monkeypatch):
    monkeypatch.setattr(
        google_places.requests, "get", lambda *a, **k: _Resp({"status": "ZERO_RESULTS", "results": []})
    )
    with places_app.app_context():
        assert google_places.geocode("zzzz") is None
        assert google_places.stats()["errors"] == 0


def test_geocoding_util_prefers_google_then_falls_back(places_app, monkeypatch):
    """app.utils.geocoding must use Google when keyed, Nominatim otherwise."""
    from app.utils import geocoding

    monkeypatch.setattr(google_places, "geocode", lambda addr: (1.0, 2.0))
    with places_app.app_context():
        assert geocoding.geocode_address("Chaville") == (1.0, 2.0)

    # Google returning nothing must not swallow the request — Nominatim answers.
    monkeypatch.setattr(google_places, "geocode", lambda addr: None)
    monkeypatch.setattr(
        geocoding.requests, "get", lambda *a, **k: _Resp([{"lat": "3.0", "lon": "4.0"}])
    )
    with places_app.app_context():
        assert geocoding.geocode_address("Chaville") == (3.0, 4.0)


# ------------------------------------------------------------------- fallback


def test_lookup_falls_back_to_ban_when_google_is_down(places_app, monkeypatch):
    """Address entry sits on the signup and quote forms — it must survive a
    Google outage rather than leaving the visitor with a dead field."""
    monkeypatch.setattr(google_places.requests, "post", lambda *a, **k: _Resp({}, status=500))
    ban = {
        "features": [
            {
                "properties": {"label": "Chaville", "city": "Chaville", "postcode": "92370"},
                "geometry": {"coordinates": [2.19, 48.80]},
            }
        ]
    }
    monkeypatch.setattr(address_lookup.requests, "get", lambda *a, **k: _Resp(ban))
    with places_app.app_context():
        out = address_lookup.suggest("chavi", kind="city")
    assert out["provider"] == "ban"
    assert out["suggestions"][0]["value"] == "Chaville"
    # BAN carries the postcode inline, so no /resolve round trip is needed.
    assert out["suggestions"][0]["postcode"] == "92370"


def test_ban_address_is_formatted_like_places(app, monkeypatch):
    """Without a Places key the fallback must still look like a place line
    (street, postcode, commune) so the directory can extract the town."""
    app.config["GOOGLE_PLACES_API_KEY"] = ""
    ban = {
        "features": [
            {
                "properties": {
                    "label": "12 Rue de la Paix 50100 Cherbourg-en-Cotentin",
                    "name": "12 Rue de la Paix",
                    "city": "Cherbourg-en-Cotentin",
                    "postcode": "50100",
                },
                "geometry": {"coordinates": [-1.62, 49.64]},
            }
        ]
    }
    monkeypatch.setattr(address_lookup.requests, "get", lambda *a, **k: _Resp(ban))
    with app.app_context():
        out = address_lookup.suggest("12 rue de la paix", kind="address")
    assert out["provider"] == "ban"
    sug = out["suggestions"][0]
    assert sug["value"] == "12 Rue de la Paix, 50100 Cherbourg-en-Cotentin"
    assert sug["city"] == "Cherbourg-en-Cotentin"
    assert sug["main"] == "12 Rue de la Paix"
    assert sug["secondary"] == "50100 Cherbourg-en-Cotentin"


def test_lookup_uses_google_when_available(places_app, monkeypatch):
    monkeypatch.setattr(
        google_places.requests, "post", lambda *a, **k: _Resp(AUTOCOMPLETE_OK)
    )
    with places_app.app_context():
        out = address_lookup.suggest("chavi", kind="city")
    assert out["provider"] == "google"


def test_short_query_never_calls_a_provider(places_app, monkeypatch):
    def _fail(*a, **k):
        raise AssertionError("must not be called")

    monkeypatch.setattr(google_places.requests, "post", _fail)
    monkeypatch.setattr(address_lookup.requests, "get", _fail)
    with places_app.app_context():
        assert address_lookup.suggest("a")["suggestions"] == []


# ---------------------------------------------------------------------- routes


def test_city_from_place_query_extracts_the_commune():
    from app.services.address_lookup import city_from_place_query

    assert city_from_place_query("Lyon") == "Lyon"
    assert city_from_place_query("75015") == "75015"
    assert city_from_place_query("Chaville (92370)") == "Chaville"
    assert city_from_place_query("12 Rue de la Paix, 75002 Paris, France") == "Paris"
    assert city_from_place_query("Rue de la République, Lyon") == "Lyon"
    # BAN's native label has no commas — that used to be returned as the "city".
    assert city_from_place_query("12 Rue de la Paix 50100 Cherbourg-en-Cotentin") == (
        "Cherbourg-en-Cotentin"
    )
    assert city_from_place_query("12 Rue de la Paix, 50100 Cherbourg-en-Cotentin") == (
        "Cherbourg-en-Cotentin"
    )
    assert city_from_place_query("12 rue de la paix") == ""


def test_autocomplete_endpoint_returns_suggestions(places_app, client, monkeypatch):
    monkeypatch.setattr(
        google_places.requests, "post", lambda *a, **k: _Resp(AUTOCOMPLETE_OK)
    )
    resp = client.get("/api/public/places/autocomplete?q=chavi&kind=city")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["provider"] == "google"
    assert body["suggestions"][0]["value"] == "Chaville"
    cache = resp.headers.get("Cache-Control", "")
    assert "max-age" in cache
    # Never shared-cacheable: the response can carry the session cookie that
    # holds the Places billing token, and its language is per-visitor.
    assert "private" in cache and "public" not in cache
    assert "Cookie" in resp.headers.get("Vary", "")


def test_resolve_endpoint_requires_an_id(places_app, client):
    assert client.get("/api/public/places/resolve").status_code == 422


def test_resolve_endpoint_404s_on_unknown_place(places_app, client, monkeypatch):
    monkeypatch.setattr(google_places.requests, "get", lambda *a, **k: _Resp({}, status=404))
    assert client.get("/api/public/places/resolve?id=nope").status_code == 404


def test_city_search_fields_are_wired_for_places(places_app, client):
    """Every public search bar must opt into Places so the JS attaches."""
    for path in ("/", "/artisans", "/trouver-un-artisan", "/depannage-urgent"):
        html = client.get(path).data.decode()
        assert "address-autocomplete.js" in html, path
        assert "data-places-where" in html, path


def test_api_key_is_never_rendered_into_a_page(places_app, client):
    """The key is billed per request and ours is not referrer-locked, so a copy
    in the HTML would be a copy anyone can spend."""
    places_app.config["GOOGLE_PLACES_API_KEY"] = "SUPER-SECRET-KEY"
    for path in ("/", "/artisans", "/contact", "/client/register", "/artisans/plombier/paris"):
        html = client.get(path).data.decode()
        assert "SUPER-SECRET-KEY" not in html, f"key leaked into {path}"
        assert "maps.googleapis.com" not in html, f"direct Google call from {path}"


def test_autocomplete_js_calls_our_origin_not_google():
    js = open("static/js/address-autocomplete.js", encoding="utf-8").read()
    assert "/api/public/places/autocomplete" in js
    assert "maps.googleapis.com" not in js
    assert "places.googleapis.com" not in js
    # Dropdown must leave the overflow-hidden search pill, otherwise
    # suggestions render inside the input and are clipped.
    assert "document.body.appendChild" in js
    assert "getBoundingClientRect" in js
    assert "input.places-city" in js
    assert "data-places-where" in js
    assert "client_address" in js
    assert "commitOpenSuggestion" in js
    assert "ac-item-main" in js
