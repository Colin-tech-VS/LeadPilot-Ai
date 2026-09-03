"""La sonde de deploy doit voir la boucle que la CI laissait passer.

Le 3 septembre 2026 https://www.pilotcore.fr/ répondait ``301`` vers
elle-même. L'ancienne étape « Verify public endpoints » n'exigeait qu'un code
dans (200, 301, 302, 308) et une ``Location`` non RFC1918 : elle passait au
vert sur un site totalement injoignable. Ces tests figent le contraire.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from check_public_endpoints import Check, follow  # noqa: E402

WWW = "https://www.pilotcore.fr"
APEX = "https://pilotcore.fr"


def fake_fetch(routes):
    """Un serveur en dur : url -> (code, headers, corps)."""

    def _fetch(url):
        if url not in routes:
            raise AssertionError(f"URL non prévue par le test : {url}")
        return routes[url]

    return _fetch


def redirect(location, code=301):
    return code, {"Location": location}, b""


def ok(body=b"<html></html>"):
    return 200, {}, body


def test_self_redirect_is_a_failure():
    """La panne réelle : www renvoie 301 vers www, à l'identique."""
    fetch = fake_fetch({f"{WWW}/": redirect(f"{WWW}/")})

    outcome = follow(f"{WWW}/", fetch=fetch)

    assert outcome.error is not None
    assert "soi-même" in outcome.error
    assert outcome.final_status is None


def test_self_redirect_is_caught_without_trailing_slash():
    """``https://www.pilotcore.fr`` et ``.../`` sont la même ressource."""
    fetch = fake_fetch({f"{WWW}/": redirect(WWW)})

    outcome = follow(f"{WWW}/", fetch=fetch)

    assert outcome.error is not None
    assert "soi-même" in outcome.error


def test_ping_pong_between_hosts_is_a_failure():
    """apex → www → apex : la boucle historique www ↔ apex."""
    fetch = fake_fetch(
        {
            f"{APEX}/": redirect(f"{WWW}/"),
            f"{WWW}/": redirect(f"{APEX}/"),
        }
    )

    outcome = follow(f"{APEX}/", fetch=fetch)

    assert outcome.error is not None
    assert "cycle" in outcome.error


def test_private_location_is_a_failure():
    """Une Location RFC1918 exposée publiquement reste refusée."""
    fetch = fake_fetch({f"{APEX}/": redirect("https://10.100.4.106/")})

    outcome = follow(f"{APEX}/", fetch=fetch)

    assert outcome.error is not None
    assert "privée" in outcome.error


def test_single_hop_to_the_other_host_is_accepted():
    """Une redirection apex → www qui aboutit n'est pas une boucle."""
    fetch = fake_fetch({f"{APEX}/": redirect(f"{WWW}/"), f"{WWW}/": ok()})

    outcome = follow(f"{APEX}/", fetch=fetch)

    assert outcome.error is None
    assert outcome.final_status == 200
    assert outcome.final_url == f"{WWW}/"


def test_direct_200_is_accepted():
    fetch = fake_fetch({f"{WWW}/": ok()})

    outcome = follow(f"{WWW}/", fetch=fetch)

    assert outcome.error is None
    assert outcome.final_status == 200


def test_external_redirect_is_a_terminal_not_a_loop():
    """/paiement peut partir chez Stripe : on ne suit pas, on n'échoue pas."""
    fetch = fake_fetch({f"{APEX}/paiement": redirect("https://checkout.stripe.com/x", code=302)})

    outcome = follow(f"{APEX}/paiement", fetch=fetch)

    assert outcome.error is None
    assert outcome.left_public_hosts is True


def test_long_chain_is_a_failure():
    """Cinq sauts distincts sans arrivée : traité comme une boucle."""
    routes = {f"{WWW}/{i}": redirect(f"{WWW}/{i + 1}") for i in range(12)}
    fetch = fake_fetch(routes)

    outcome = follow(f"{WWW}/0", fetch=fetch)

    assert outcome.error is not None
    assert "redirections" in outcome.error


def test_check_reports_the_loop_with_its_chain():
    """Le message d'échec doit montrer la chaîne, pas seulement le code."""
    fetch = fake_fetch({f"{WWW}/": redirect(f"{WWW}/")})

    failures = Check(f"{WWW}/").run(fetch=fetch)

    assert len(failures) == 1
    assert "soi-même" in failures[0]
    assert f"301 {WWW}/" in failures[0]


def test_check_rejects_the_old_green_light():
    """Un 301 vers soi-même passait l'ancienne sonde ; il échoue désormais."""
    fetch = fake_fetch({f"{APEX}/api/health": redirect(f"{APEX}/api/health")})

    failures = Check(f"{APEX}/api/health", expect_json={"status": "ok"}).run(fetch=fetch)

    assert failures, "un 301 vers soi-même doit faire échouer le deploy"


def test_check_validates_health_payload():
    fetch = fake_fetch({f"{APEX}/api/health": (200, {}, b'{"status": "ok"}')})

    failures = Check(f"{APEX}/api/health", expect_json={"status": "ok"}).run(fetch=fetch)

    assert failures == []


def test_check_rejects_wrong_health_payload():
    fetch = fake_fetch({f"{APEX}/api/health": (200, {}, b'{"status": "degraded"}')})

    failures = Check(f"{APEX}/api/health", expect_json={"status": "ok"}).run(fetch=fetch)

    assert failures
    assert "JSON inattendu" in failures[0]


def test_unreachable_host_is_reported_not_swallowed():
    def _boom(url):
        raise OSError("connection reset by peer")

    outcome = follow(f"{WWW}/", fetch=_boom)

    assert outcome.error is not None
    assert "requête impossible" in outcome.error


@pytest.mark.parametrize("code", [301, 302, 307, 308])
def test_every_redirect_code_loops_the_same_way(code):
    """Le 308 boucle autant que le 301 : le code ne change rien au diagnostic."""
    fetch = fake_fetch({f"{WWW}/": redirect(f"{WWW}/", code=code)})

    outcome = follow(f"{WWW}/", fetch=fetch)

    assert outcome.error is not None
    assert "soi-même" in outcome.error
