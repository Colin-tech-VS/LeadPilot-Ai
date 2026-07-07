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


def test_artisan_profile_seo(client, app):
    with app.app_context():
        from app.core.extensions import db

        tenant = Tenant(
            name="Plomberie Test SEO",
            trade_type="plombier",
            city="Paris",
            postal_code="75015",
            public_slug="plomberie-test-seo",
            is_public=True,
            public_blurb="Dépannage plomberie 7j/7 à Paris.",
            service_radius_km=20,
        )
        db.session.add(tenant)
        db.session.commit()

    response = client.get("/artisans/plomberie-test-seo")
    assert response.status_code == 200
    html = response.data.decode()
    assert "Plomberie Test SEO" in html
    assert "Paris" in html
    assert "LocalBusiness" in html
    assert "FAQPage" in html
    assert 'hreflang="en"' in html
    assert "plombier" in html.lower() or "plumber" in html.lower()
