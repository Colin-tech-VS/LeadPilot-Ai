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
    assert "standard téléphonique" in html.lower() or "phone system" in html.lower()


def test_directory_seo(client):
    response = client.get("/artisans")
    assert response.status_code == 200
    html = response.data.decode()
    assert "CollectionPage" in html or "ItemList" in html
    assert 'canonical' in html


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
