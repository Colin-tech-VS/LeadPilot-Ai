"""Every template must compile, and the signed-in dashboard must render.

`templates/artisan/dashboard.html` shipped with the Jinja block `body_attrs`
declared twice — once correctly at the top, once pasted inside `{% block
content %}`. Jinja only raises on *compile*, which happens the first time the
template is rendered, so nothing failed at boot, no test covered it, and the
artisan dashboard returned 500 for every account that reached it after signing
up. A syntax error that only surfaces on a live request is exactly the kind the
suite should catch instead.
"""
import uuid

import pytest

TEMPLATE_GLOB = "**/*.html"


def _all_templates(app):
    return sorted(app.jinja_env.list_templates(extensions=("html",)))


def test_every_template_compiles(app):
    """Catches duplicate blocks, unclosed tags, bad filters — statically."""
    broken = []
    for name in _all_templates(app):
        try:
            app.jinja_env.get_template(name)
        except Exception as exc:  # noqa: BLE001 - we want the whole failure list
            broken.append(f"{name}: {type(exc).__name__}: {exc}")

    assert not broken, "templates that do not compile:\n  " + "\n  ".join(broken)


def test_no_template_declares_the_same_block_twice(app):
    """The specific shape that broke the dashboard, named so a regression is
    obvious in the failure message rather than a generic compile error."""
    import re

    from jinja2 import TemplateNotFound

    pattern = re.compile(r"{%-?\s*block\s+([A-Za-z_][A-Za-z0-9_]*)")
    offenders = []
    for name in _all_templates(app):
        try:
            source = app.jinja_env.loader.get_source(app.jinja_env, name)[0]
        except TemplateNotFound:
            continue
        seen = {}
        for block in pattern.findall(source):
            seen[block] = seen.get(block, 0) + 1
        for block, count in seen.items():
            if count > 1:
                offenders.append(f"{name}: block '{block}' declared {count}x")

    assert not offenders, "duplicate Jinja blocks:\n  " + "\n  ".join(offenders)


def test_a_fresh_signup_reaches_a_working_dashboard(client, app):
    """The exact path the artisan takes: create the account, land on /dashboard.

    The 500 was only reachable through a real session, which is why unit-level
    template checks alone would not have caught it.
    """
    email = f"dash-{uuid.uuid4().hex[:8]}@example.com"
    response = client.post(
        "/register",
        data={
            "company_name": "Plomberie Dashboard",
            "city": "Lyon",
            "trade_type": "plombier",
            "email": email,
            "password": "MotDePasse123",
        },
    )
    assert response.status_code == 302, "signup should redirect, not redisplay the form"
    assert "/dashboard" in response.headers["Location"]

    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200, f"dashboard returned {dashboard.status_code}"


@pytest.mark.parametrize("path", ["/", "/pro", "/register", "/login", "/trouver-un-artisan"])
def test_the_public_pages_render(client, path):
    assert client.get(path).status_code == 200
