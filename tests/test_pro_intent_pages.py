"""Pages written for the artisan who buys, not for the client who searches.

Every other SEO page on this site answers « je cherche un plombier ». An artisan
never types that, so the acquisition engine and the thing being sold pointed at
different people: /pro and /50-artisans were the only two URLs addressed to the
person who pays.
"""
import html as html_lib
import re

from app.constants import pro_intents


def test_only_the_call_out_trades_get_a_page_of_their_own(client):
    """A page per trade for all thirteen would be the doorway pattern
    ``app.utils.indexability`` exists to keep this site out of. Five trades earn
    one; everything else is answered by the hub."""
    for trade in pro_intents.INTENT_TRADES:
        assert client.get(f"/secretariat-telephonique/{trade}").status_code == 200

    # A real trade with no page of its own is a 404, not a shell.
    assert client.get("/secretariat-telephonique/carreleur").status_code == 404
    assert client.get("/secretariat-telephonique/nawak").status_code == 404


def test_each_trade_page_says_something_the_others_do_not(client):
    """Unique substance is the whole justification for the set existing."""
    bodies = {}
    for trade in pro_intents.INTENT_TRADES:
        # Jinja escapes the apostrophes these sentences are full of.
        html = html_lib.unescape(client.get(f"/secretariat-telephonique/{trade}").get_data(as_text=True))
        content = pro_intents.content_for(trade, "fr")
        assert content["h1"] in html
        for line in content["calls"] + content["captured"]:
            assert line in html
        for question, answer in content["faq"]:
            assert question in html and answer in html
        bodies[trade] = content

    # No two trades share a call reason, a captured field or a question.
    for field in ("calls", "captured"):
        seen = set()
        for trade, content in bodies.items():
            for line in content[field]:
                assert line not in seen, f"{trade}: « {line} » is shared with another trade"
                seen.add(line)


def test_a_trade_page_carries_one_destination(client):
    """The landing page's old failure repeated here would undo the point of
    fixing it: one offer, one button."""
    html = client.get("/secretariat-telephonique/plombier").get_data(as_text=True)
    assert 'href="/register?src=intent-plombier&amp;trade=plombier"' in html
    assert "/50-artisans" not in html.split('class="v-footer')[0]
    # The trade is carried into the form, so the artisan lands on the account
    # step with the first question already answered.
    assert "trade=plombier" in html


def test_the_hub_links_every_trade_page_and_back(client):
    hub = client.get("/secretariat-telephonique-artisan").get_data(as_text=True)
    for trade in pro_intents.INTENT_TRADES:
        assert f'href="/secretariat-telephonique/{trade}"' in hub

    page = client.get("/secretariat-telephonique/serrurier").get_data(as_text=True)
    assert 'href="/secretariat-telephonique-artisan"' in page
    for other in pro_intents.other_trades("serrurier"):
        assert f'href="/secretariat-telephonique/{other}"' in page


def test_the_pages_are_indexable_and_in_the_sitemap(client):
    hub = client.get("/secretariat-telephonique-artisan").get_data(as_text=True)
    assert '<meta name="robots" content="index, follow">' in hub
    assert '"@type": "Service"' in hub

    page = client.get("/secretariat-telephonique/vitrier").get_data(as_text=True)
    assert '<meta name="robots" content="index, follow">' in page
    assert '"@type": "FAQPage"' in page
    assert "/secretariat-telephonique/vitrier</loc>" not in page  # not a self-listing

    sitemap = client.get("/sitemap-core.xml").get_data(as_text=True)
    assert "/secretariat-telephonique-artisan</loc>" in sitemap
    for trade in pro_intents.INTENT_TRADES:
        assert f"/secretariat-telephonique/{trade}</loc>" in sitemap


def test_the_pages_are_reachable_from_the_rest_of_the_site(client):
    """An orphan page is one a crawler and a visitor both have to be told
    about."""
    pro = client.get("/pro").get_data(as_text=True)
    assert 'href="/secretariat-telephonique-artisan"' in pro


def test_the_funnel_can_name_where_these_signups_came_from(client):
    from app.services import signup_funnel

    for trade in pro_intents.INTENT_TRADES:
        assert f"intent-{trade}" in signup_funnel.SOURCE_LABELS
    assert "intent-hub" in signup_funnel.SOURCE_LABELS


def test_the_pages_do_not_reintroduce_promises(client):
    """These are new pages; they must not quietly bring back the copy the rest
    of the site just stopped making."""
    for path in ["/secretariat-telephonique-artisan"] + [
        f"/secretariat-telephonique/{t}" for t in pro_intents.INTENT_TRADES
    ]:
        low = client.get(path).get_data(as_text=True).lower()
        assert "clients garantis" not in low
        assert "des milliers" not in low
        assert not re.search(r"\+\s?\d{2}\s?% de (prospects|clients)", low)


def test_english_falls_back_to_french_rather_than_breaking(client):
    """The search intent is French. English readers get the page, not a hole."""
    html = html_lib.unescape(
        client.get("/secretariat-telephonique/plombier?lang=en").get_data(as_text=True)
    )
    assert "Phone answering service for plumbers" in html
    assert pro_intents.content_for("plombier", "en")["stake"] in html
    # A trade with no English payload would still render its French one.
    assert pro_intents.content_for("plombier", "de") == pro_intents.content_for("plombier", "fr")


def test_the_directory_band_points_at_the_trade_intent_page(client):
    """The band sits on thousands of client-intent pages and knows their trade.

    Sending every one of them to /pro left the intent set reachable only from
    the pro nav — nine pages of internal links against six thousand — so the
    pages written for the buyer were orphans whatever the sitemap said. The
    anchor also carries the head term they are meant to rank for.
    """
    covered = client.get("/artisans/metier/plombier").get_data(as_text=True)
    assert 'href="/secretariat-telephonique/plombier"' in covered
    assert "secrétariat téléphonique pour plombier" in covered.lower()

    city = client.get("/artisans/plombier/paris").get_data(as_text=True)
    assert 'href="/secretariat-telephonique/plombier"' in city

    # A trade with no page of its own falls back to the hub rather than to a 404.
    other = client.get("/artisans/metier/carreleur").get_data(as_text=True)
    assert 'href="/secretariat-telephonique-artisan"' in other
    assert "/secretariat-telephonique/carreleur" not in other

    # And the pages with no trade context at all reach the hub too.
    for path in ("/artisans", "/prix-artisans", "/depannage-urgent"):
        html = client.get(path).get_data(as_text=True)
        assert 'href="/secretariat-telephonique-artisan"' in html, path


def test_a_company_page_is_only_indexed_inside_a_live_cluster(app):
    """Twelve thousand registry pages against nine written for the buyer, all
    drawing on the same crawl. A company floating alone in a trade × city
    cluster that is itself noindex answers nothing its siblings do not."""
    from app.utils.indexability import listing_page_robots

    class _Listing:
        status = "listed"
        years_active = 12

    from app.models.registry_listing import STATUS_LISTED

    listing = _Listing()
    listing.status = STATUS_LISTED

    assert listing_page_robots(listing, city_has_page=True, cluster_indexable=True).startswith("index")
    assert listing_page_robots(listing, city_has_page=True, cluster_indexable=False).startswith("noindex")
    # Omitted (a caller that cannot cheaply know) keeps the older, looser rule.
    assert listing_page_robots(listing, city_has_page=True).startswith("index")
    # The existing gates still bite.
    assert listing_page_robots(listing, city_has_page=False, cluster_indexable=True).startswith("noindex")
    listing.years_active = 1
    assert listing_page_robots(listing, city_has_page=True, cluster_indexable=True).startswith("noindex")
