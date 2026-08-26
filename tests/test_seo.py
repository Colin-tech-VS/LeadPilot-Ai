"""SEO meta tags, structured data and sitemap."""
from app.models.tenant import Tenant


def test_client_home_seo(client):
    response = client.get("/")
    assert response.status_code == 200
    html = response.data.decode()
    assert 'name="description"' in html
    assert "plombier" in html.lower() or "tradesperson" in html.lower()
    assert 'rel="canonical"' in html
    assert 'hreflang="fr"' in html
    assert 'hreflang="en"' in html
    assert "application/ld+json" in html
    assert "WebSite" in html
    assert "SearchAction" in html
    assert "<h1" in html
    assert "logo-512.png" in html
    assert "og-image.png" in html
    assert 'property="og:image:width" content="1200"' in html
    assert 'rel="icon"' in html
    assert "apple-touch-icon.png" in html
    assert "favicon-32.png" in html


def test_pro_landing_seo(client):
    response = client.get("/pro")
    assert response.status_code == 200
    html = response.data.decode()
    assert 'name="keywords"' in html
    assert "SoftwareApplication" in html
    assert "FAQPage" in html
    assert "pilotcore" in html.lower()
    assert "occupé" in html.lower() or "busy" in html.lower()
    assert "annuaire" in html.lower() or "directory" in html.lower()


def test_public_and_pro_nav_share_zone_line(client):
    """Same Particuliers | Professionnels switcher on both public headers."""
    home = client.get("/").data.decode()
    pro = client.get("/pro").data.decode()
    for html in (home, pro):
        assert 'class="zone-line"' in html
        assert "Particuliers" in html or "Public" in html or "Customers" in html
        assert "Professionnels" in html or "Professionals" in html
        assert "nav-auth-cta" in html
        assert "Connexion" in html or "Sign in" in html
    assert home.index("zone-line") < home.index("public-nav")
    assert pro.index("zone-line") < pro.index("pro-nav")
    assert 'aria-current="page"' in home
    assert 'aria-current="page"' in pro


def test_pro_landing_copy_is_honest(client):
    """No invented volume, no fake launch SKU, no guaranteed-client promise."""
    html = client.get("/pro").data.decode()
    low = html.lower()
    assert "offre de lancement" not in low
    assert "249 €" not in html
    assert "8 clients sur 10" not in low
    assert "8 out of 10" not in low
    assert "ne promet ni" in low or "does not promise" in low
    assert "tester gratuitement" in low or "try it free" in low or "start for free" in low
    assert "sans carte" in low or "no credit card" in low
    assert "google agenda" not in low
    assert "/register" in html


def test_directory_seo(client):
    response = client.get("/artisans")
    assert response.status_code == 200
    html = response.data.decode()
    assert "CollectionPage" in html or "ItemList" in html
    assert 'canonical' in html


def test_directory_filtered_seo_is_localized(client, app):
    """A trade+city filter localizes title/description/H1 and self-canonicalizes."""
    import uuid

    slug = f"serrurier-seo-{uuid.uuid4().hex[:8]}"
    with app.app_context():
        from app.core.extensions import db

        tenant = Tenant(
            name="Serrurier SEO Test",
            trade_type="serrurier",
            city="Chaville",
            postal_code="92370",
            public_slug=slug,
            is_public=True,
            public_blurb="Dépannage serrurerie à Chaville.",
        )
        db.session.add(tenant)
        db.session.commit()

    response = client.get("/artisans?metier=serrurier&ville=Chaville")
    assert response.status_code == 200
    html = response.data.decode()
    # Localized, keyword-rich title + H1 for the metier+ville target
    assert "Serrurier" in html and "Chaville" in html
    assert '<meta name="robots" content="index, follow">' in html
    # Self-referencing canonical so the local combo can rank
    assert "metier=serrurier" in html and "ville=Chaville" in html


def test_directory_empty_filter_is_noindexed(client):
    """A filter with no matching artisans must not be indexed (thin page)."""
    response = client.get("/artisans?metier=plombier&ville=VilleInexistante12345")
    assert response.status_code == 200
    html = response.data.decode()
    assert 'name="robots" content="noindex, follow"' in html


def _set_guides(app, *trade_keys):
    """Control guide state explicitly — the shared test DB is not rolled back
    between tests, so a test that depends on the indexability gate must own
    what is in the table rather than assume it starts empty."""
    from app.core.extensions import db
    from app.models.trade_guide import TradeGuide

    with app.app_context():
        TradeGuide.query.delete()
        for key in trade_keys:
            db.session.add(
                TradeGuide(trade_key=key, lang="fr", body_html="<p>guide</p>")
            )
        db.session.commit()


def test_local_trade_city_landing(client):
    """Clean-URL local landing page is self-canonical, rich and cross-linked."""
    response = client.get("/artisans/plombier/lyon")
    assert response.status_code == 200
    html = response.data.decode()
    assert "Plombier" in html and "Lyon" in html
    assert "/artisans/plombier/lyon" in html  # self-referencing canonical
    assert "BreadcrumbList" in html
    assert "FAQPage" in html
    # real INSEE facts make the page distinct from its siblings
    assert "habitants" in html
    assert "69001" in html  # Lyon postal code
    # internal mesh: real geographic neighbours, not an arbitrary fixed list
    assert "/artisans/plombier/villeurbanne" in html


def test_city_landing_is_noindex_until_it_has_substance(client, app):
    """Thin generated pages must not enter the index — that is the doorway-page
    pattern Google penalises. They graduate on their own once a trade guide
    gives them a body."""
    _set_guides(app)
    assert 'content="noindex, follow"' in client.get("/artisans/plombier/lyon").data.decode()

    _set_guides(app, "plombier")
    assert 'content="index, follow"' in client.get("/artisans/plombier/lyon").data.decode()


def test_small_city_stays_noindex_even_with_a_guide(client, app):
    """Population gate: a guide alone does not justify a page for a town where
    the query barely exists."""
    _set_guides(app, "plombier")

    # Unknown/tiny slug: no INSEE record, so no population to clear the bar.
    html = client.get("/artisans/plombier/trifouillis-les-oies").data.decode()
    assert 'content="noindex, follow"' in html


def test_local_trade_pillar_landing(client):
    response = client.get("/artisans/metier/serrurier")
    assert response.status_code == 200
    html = response.data.decode()
    assert "Serrurier" in html
    assert "/artisans/metier/serrurier" in html


def test_local_landing_invalid_trade_is_404(client):
    assert client.get("/artisans/notatrade/lyon").status_code == 404
    assert client.get("/artisans/metier/notatrade").status_code == 404


def test_local_landing_normalizes_city_slug(client):
    """Accented/cased city input redirects to the canonical slug URL (301)."""
    response = client.get("/artisans/plombier/Lyon", follow_redirects=False)
    assert response.status_code == 301
    assert response.headers["Location"].endswith("/artisans/plombier/lyon")


def test_sitemap_index_lists_children(client):
    response = client.get("/sitemap.xml")
    assert response.status_code == 200
    body = response.data.decode()
    assert "<sitemapindex" in body
    assert "/sitemap-core.xml" in body
    assert "<lastmod>" in body


def test_sitemap_local_pages_gated_until_they_earn_indexing(client, app):
    """Generated city pages stay out of the sitemap while they are thin, and
    appear as soon as a trade guide gives them a body worth ranking."""
    _set_guides(app)
    body = client.get("/sitemap-cities.xml").data.decode()
    assert "/artisans/plombier/lyon</loc>" not in body

    _set_guides(app, "plombier")
    assert "/artisans/plombier/lyon</loc>" in client.get("/sitemap-cities.xml").data.decode()
    assert "/artisans/metier/plombier</loc>" in client.get("/sitemap-trades.xml").data.decode()


def test_sitemap_core_includes_key_pages(client):
    response = client.get("/sitemap-core.xml")
    assert response.status_code == 200
    body = response.data.decode()
    assert "<loc>" in body
    assert "/pro</loc>" in body
    assert "/50-artisans</loc>" in body
    assert "/contact</loc>" in body
    assert "/verification-linkedin</loc>" in body
    assert "/artisans</loc>" in body
    assert "<lastmod>" in body


def test_sitemap_rejects_unknown_section(client):
    assert client.get("/sitemap-nope.xml").status_code == 404


def test_twilio_domain_verification(client):
    response = client.get("/twilio-domain-verification.html")
    assert response.status_code == 200
    body = response.data.decode()
    assert 'name="twilio-domain-verification"' in body
    assert 'content="1f6c8bfa40257e582fd2df5cacfab6bb"' in body

    home = client.get("/").data.decode()
    assert 'name="twilio-domain-verification"' in home
    assert "1f6c8bfa40257e582fd2df5cacfab6bb" in home


def test_robots_allows_public_pages(client):
    response = client.get("/robots.txt")
    assert response.status_code == 200
    body = response.data.decode()
    assert "Sitemap:" in body
    assert "Allow: /contact" in body
    assert "Disallow: /admin" in body
    assert "GPTBot" in body
    assert "ClaudeBot" in body
    assert "PerplexityBot" in body
    assert "llms.txt" in body


def test_llms_txt_index(client):
    response = client.get("/llms.txt")
    assert response.status_code == 200
    assert response.content_type.startswith("text/plain")
    body = response.data.decode()
    assert body.startswith("# PilotCore")
    assert "> PilotCore est" in body
    assert "/blog" in body
    assert "/pro" in body
    assert "/50-artisans" in body
    assert "/artisans" in body
    assert "Starter 149" in body
    assert "Pro 349" in body
    assert "Premium 699" in body
    assert "30 jours" in body
    assert "Starter offert" in body
    assert "numéro IA dédié" not in body
    assert "ligne PilotCore partagée" in body or "ligne PilotCore partagee" in body


def test_llms_full_txt(client):
    response = client.get("/llms-full.txt")
    assert response.status_code == 200
    body = response.data.decode()
    assert "Base de connaissances" in body
    assert "contact@pilotcore.fr" in body
    assert "PilotCore Pro" in body
    assert "Starter 149" in body
    assert "Pro 349" in body
    assert "Premium 699" in body
    assert "30 jours de Starter offert" in body
    assert "numéro IA dédié" not in body
    assert "prendre rendez-vous en ligne 24h/24" not in body


def test_global_json_ld_on_home(client):
    response = client.get("/")
    html = response.data.decode()
    assert '"@id"' in html
    assert "knowsAbout" in html
    assert '"width":512' in html.replace(" ", "") or '"width": 512' in html
    assert "logo-512.png" in html


def test_favicon_ico(client):
    response = client.get("/favicon.ico")
    assert response.status_code == 200
    assert response.data[:4] == b"\x00\x00\x01\x00" or response.mimetype in (
        "image/x-icon",
        "image/vnd.microsoft.icon",
        "image/ico",
    )


def test_artisan_profile_seo(client, app):
    import uuid

    slug = f"plomberie-test-seo-{uuid.uuid4().hex[:8]}"
    with app.app_context():
        from app.core.extensions import db

        tenant = Tenant(
            name="Plomberie Test SEO",
            trade_type="plombier",
            city="Paris",
            postal_code="75015",
            public_slug=slug,
            is_public=True,
            public_blurb="Dépannage plomberie 7j/7 à Paris.",
            service_radius_km=20,
        )
        db.session.add(tenant)
        db.session.commit()

    response = client.get(f"/artisans/{slug}")
    assert response.status_code == 200
    html = response.data.decode()
    assert "Plomberie Test SEO" in html
    assert "Paris" in html
    assert "LocalBusiness" in html
    assert "FAQPage" in html
    assert 'hreflang="en"' in html
    assert "plombier" in html.lower() or "plumber" in html.lower()


def test_artisan_profile_local_seo_wiring(client, app):
    """Profile links into the local-SEO cluster and ships an enriched breadcrumb."""
    import uuid

    slug = f"plomberie-cluster-{uuid.uuid4().hex[:8]}"
    with app.app_context():
        from app.core.extensions import db

        tenant = Tenant(
            name="Plomberie Cluster SEO",
            trade_type="plombier",
            city="Lyon",
            postal_code="69003",
            public_slug=slug,
            is_public=True,
            public_blurb="Dépannage plomberie 7j/7 à Lyon.",
            service_radius_km=25,
        )
        db.session.add(tenant)
        db.session.commit()

    html = client.get(f"/artisans/{slug}").data.decode()
    # Breadcrumb + internal links point at the clean local landing page.
    assert "/artisans/plombier/lyon" in html
    # Cluster cross-links this trade in other cities.
    assert "/artisans/plombier/paris" in html
    # Enriched 4-level BreadcrumbList (Home > Directory > Local landing > Name).
    assert '"position": 4' in html or '"position":4' in html
    # OG image is the branded per-artisan card, not the generic square logo.
    assert "/media/social/profile-" in html


def test_department_landing_pages_render(client):
    """Every /artisans/<trade>/departement/<slug> URL is in the sitemap, so a
    500 here is ~1 000 submitted URLs serving an error page to Googlebot.

    Regression: the view referenced ``registry_import`` without importing it,
    which made the whole family raise NameError.
    """
    for path in (
        "/artisans/plombier/departement/hauts-de-seine",
        "/artisans/electricien/departement/haute-savoie",
        "/artisans/serrurier/departement/gironde",
    ):
        resp = client.get(path)
        assert resp.status_code == 200, f"{path} → {resp.status_code}"
        html = resp.data.decode()
        assert "<title>" in html
        assert 'rel="canonical"' in html


def test_every_department_page_is_reachable(client):
    """Sweep the whole family rather than a sample — these are generated URLs
    and a single bad slug is a silently broken page nobody visits by hand."""
    from app.constants.departments import DEPARTMENTS

    broken = []
    for _code, slug, _name, _chef in DEPARTMENTS:
        resp = client.get(f"/artisans/plombier/departement/{slug}")
        if resp.status_code not in (200, 301, 302):
            broken.append((slug, resp.status_code))
    assert not broken, f"department pages failing: {broken[:10]}"


# --------------------------------------------------------------------------
# Structural invariants — cheap to break silently, so they are asserted rather
# than left to a manual pass over Search Console.
# --------------------------------------------------------------------------

_PUBLIC_PAGES = [
    "/",
    "/artisans",
    "/artisans/plombier/paris",
    "/artisans/metier/plombier",
    "/artisans/plombier/departement/hauts-de-seine",
    "/blog",
    "/contact",
    "/depannage-urgent",
    "/prix-artisans",
    "/pro",
    "/trouver-un-artisan",
    "/prendre-rdv-artisan-en-ligne",
]


def _ld_blocks(html):
    import json
    import re

    out = []
    for raw in re.findall(
        r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S
    ):
        out.append(json.loads(raw))  # a parse error here IS the failure
    return out


def test_all_json_ld_parses(client):
    """A single malformed block makes Google discard the whole page's markup."""
    for path in _PUBLIC_PAGES:
        html = client.get(path).data.decode()
        assert _ld_blocks(html), f"{path} ships no structured data"


def test_brand_entity_is_declared_exactly_once_per_page(client):
    """Organization and WebSite are declared in the layout's global graph.

    A page repeating them repeats the entity: without a matching @id Google
    reads it as a *second* company, and with one it just ships a thinner
    duplicate whose fields can disagree with the canonical node.
    """
    for path in _PUBLIC_PAGES:
        html = client.get(path).data.decode()
        counts = {"Organization": 0, "WebSite": 0}
        for block in _ld_blocks(html):
            for node in block.get("@graph") or [block]:
                if not isinstance(node, dict):
                    continue
                if node.get("@type") in counts:
                    counts[node["@type"]] += 1
                    assert node.get("@id"), f"{path}: {node['@type']} without @id"
        assert counts["Organization"] == 1, f"{path}: {counts['Organization']} Organization nodes"
        assert counts["WebSite"] == 1, f"{path}: {counts['WebSite']} WebSite nodes"


def test_titles_fit_in_a_search_result(client):
    """Google truncates around 60 characters; anything past that is spent ink.

    The cap is generous — it only catches titles long enough that the tail is
    guaranteed invisible.
    """
    import re

    too_long = []
    for path in _PUBLIC_PAGES:
        html = client.get(path).data.decode()
        title = re.search(r"<title[^>]*>(.*?)</title>", html, re.S)
        assert title, f"{path} has no <title>"
        text = title.group(1).strip()
        if len(text) > 70:
            too_long.append((path, len(text), text))
    assert not too_long, f"titles past the truncation point: {too_long}"


def test_every_public_page_is_self_canonical(client):
    """A canonical pointing anywhere but the page itself de-indexes it."""
    import re

    for path in _PUBLIC_PAGES:
        html = client.get(path).data.decode()
        canon = re.search(r'<link[^>]+rel="canonical"[^>]+href="([^"]+)"', html)
        assert canon, f"{path} has no canonical"
        assert canon.group(1).endswith(path.rstrip("/") or "/"), (
            f"{path} canonicalises to {canon.group(1)}"
        )
