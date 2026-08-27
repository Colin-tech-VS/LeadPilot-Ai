"""SEO of the pages that acquire artisans.

/pro is the only page in the funnel that has to be *found* — the rest are
reached from an ad, a link or a session. Its technical SEO was already sound
(canonical, hreflang, four JSON-LD blocks, 2000+ words); the gap was the one
signal Google weighs most and it was the only page missing it.
"""
import re

import pytest

PRO_PAGES = ["/pro", "/50-artisans"]


def _head(client, path):
    return client.get(path).get_data(as_text=True)


def _tag(html, pattern):
    m = re.search(pattern, html, re.I | re.S)
    return m.group(1).strip() if m else None


# ── The title, which is where the acquisition happens ────────────────────────


def test_the_pro_title_leads_with_what_an_artisan_searches(client):
    """« Ne ratez plus aucun appel » is a good ad headline and ranks for
    nothing. The search term goes first, the hook second, the brand last."""
    title = _tag(_head(client, "/pro"), r"<title>(.*?)</title>")
    assert title
    assert re.search(r"secr[ée]tariat t[ée]l[ée]phonique", title, re.I), title
    assert "artisan" in title.lower()


def test_every_pro_page_is_branded(client):
    """/pro was the only page in the whole site whose title had no brand."""
    for path in PRO_PAGES + ["/register", "/login"]:
        title = _tag(_head(client, path), r"<title>(.*?)</title>")
        assert title and "PilotCore" in title, f"{path}: {title}"


def test_the_keyword_comes_before_the_brand(client):
    """A truncated SERP entry must still show the term, not just the name."""
    title = _tag(_head(client, "/pro"), r"<title>(.*?)</title>")
    assert title.lower().index("artisan") < title.index("PilotCore")


# ── The rest of the head ─────────────────────────────────────────────────────


@pytest.mark.parametrize("path", PRO_PAGES)
def test_the_page_is_indexable_and_self_canonical(client, path):
    html = _head(client, path)
    robots = _tag(html, r'<meta name="robots" content="(.*?)"')
    assert robots and "noindex" not in robots, f"{path} is not indexable"

    canonical = _tag(html, r'<link rel="canonical" href="(.*?)"')
    assert canonical and canonical.rstrip("/").endswith(path), f"{path}: {canonical}"


@pytest.mark.parametrize("path", PRO_PAGES)
def test_the_description_fits_a_search_result(client, path):
    """Under ~120 chars wastes the slot; much over ~160 is cut mid-sentence."""
    desc = _tag(_head(client, path), r'<meta name="description" content="(.*?)"')
    assert desc, f"{path} has no description"
    assert 110 <= len(desc) <= 175, f"{path}: {len(desc)} chars — {desc[:80]}"


@pytest.mark.parametrize("path", PRO_PAGES)
def test_there_is_exactly_one_h1(client, path):
    html = _head(client, path)
    assert len(re.findall(r"<h1[\s>]", html)) == 1, f"{path} needs exactly one h1"


def test_the_pro_page_declares_what_it_is_to_a_search_engine(client):
    """SoftwareApplication + FAQPage are what earn the rich result."""
    html = _head(client, "/pro")
    blocks = re.findall(r'application/ld\+json[^>]*>(.*?)</script>', html, re.S)
    assert blocks, "no structured data on /pro"
    types = set()
    for raw in blocks:
        import json

        try:
            data = json.loads(raw)
        except ValueError:
            pytest.fail("a JSON-LD block on /pro is not valid JSON")
        for node in data if isinstance(data, list) else [data]:
            for item in node.get("@graph", [node]):
                if isinstance(item, dict) and item.get("@type"):
                    types.add(item["@type"])
    assert "SoftwareApplication" in types
    assert "FAQPage" in types


def test_the_acquisition_pages_are_in_the_sitemap(client):
    body = client.get("/sitemap-core.xml").get_data(as_text=True)
    for path in PRO_PAGES:
        assert f"<loc>https://www.pilotcore.fr{path}</loc>" in body, path
