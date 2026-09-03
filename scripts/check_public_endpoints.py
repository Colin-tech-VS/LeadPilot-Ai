#!/usr/bin/env python3
"""Sonde publique : un 301 vers soi-même est une panne, pas un succès.

L'étape « Verify public endpoints » du workflow de deploy acceptait n'importe
quel 3xx dont la ``Location`` n'était pas une adresse RFC1918. Le 3 septembre
2026 la production répondait ``301 Location: https://www.pilotcore.fr/`` **sur
https://www.pilotcore.fr/ lui-même** — une boucle infinie (``TooManyRedirects``
dans le navigateur, site injoignable) que le déploiement affichait au vert.
Six commits de « correctif » puis rollback ont suivi, chacun validé par une CI
qui ne regardait jamais la cible de la redirection.

Ce script suit la chaîne à la main et échoue sur ce qu'un simple code HTTP ne
dit pas :

* un saut vers l'URL courante (self-redirect) — la boucle du 3 septembre ;
* un cycle (A → B → A) ;
* une chaîne plus longue que ``MAX_HOPS`` ;
* une ``Location`` pointant vers une adresse privée (RFC1918 / loopback), qui
  fuiterait l'adressage interne au client.

Une redirection qui quitte les hôtes publics (Stripe, Google) est un point
d'arrivée légitime : on ne la suit pas, on ne la juge pas.

Usage : ``python scripts/check_public_endpoints.py`` — code de sortie non nul
dès qu'un point d'entrée est en défaut.
"""
from __future__ import annotations

import ipaddress
import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

WWW_ORIGIN = "https://www.pilotcore.fr"
APEX_ORIGIN = "https://pilotcore.fr"
PUBLIC_HOSTS = {"pilotcore.fr", "www.pilotcore.fr"}

MAX_HOPS = 5
TIMEOUT = 30
USER_AGENT = "PilotCore-deploy-probe/1.0"


@dataclass
class Hop:
    """Un maillon de la chaîne de redirection."""

    url: str
    status: int
    location: str | None = None

    def __str__(self) -> str:
        arrow = f" -> {self.location}" if self.location else ""
        return f"{self.status} {self.url}{arrow}"


@dataclass
class Outcome:
    """Résultat du parcours d'une chaîne."""

    chain: list[Hop] = field(default_factory=list)
    final_status: int | None = None
    final_url: str | None = None
    body: bytes = b""
    left_public_hosts: bool = False
    error: str | None = None

    def render_chain(self) -> str:
        return "\n".join(f"    {hop}" for hop in self.chain) or "    (chaîne vide)"


def _host_of(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host


def _is_private_host(host: str) -> bool:
    """Une Location vers du RFC1918 / loopback ne doit jamais sortir en public."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved)


def _normalize(url: str) -> str:
    """Compare les URL sans se laisser piéger par un « / » final absent.

    ``https://www.pilotcore.fr`` et ``https://www.pilotcore.fr/`` désignent la
    même ressource : sans cette normalisation, la boucle du 3 septembre passe
    entre les mailles quand l'un des deux côtés omet la barre oblique.
    """
    parts = urlsplit(url)
    scheme = (parts.scheme or "https").lower()
    host = (parts.hostname or "").lower()
    port = parts.port
    if port and not ((scheme == "https" and port == 443) or (scheme == "http" and port == 80)):
        host = f"{host}:{port}"
    path = parts.path or "/"
    query = f"?{parts.query}" if parts.query else ""
    return f"{scheme}://{host}{path}{query}"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """On suit la chaîne nous-mêmes : urllib ne doit rien avaler."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


def http_fetch(url: str) -> tuple[int, dict[str, str], bytes]:
    """Un aller simple, sans suivre la redirection."""
    opener = urllib.request.build_opener(_NoRedirect)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with opener.open(request, timeout=TIMEOUT) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:  # 3xx et 4xx/5xx arrivent ici
        return exc.code, dict(exc.headers or {}), exc.read()


def follow(url: str, fetch=http_fetch, max_hops: int = MAX_HOPS) -> Outcome:
    """Parcourt la chaîne de redirection en nommant précisément la panne."""
    outcome = Outcome()
    seen: list[str] = []
    current = url

    for _ in range(max_hops + 1):
        try:
            status, headers, body = fetch(current)
        except Exception as exc:  # noqa: BLE001 — le message brut est l'information utile
            outcome.error = f"requête impossible sur {current} : {exc}"
            return outcome

        location = headers.get("Location") or headers.get("location")
        outcome.chain.append(Hop(current, status, location))
        seen.append(_normalize(current))

        if not (300 <= status < 400 and location):
            outcome.final_status = status
            outcome.final_url = current
            outcome.body = body
            return outcome

        target = urljoin(current, location)
        target_host = _host_of(target)

        if _is_private_host(target_host):
            outcome.error = (
                f"Location privée exposée publiquement : {current} renvoie vers {target}"
            )
            return outcome

        if _normalize(target) == _normalize(current):
            outcome.error = (
                f"redirection vers soi-même : {current} renvoie {status} vers {target} "
                "— boucle infinie (TooManyRedirects) pour tout navigateur"
            )
            return outcome

        if _normalize(target) in seen:
            outcome.error = f"cycle de redirection : {target} déjà visité dans cette chaîne"
            return outcome

        if target_host not in PUBLIC_HOSTS:
            # Sortie légitime (Stripe, Google…) : point d'arrivée, pas une boucle.
            outcome.final_status = status
            outcome.final_url = current
            outcome.left_public_hosts = True
            return outcome

        current = target

    outcome.error = f"plus de {max_hops} redirections depuis {url} — chaîne considérée comme bouclée"
    return outcome


@dataclass
class Check:
    """Un point d'entrée public et ce qu'on exige de lui."""

    url: str
    allowed_status: tuple[int, ...] = (200,)
    expect_json: dict | None = None

    def run(self, fetch=http_fetch) -> list[str]:
        failures: list[str] = []
        outcome = follow(self.url, fetch=fetch)

        if outcome.error:
            failures.append(f"{self.url}\n  {outcome.error}\n  chaîne :\n{outcome.render_chain()}")
            return failures

        if outcome.left_public_hosts:
            # Redirection hors du site : on n'exige pas de code final précis.
            return failures

        if outcome.final_status not in self.allowed_status:
            expected = "/".join(str(code) for code in self.allowed_status)
            failures.append(
                f"{self.url}\n  code final {outcome.final_status}, attendu {expected}"
                f"\n  chaîne :\n{outcome.render_chain()}"
            )
            return failures

        if self.expect_json is not None:
            try:
                payload = json.loads(outcome.body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                failures.append(f"{self.url}\n  réponse JSON illisible : {exc}")
                return failures
            for key, value in self.expect_json.items():
                if payload.get(key) != value:
                    failures.append(
                        f"{self.url}\n  JSON inattendu : {key}={payload.get(key)!r}, "
                        f"attendu {value!r}"
                    )

        return failures


CHECKS = (
    Check(f"{APEX_ORIGIN}/api/health", expect_json={"status": "ok"}),
    Check(f"{WWW_ORIGIN}/api/health", expect_json={"status": "ok"}),
    Check(f"{APEX_ORIGIN}/"),
    Check(f"{WWW_ORIGIN}/"),
    Check(f"{APEX_ORIGIN}/admin"),
    Check(f"{APEX_ORIGIN}/paiement"),
)

REMEDIATION = """
Une boucle 301 sur un hôte public ne se corrige pas dans ce dépôt : le code
part sur Scalingo (buildpack Python + gunicorn), qui n'applique ni apache.conf
ni nginx.conf ni .htaccess. Voir docs/PRODUCTION.md, section « Routage public ».
""".strip()


def main() -> int:
    failures: list[str] = []
    for check in CHECKS:
        print(f"→ {check.url}", flush=True)
        failures.extend(check.run())

    if failures:
        print("\nSondes publiques en échec :\n", file=sys.stderr)
        for failure in failures:
            print(f"  ✗ {failure}\n", file=sys.stderr)
        print(REMEDIATION, file=sys.stderr)
        return 1

    print("\nToutes les sondes publiques répondent sans boucle.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
