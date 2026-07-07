"""Blog public routes and SEO."""
import json
import uuid
from datetime import datetime, timezone

from app.core.extensions import db
from app.models.blog_category import BlogCategory
from app.models.blog_post import BlogPost
from app.services.blog import ensure_default_categories


def test_blog_index_and_seo(client, app):
    with app.app_context():
        ensure_default_categories()
        cat = BlogCategory.query.filter_by(slug="conseils-artisans").first()
        slug = f"test-article-seo-{uuid.uuid4().hex[:8]}"
        post = BlogPost(
            id=uuid.uuid4(),
            slug=slug,
            title="Comment ne plus rater un appel client",
            excerpt="Guide pratique pour les artisans en intervention.",
            meta_description="Ne ratez plus les appels clients : conseils pour plombiers et électriciens.",
            meta_keywords="artisan, appels manqués, plombier, PilotCore",
            body_html="<h2>Le problème</h2><p>En intervention, chaque appel compte.</p>",
            category_id=cat.id if cat else None,
            status="published",
            reading_time_min=5,
            featured=True,
            published_at=datetime.now(timezone.utc),
        )
        post.set_faq([{"question": "Pourquoi rater des appels ?", "answer": "Parce qu'on est occupé sur chantier."}])
        db.session.add(post)
        db.session.commit()

    response = client.get("/blog")
    assert response.status_code == 200
    html = response.data.decode()
    assert "Blog" in html
    assert "application/ld+json" in html
    assert 'rel="canonical"' in html

    article = client.get(f"/blog/{slug}")
    assert article.status_code == 200
    art_html = article.data.decode()
    assert "BlogPosting" in art_html
    assert "FAQPage" in art_html
    assert "Comment ne plus rater" in art_html
    assert "blog-article-shell" in art_html
    assert "blog-toc-list" in art_html or "blog-mid-cta" in art_html
    assert "article:published_time" in art_html or "article:section" in art_html


def test_prepare_article_body_toc(app):
    with app.app_context():
        from app.services.blog import prepare_article_body

        html, toc = prepare_article_body("<h2>Le problème</h2><p>Texte.</p><h2>La solution</h2>")
        assert 'id="le-probleme"' in html
        assert len(toc) == 2
        assert toc[0][1] == "Le problème"


def test_blog_category_page(client, app):
    with app.app_context():
        ensure_default_categories()

    response = client.get("/blog/categorie/conseils-artisans")
    assert response.status_code == 200
    assert "Conseils artisans" in response.data.decode()


def test_sitemap_includes_blog(client, app):
    with app.app_context():
        ensure_default_categories()
    response = client.get("/sitemap.xml")
    assert response.status_code == 200
    body = response.data.decode()
    assert "/blog" in body
    assert "/blog/categorie/conseils-artisans" in body


def test_category_post_counts(app):
    with app.app_context():
        from app.services.blog import category_post_counts, ensure_blog_schema, ensure_default_categories

        ensure_blog_schema()
        ensure_default_categories()
        counts = category_post_counts()
        assert isinstance(counts, dict)


def test_generate_blog_post_shape(app, monkeypatch):
    def fake_complete(system, user, **kwargs):
        assert "SEO" in system or "seo" in system.lower()
        return json.dumps(
            {
                "title": "Titre test",
                "meta_description": "Meta test " * 8,
                "meta_keywords": "artisan, plombier",
                "excerpt": "Chapô test.",
                "reading_time_min": 6,
                "body_html": "<h2>Section</h2><p>Contenu.</p>",
                "faq": [{"question": "Q?", "answer": "A."}],
            }
        )

    monkeypatch.setattr("app.services.content_ai._complete", fake_complete)
    with app.app_context():
        from app.services.content_ai import generate_blog_post

        result = generate_blog_post("Sujet test", category_hint="Conseils artisans")
    assert result["title"]
    assert result["meta_keywords"]
    assert result["faq"]
    assert "<h2>" in result["body_html"]


def test_blog_article_with_incomplete_faq_json_ld(client, app):
    with app.app_context():
        ensure_default_categories()
        slug = f"test-faq-gap-{uuid.uuid4().hex[:8]}"
        post = BlogPost(
            id=uuid.uuid4(),
            slug=slug,
            title="Article FAQ incomplète",
            excerpt="Test",
            meta_description="Test meta",
            body_html="<p>Contenu.</p>",
            status="published",
            published_at=datetime.now(timezone.utc),
        )
        post.faq_json = '[{"question": "Question sans réponse ?"}]'
        db.session.add(post)
        db.session.commit()

    response = client.get(f"/blog/{slug}")
    assert response.status_code == 200, response.get_data(as_text=True)
    assert "Question sans réponse" in response.get_data(as_text=True)
