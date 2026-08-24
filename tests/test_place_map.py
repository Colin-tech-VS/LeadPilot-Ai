"""Public place maps: coords from address, then city centroid."""


def test_city_fallback_without_network(app, monkeypatch):
    monkeypatch.setattr("app.services.place_map.geocode_address", lambda q: (_ for _ in ()).throw(AssertionError("no geocode")))
    with app.app_context():
        from app.services.place_map import resolve_coords

        lat, lng = resolve_coords(
            latitude=None,
            longitude=None,
            city="Nantes",
            city_slug="nantes",
        )
        assert round(lat, 1) == 47.2
        assert round(lng, 1) == -1.6


def test_street_address_uses_geocode(app, monkeypatch):
    monkeypatch.setattr("app.services.place_map.geocode_address", lambda q: (47.218, -1.553))
    with app.app_context():
        from app.services.place_map import resolve_coords

        coords = resolve_coords(
            latitude=None,
            longitude=None,
            address="12 rue Kervégan",
            postal_code="44000",
            city="Nantes",
            city_slug="nantes",
        )
        assert coords == (47.218, -1.553)


def test_stored_coords_win(app, monkeypatch):
    monkeypatch.setattr("app.services.place_map.geocode_address", lambda q: (0, 0))
    with app.app_context():
        from app.services.place_map import resolve_coords

        coords = resolve_coords(latitude=48.85, longitude=2.35, address="anywhere")
        assert coords == (48.85, 2.35)


def test_pro_landing_shows_dashboard_shots(client):
    resp = client.get("/pro")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "dash-mock--board" in html
    assert "v-product-shots" in html
    assert "Espace artisan" in html or "Your trade workspace" in html or "tableau de bord" in html.lower()
