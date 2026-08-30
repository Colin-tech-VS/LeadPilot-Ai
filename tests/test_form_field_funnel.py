"""« Ils commencent le formulaire et ne le terminent pas » — sur quelle ligne ?

A funnel that stops at « started » and « completed » cannot answer that. The
field tracker records, per visitor and per field, that it was reached, how it
was left, and which one they were sitting on when they gave up.
"""
from app.models.heatmap_event import TYPE_FIELD
from app.services import heatmap


# The collector drops anything that does not look like a browser, and keys the
# batch on the httpOnly visitor cookie rather than on anything the page sends.
_UA = ("Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/151.0.0.0 Mobile Safari/537.36")


def _send(client, events):
    client.get("/register", headers={"User-Agent": _UA})  # sets the visitor cookie
    return client.post(
        "/api/heatmap/collect", json={"events": events}, headers={"User-Agent": _UA}
    )


def _field(name, state, path="/register"):
    return {"t": TYPE_FIELD, "p": path, "s": name, "txt": state}


def test_the_funnel_names_the_field_people_stop_on(client, app):
    resp = _send(client, [
        _field("company_name", "reached"), _field("company_name", "filled"),
        _field("city", "reached"), _field("city", "empty"),
        _field("email", "reached"), _field("email", "filled"),
        _field("password", "reached"), _field("password", "abandoned"),
    ])
    assert resp.status_code == 204

    with app.app_context():
        d = heatmap.form_funnel("/register", 30)

    assert d["abandoned_on"] == "password"
    by_name = {f["name"]: f for f in d["fields"]}
    # Order follows the form, so the table reads top to bottom like the page.
    assert [f["name"] for f in d["fields"]] == ["company_name", "city", "email", "password"]
    assert by_name["company_name"]["filled"] == 1
    assert by_name["city"]["empty"] == 1 and by_name["city"]["filled"] == 0
    assert by_name["password"]["abandoned"] == 1


def test_one_visitor_counts_once_per_field(client, app):
    """Someone who tabs in and out of the same field four times is one person
    stuck on it, not four.

    Its own path: the suite shares one database between tests, and the funnel
    is scoped by page — which is exactly the isolation this needs.
    """
    path = "/register?case=repeat"
    _send(client, [_field("email", "reached", path)] * 4 + [_field("email", "empty", path)] * 4)
    with app.app_context():
        d = heatmap.form_funnel(path, 30)
    email = next(f for f in d["fields"] if f["name"] == "email")
    assert email["reached"] == 1 and email["empty"] == 1


def test_a_bogus_state_or_an_empty_name_is_dropped(client, app):
    """The payload comes from the page; only the four known states are stored."""
    path = "/register?case=bogus"
    _send(client, [
        _field("email", "value:hunter2", path),   # never a state we accept
        _field("", "reached", path),
        {"t": TYPE_FIELD, "p": path, "s": "siret", "txt": "reached"},
    ])
    with app.app_context():
        d = heatmap.form_funnel(path, 30)
    assert [f["name"] for f in d["fields"]] == ["siret"]


def test_the_funnel_is_scoped_to_one_page(client, app):
    """Three sign-up forms share the tracker; the panel must never mix them."""
    _send(client, [_field("phone", "reached", "/50-artisans")])
    with app.app_context():
        founding = heatmap.form_funnel("/50-artisans", 30)
        artisan = heatmap.form_funnel("/register", 30)
    assert [f["name"] for f in founding["fields"]] == ["phone"]
    assert "phone" not in [f["name"] for f in artisan["fields"]]
    assert founding["visitors"] >= 1
