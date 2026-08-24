"""Sitemap generation, split into a sitemap index.

One flat sitemap works up to 50 000 URLs, but a split index is strictly better
operationally: Search Console reports discovered/indexed counts *per child
sitemap*, so a coverage drop can be traced to "city pages" or "blog" instead of
to one opaque 6 000-URL file. Children are generated on demand — nothing is
cached to disk, so a newly published guide or artisan shows up on the next
crawl.

Every URL emitted here has already cleared ``app.utils.indexability``: a
sitemap is a statement that a page is worth indexing, so listing noindex URLs
would contradict the gate and waste crawl budget.
"""
from __future__ import annotations

from datetime import date
from xml.sax.saxutils import escape

Url = tuple[str, str, str, str | None]  # (path, changefreq, priority, lastmod)

SECTIONS = ("core", "trades", "cities", "departments", "artisans", "entreprises", "blog")


def _today() -> str:
    return date.today().isoformat()


def _guided_trades() -> set[str]:
    from app.models.trade_guide import TradeGuide

    return {g.trade_key for g in TradeGuide.query.with_entities(TradeGuide.trade_key).all()}


def _artisan_index() -> tuple[dict[str, int], set[tuple[str, str]]]:
    """``({trade: count}, {(trade, city_slug)})`` in a single pass."""
    from app.constants.cities import city_slugify
    from app.services.artisan_directory import list_public_artisans

    by_trade: dict[str, int] = {}
    by_city: set[tuple[str, str]] = set()
    for tenant in list_public_artisans(limit=2000):
        key = (tenant.trade_type or "").strip().lower()
        if not key:
            continue
        by_trade[key] = by_trade.get(key, 0) + 1
        if tenant.city:
            by_city.add((key, city_slugify(tenant.city)))
    return by_trade, by_city


def core_urls() -> list[Url]:
    today = _today()
    return [
        ("", "daily", "1.0", today),
        ("/artisans", "daily", "0.95", today),
        ("/trouver-un-artisan", "weekly", "0.9", today),
        ("/depannage-urgent", "weekly", "0.9", today),
        ("/prix-artisans", "weekly", "0.9", today),
        ("/prendre-rdv-artisan-en-ligne", "weekly", "0.9", today),
        ("/artisans/ma-fiche", "weekly", "0.8", today),
        ("/pro", "weekly", "0.9", today),
        ("/contact", "monthly", "0.5", today),
        ("/blog", "daily", "0.85", today),
        ("/mentions-legales", "yearly", "0.3", None),
        ("/confidentialite", "yearly", "0.3", None),
        ("/cgu", "yearly", "0.3", None),
        ("/cookies", "yearly", "0.3", None),
    ]


def trade_urls() -> list[Url]:
    from app.constants.trades import TRADES
    from app.utils.indexability import is_indexable, trade_pillar_robots

    guided = _guided_trades()
    by_trade, _ = _artisan_index()
    today = _today()
    out: list[Url] = []
    for trade in (k for k in TRADES if k != "autre"):
        robots = trade_pillar_robots(
            has_trade_guide=trade in guided, artisan_count=by_trade.get(trade, 0)
        )
        if is_indexable(robots):
            out.append((f"/artisans/metier/{trade}", "weekly", "0.85", today))
    return out


def city_urls() -> list[Url]:
    from app.constants.cities import CITY_ROWS, city_info
    from app.constants.trades import SEO_LOCAL_TRADES
    from app.utils.indexability import city_page_robots, is_indexable

    guided = _guided_trades()
    _, by_city = _artisan_index()
    today = _today()
    out: list[Url] = []
    for trade in SEO_LOCAL_TRADES:
        has_guide = trade in guided
        for row in CITY_ROWS:
            slug = row[0]
            robots = city_page_robots(
                artisan_count=1 if (trade, slug) in by_city else 0,
                has_trade_guide=has_guide,
                city=city_info(slug),
            )
            if is_indexable(robots):
                out.append((f"/artisans/{trade}/{slug}", "weekly", "0.7", today))
    return out


def department_urls() -> list[Url]:
    from app.constants.cities import cities_in_department, is_known_city
    from app.constants.departments import DEPARTMENTS
    from app.constants.trades import SEO_LOCAL_TRADES
    from app.utils.indexability import department_page_robots, is_indexable

    guided = _guided_trades()
    today = _today()
    # Precompute once: the city count per department drives the gate.
    counts = {code: len(cities_in_department(code)) for code, _s, _n, _c in DEPARTMENTS}
    out: list[Url] = []
    for trade in SEO_LOCAL_TRADES:
        has_guide = trade in guided
        for code, slug, _name, _chef in DEPARTMENTS:
            # Paris' department page 301s to the city page — never list a redirect.
            if is_known_city(slug):
                continue
            robots = department_page_robots(
                artisan_count=0, has_trade_guide=has_guide, city_count=counts[code]
            )
            if is_indexable(robots):
                out.append((f"/artisans/{trade}/departement/{slug}", "weekly", "0.65", today))
    return out


def artisan_urls() -> list[Url]:
    from app.services.artisan_directory import list_public_artisans
    from app.utils.seo import format_lastmod

    out: list[Url] = []
    for tenant in list_public_artisans(limit=2000):
        if tenant.public_slug:
            out.append(
                (
                    f"/artisans/{tenant.public_slug}",
                    "weekly",
                    "0.8",
                    format_lastmod(tenant.created_at),
                )
            )
    return out


def blog_urls() -> list[Url]:
    from app.models.blog_category import BlogCategory
    from app.models.blog_post import BlogPost
    from app.models.site_page import SitePage
    from app.utils.seo import format_lastmod

    today = _today()
    out: list[Url] = []
    pages = (
        SitePage.query.filter_by(status="published")
        .order_by(SitePage.updated_at.desc())
        .limit(100)
        .all()
    )
    for page in pages:
        if page.slug:
            out.append((f"/p/{page.slug}", "weekly", "0.7", format_lastmod(page.updated_at)))
    posts = (
        BlogPost.query.filter_by(status="published")
        .order_by(BlogPost.published_at.desc())
        .limit(1000)
        .all()
    )
    for post in posts:
        if post.slug:
            out.append(
                (
                    f"/blog/{post.slug}",
                    "weekly",
                    "0.8",
                    format_lastmod(post.published_at or post.updated_at),
                )
            )
    for cat in BlogCategory.query.order_by(BlogCategory.sort_order).all():
        out.append((f"/blog/categorie/{cat.slug}", "weekly", "0.75", today))
    return out


def entreprise_urls() -> list[Url]:
    """Individual registry-listing pages that cleared the indexing gate.

    Deliberately the strictest section: only businesses trading long enough and
    sitting in a town the directory already covers. Everything else is served
    noindex and stays out of here.
    """
    from app.constants.cities import city_info
    from app.models.registry_listing import STATUS_LISTED, RegistryListing
    from app.utils.indexability import is_indexable, listing_page_robots
    from app.utils.seo import format_lastmod

    out: list[Url] = []
    rows = (
        RegistryListing.query.filter_by(status=STATUS_LISTED)
        .order_by(RegistryListing.date_creation.asc().nullslast())
        .limit(50000)
        .all()
    )
    for listing in rows:
        city = city_info(listing.city_slug) if listing.city_slug else None
        robots = listing_page_robots(listing, city_has_page=city is not None)
        if is_indexable(robots):
            out.append(
                (
                    f"/artisans/entreprise/{listing.siren}",
                    "monthly",
                    "0.5",
                    format_lastmod(listing.updated_at),
                )
            )
    return out


_BUILDERS = {
    "core": core_urls,
    "trades": trade_urls,
    "cities": city_urls,
    "departments": department_urls,
    "artisans": artisan_urls,
    "entreprises": entreprise_urls,
    "blog": blog_urls,
}


def section_urls(section: str) -> list[Url]:
    builder = _BUILDERS.get(section)
    return builder() if builder else []


def all_urls() -> list[Url]:
    out: list[Url] = []
    for section in SECTIONS:
        out.extend(section_urls(section))
    return out


def render_urlset(urls: list[Url]) -> str:
    from app.utils.seo import site_base_url

    base = site_base_url()
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for path, freq, priority, lastmod in urls:
        lines.append("  <url>")
        lines.append(f"    <loc>{escape(base + path)}</loc>")
        if lastmod:
            lines.append(f"    <lastmod>{lastmod}</lastmod>")
        lines.append(f"    <changefreq>{freq}</changefreq>")
        lines.append(f"    <priority>{priority}</priority>")
        lines.append("  </url>")
    lines.append("</urlset>")
    return "\n".join(lines)


def render_index() -> str:
    """The sitemap index. Empty sections are omitted — an index entry pointing
    at a zero-URL sitemap is reported as an error in Search Console."""
    from app.utils.seo import site_base_url

    base = site_base_url()
    today = _today()
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for section in SECTIONS:
        if not section_urls(section):
            continue
        lines.append("  <sitemap>")
        lines.append(f"    <loc>{escape(f'{base}/sitemap-{section}.xml')}</loc>")
        lines.append(f"    <lastmod>{today}</lastmod>")
        lines.append("  </sitemap>")
    lines.append("</sitemapindex>")
    return "\n".join(lines)
