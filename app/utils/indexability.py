"""Quality gate deciding which programmatic pages Google may index.

Programmatic local SEO generates thousands of URLs from a handful of
templates. Google's doorway-page guidance is explicit: a large set of
near-identical pages built to funnel visitors into the same destination is a
manual-action risk, and the penalty lands on the whole domain, not just the
thin URLs.

So we gate. A generated page is only submitted for indexing once it carries
something a searcher could not get from its siblings:

* at least one real artisan listed, or
* a trade guide (unique long-form body + FAQ) *and* enough local substance —
  population, postal codes, neighbouring towns — to differentiate it.

Everything below the bar is served ``noindex, follow``: still crawlable, still
passing link equity to the pages that do deserve to rank, but never counted as
thin inventory. Pages graduate automatically — publish a trade guide, or sign
one artisan in the city, and the gate opens on the next crawl with no code
change and no sitemap surgery.
"""
from __future__ import annotations

# A city needs enough people in it for a dedicated trade page to answer real,
# recurring demand. Below this we would be generating a page for a query that
# barely exists — the definition of thin.
MIN_CITY_POPULATION = 30_000

INDEX = "index, follow"
NOINDEX = "noindex, follow"


def city_page_robots(
    *,
    artisan_count: int,
    has_trade_guide: bool,
    city: dict | None,
    listing_count: int = 0,
) -> str:
    """Robots directive for ``/artisans/<trade>/<city>``.

    Registry listings count as substance alongside registered artisans: a page
    naming the actual businesses of that trade in that town answers the query,
    whether or not any of them has signed up yet.
    """
    if artisan_count > 0 or listing_count > 0:
        return INDEX
    if not has_trade_guide or city is None:
        return NOINDEX
    if (city.get("population") or 0) < MIN_CITY_POPULATION:
        return NOINDEX
    return INDEX


def department_page_robots(*, artisan_count: int, has_trade_guide: bool, city_count: int) -> str:
    """Robots directive for ``/artisans/<trade>/departement/<slug>``.

    Department pages carry a genuinely unique payload even when empty — the
    list of every covered town, the chef-lieu, the postal range — so the bar is
    lower than for a city page, but a guide is still required so the page has a
    body worth reading.
    """
    if artisan_count > 0:
        return INDEX
    if has_trade_guide and city_count > 0:
        return INDEX
    return NOINDEX


def trade_pillar_robots(*, has_trade_guide: bool, artisan_count: int) -> str:
    """Robots directive for ``/artisans/metier/<trade>``.

    Only 13 of these exist and they are the hubs the whole mesh links into, so
    they stay indexable as long as they have a body.
    """
    if has_trade_guide or artisan_count > 0:
        return INDEX
    return NOINDEX


# A generated page per registered business is where programmatic SEO most
# easily tips into doorway territory: hundreds of thousands of near-identical
# shells built to funnel traffic. So the individual company page is deliberately
# the *narrowest* gate of all, and it opens in stages.
#
# Two conditions, both about the page having a reason to exist:
#   * the business has been trading long enough that a searcher looking it up is
#     looking for something real, not a shell registered last month;
#   * it sits in a town the directory already covers, so the page lands inside
#     an existing cluster instead of floating alone.
MIN_LISTING_YEARS = 5


def listing_page_robots(listing, *, city_has_page: bool, cluster_indexable: bool | None = None) -> str:
    """Robots directive for ``/artisans/entreprise/<siren>``.

    A claimed listing has its own artisan page and must never compete with it;
    an opted-out one must not be served at all.

    ``cluster_indexable`` is the second condition above, stated properly: the
    business must sit inside a cluster that exists, not merely in a town on our
    list. ``city_has_page`` only ever meant « the slug resolves », which let a
    company page float alone in a trade × city cluster that is itself noindex —
    twelve thousand islands against nine pages written for the buyer, all
    competing for the same crawl. When it is not supplied the old, looser rule
    applies, so a caller that cannot cheaply know the cluster's state (the page
    view itself) is not forced to guess.
    """
    from app.models.registry_listing import STATUS_LISTED

    if listing is None or listing.status != STATUS_LISTED:
        return NOINDEX
    if not city_has_page:
        return NOINDEX
    if cluster_indexable is False:
        return NOINDEX
    years = listing.years_active
    if years is None or years < MIN_LISTING_YEARS:
        return NOINDEX
    return INDEX


def is_indexable(robots: str) -> bool:
    return not robots.lower().startswith("noindex")
