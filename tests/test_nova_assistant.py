"""Tests for Nova's site-analysis capabilities (deep analysis + audit tools).

These cover the pure, data-driven helpers so we can trust that Nova's
recommendations and actions target real gaps — without needing a live
Mistral / Search Console connection.
"""
from app.services import assistant


def test_new_analysis_tools_are_registered(app):
    with app.app_context():
        names = {s["function"]["name"] for s in assistant._tool_schemas()}
        for tool in ("analyze_traffic", "analyze_seo", "audit_content", "system_health"):
            assert tool in names
            assert tool in assistant._TOOL_IMPL


def test_site_snapshot_includes_deep_dimensions(app):
    with app.app_context():
        snap = assistant.site_snapshot()
        # Nova can now "see" content health, traffic and SEO, not just counts.
        for key in ("content_health", "traffic_30d", "seo", "integrations"):
            assert key in snap
        assert set(snap["content_health"]) >= {
            "pages_missing_meta", "blog_drafts", "blog_thin"
        }


def test_content_audit_flags_real_gaps(app):
    from app.core.extensions import db
    from app.models.blog_post import BlogPost
    from app.models.site_page import SitePage

    with app.app_context():
        # The testing DB is a persistent file shared across the suite — start
        # from a clean content slate so the audit counts are deterministic.
        BlogPost.query.delete()
        SitePage.query.delete()
        db.session.commit()

        # A healthy, published article with a meta description and a real body.
        good = BlogPost()
        good.title = "Guide complet du dépannage plomberie"
        good.slug = "guide-depannage"
        good.status = "published"
        good.meta_description = "Un guide détaillé pour les artisans plombiers."
        good.body_html = "<p>" + ("Contenu riche et détaillé. " * 60) + "</p>"

        # A weak draft: no meta, thin body, not published.
        weak = BlogPost()
        weak.title = "Brouillon rapide"
        weak.slug = "brouillon-rapide"
        weak.status = "draft"
        weak.meta_description = None
        weak.body_html = "<p>Trop court.</p>"

        # A page missing its meta description.
        page = SitePage()
        page.title = "Page sans meta"
        page.slug = "page-sans-meta"
        page.status = "published"
        page.meta_description = ""
        page.body_html = "<p>" + ("Texte de page. " * 60) + "</p>"

        db.session.add_all([good, weak, page])
        db.session.commit()

        audit = assistant.content_audit()
        blog = audit["blog"]
        assert blog["total"] == 2
        assert blog["draft_count"] == 1
        assert blog["missing_meta_count"] == 1
        assert blog["thin_count"] == 1
        assert "Brouillon rapide" in blog["drafts"]

        pages = audit["pages"]
        assert pages["missing_meta_count"] == 1
        assert "Page sans meta" in pages["missing_meta"]

        # The tool wrapper mirrors the helper and marks success.
        result = assistant.tool_audit_content(None)
        assert result["ok"] is True
        assert result["blog"]["draft_count"] == 1


def test_seo_opportunities_selects_underperformers():
    rows = [
        # Strong performer — already ranks well, not an opportunity.
        {"keys": ["plombier paris"], "clicks": 50, "impressions": 500,
         "ctr": 0.10, "position": 2.0},
        # High impressions but poor position — a real opportunity.
        {"keys": ["devis electricien lyon"], "clicks": 1, "impressions": 800,
         "ctr": 0.001, "position": 14.0},
        # Decent position but very low CTR — largest CTR/position gap.
        {"keys": ["standard telephonique artisan"], "clicks": 0, "impressions": 300,
         "ctr": 0.0, "position": 6.0},
        # Too few impressions — ignored as noise.
        {"keys": ["mot rare"], "clicks": 0, "impressions": 3,
         "ctr": 0.0, "position": 30.0},
        # Plenty of clicks already — not a priority even with a modest CTR.
        {"keys": ["trouver un artisan"], "clicks": 120, "impressions": 9000,
         "ctr": 0.013, "position": 4.0},
    ]
    opps = assistant._seo_opportunities(rows)
    keys = [o["key"] for o in opps]
    assert "devis electricien lyon" in keys
    assert "standard telephonique artisan" in keys
    assert "plombier paris" not in keys
    assert "mot rare" not in keys
    assert "trouver un artisan" not in keys
    # Ranked by CTR/position gap, not raw impressions: position 6 at 0 % CTR
    # outranks position 14 at 0.1 % CTR. CTR is normalised to percent.
    assert opps[0]["key"] == "standard telephonique artisan"
    assert opps[0]["ctr"] < 1
    assert opps[0]["ctr_position_gap"] > opps[1]["ctr_position_gap"]


def test_seo_opportunities_accepts_dashboard_page_rows():
    """dashboard_payload exposes ``label`` and CTR already in percent."""
    rows = [
        {"label": "https://www.pilotcore.fr/", "clicks": 8, "impressions": 400,
         "ctr": 2.0, "position": 3.0},
        {"label": "https://www.pilotcore.fr/pro", "clicks": 40, "impressions": 200,
         "ctr": 20.0, "position": 2.0},
    ]
    opps = assistant._seo_opportunities(rows, limit=2)
    assert [o["key"] for o in opps] == ["https://www.pilotcore.fr/"]
    assert opps[0]["ctr"] == 2.0
    assert opps[0]["ctr_position_gap"] > 0


def test_analyze_traffic_tool_runs_on_empty_db(app):
    with app.app_context():
        result = assistant.tool_analyze_traffic(None, days=30)
        assert result["ok"] is True
        assert result["range_days"] == 30
        assert "audience" in result and "top_pages" in result


def test_analyze_seo_reports_disconnected_gracefully(app):
    with app.app_context():
        result = assistant.tool_analyze_seo(None)
        assert result["ok"] is False
        assert result["connected"] is False
        assert "Search Console" in result["error"]


def test_system_health_tool(app):
    with app.app_context():
        result = assistant.tool_system_health(None)
        assert result["ok"] is True
        assert "missing_required" in result
        assert "integrations" in result
