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


def test_pro_landing_seo(client):
    response = client.get("/pro")
    assert response.status_code == 200
    html = response.data.decode()
    assert 'name="keywords"' in html
    assert "SoftwareApplication" in html
    assert "FAQPage" in html
    assert "pilotcore" in html.lower()
    assert "occupé" in html.lower() or "busy" in html.lower()


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


def test_local_trade_city_landing(client):
    """Clean-URL local landing page is indexable, self-canonical and rich."""
    response = client.get("/artisans/plombier/lyon")
    assert response.status_code == 200
    html = response.data.decode()
    assert "Plombier" in html and "Lyon" in html
    assert '<meta name="robots" content="index, follow">' in html
    assert "/artisans/plombier/lyon" in html  # self-referencing canonical
    assert "BreadcrumbList" in html
    assert "FAQPage" in html
    # cross-links to other cities and trades for internal SEO
    assert "/artisans/plombier/paris" in html


def test_local_trade_pillar_landing(client):
    response = client.get("/artisans/metier/serrurier")
    assert response.status_code == 200
    html = response.data.decode()
    assert "Serrurier" in html
    assert '<meta name="robots" content="index, follow">' in html
    assert "/artisans/metier/serrurier" in html


def test_local_landing_invalid_trade_is_404(client):
    assert client.get("/artisans/notatrade/lyon").status_code == 404
    assert client.get("/artisans/metier/notatrade").status_code == 404


def test_local_landing_normalizes_city_slug(client):
    """Accented/cased city input redirects to the canonical slug URL (301)."""
    response = client.get("/artisans/plombier/Lyon", follow_redirects=False)
    assert response.status_code == 301
    assert response.headers["Location"].endswith("/artisans/plombier/lyon")


def test_sitemap_includes_local_pages(client):
    body = client.get("/sitemap.xml").data.decode()
    assert "/artisans/metier/plombier</loc>" in body
    assert "/artisans/plombier/lyon</loc>" in body


def test_sitemap_includes_key_pages(client):
    response = client.get("/sitemap.xml")
    assert response.status_code == 200
    body = response.data.decode()
    assert "<loc>" in body
    assert "/pro</loc>" in body or "/pro<" in body
    assert "/contact</loc>" in body or "/contact<" in body
    assert "/artisans</loc>" in body or "/artisans<" in body
    assert "<lastmod>" in body


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
    assert "/artisans" in body


def test_llms_full_txt(client):
    response = client.get("/llms-full.txt")
    assert response.status_code == 200
    body = response.data.decode()
    assert "Base de connaissances" in body
    assert "contact@pilotcore.fr" in body
    assert "PilotCore Pro" in body


def test_global_json_ld_on_home(client):
    response = client.get("/")
    html = response.data.decode()
    assert '"@id"' in html
    assert "knowsAbout" in html


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
