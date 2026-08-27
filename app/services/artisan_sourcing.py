"""Bulk sourcing of real artisan prospects from French open data.

Why this exists
---------------
:mod:`app.services.prospecting` finds artisans one search at a time: a query,
a handful of websites, an e-mail harvested from a contact page. That is fine to
top up a city, and hopeless for filling a mailing list — a hundred contacts
would take a hundred searches and still land a pile of ``contact@`` catch-alls.

This module goes to the source instead. ADEME publishes the national register of
RGE-certified building companies (*Reconnu Garant de l'Environnement*) as open
data under the Licence Ouverte, and — unlike the INSEE Sirene base — the records
carry a **contact e-mail the company itself supplied for the public register**.
That is ~160 000 building professionals, already qualified:

* they are artisans du bâtiment, not marketplaces or lead brokers;
* ``particulier: true`` means they declared they work for homeowners — exactly
  the trade that loses money on a missed call, which is what PilotCore sells;
* a live certification means a real, currently-trading business.

Boundaries, because sending to sourced contacts is regulated
------------------------------------------------------------
* Only professional contact details from a public register are stored. No
  personal data beyond what ADEME publishes for contact purposes.
* An e-mail is mandatory. A record without one is not a prospect for a mailing
  campaign, so it is dropped rather than stored "for later".
* Every address is run through :mod:`app.services.email_validation` before it is
  written, so an obvious bounce never enters the sending pool.
* Anyone already known — an existing prospect, an existing account, someone who
  opted out — is skipped. Opt-out is checked first and is terminal.
* Bureaux d'études, architects and auditors are *not* artisans; their domains
  are deliberately absent from the mapping below and their records are ignored.
"""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import Iterator

import requests

from app.core.extensions import db
from app.models.outreach_prospect import OutreachProspect
from app.services.email_validation import check_recipient

logger = logging.getLogger(__name__)

API = "https://data.ademe.fr/data-fair/api/v1/datasets/liste-des-entreprises-rge-2/lines"
_UA = "PilotCore-ArtisanSourcing/1.0 (+https://www.pilotcore.fr)"
_PAGE_SIZE = 1000          # data-fair maximum for a single page
_MAX_PAGES = 60            # hard stop, so a bad filter can never loop forever
_SOURCE = "rge_ademe"

_FIELDS = (
    "siret,nom_entreprise,email,telephone,site_internet,"
    "adresse,code_postal,commune,domaine,particulier,lien_date_fin"
)

# ADEME "domaine de travaux" -> our trade key. Only hands-on building trades are
# mapped; study/audit/architecture domains are intentionally missing so they are
# filtered out (they are not artisans and would pollute the campaigns).
DOMAIN_TO_TRADE: dict[str, str] = {
    # Heating
    "Pompe à chaleur : chauffage": "chauffagiste",
    "Pompe à chaleur : eau chaude sanitaire": "chauffagiste",
    "Chaudière condensation ou micro-cogénération gaz ou fioul": "chauffagiste",
    "Chaudière bois": "chauffagiste",
    "Poêle ou insert bois": "chauffagiste",
    "Chauffe-Eau Thermodynamique": "plombier",
    "Chauffage et/ou eau chaude solaire": "plombier",
    # Air handling
    "Ventilation mécanique": "climaticien",
    # Electricity
    "Panneaux solaires photovoltaïques": "electricien",
    "Radiateurs électriques, dont régulation.": "electricien",
    # Joinery
    "Fenêtres, volets, portes donnant sur l'extérieur": "menuisier",
    "Fenêtres de toit": "menuisier",
    # Roofing
    "Isolation des toitures terrasses ou des toitures par l'extérieur": "couvreur",
    "Isolation des combles perdus": "couvreur",
    # Masonry / facade
    "Isolation des murs par l'extérieur": "peintre",
    "Isolation par l'intérieur des murs ou rampants de toitures  ou plafonds": "macon",
    "Isolation des planchers bas": "macon",
    "Projet complet de rénovation": "macon",
}

# Generic mailboxes are still the artisan's own professional address, but a
# named one converts better, so we keep the confidence signal honest.
_GENERIC_LOCALPARTS = {
    "contact", "info", "infos", "accueil", "commercial", "devis", "secretariat",
    "administration", "admin", "direction", "service", "sav", "bureau", "agence",
}


class SourcingError(RuntimeError):
    pass


def trades_available() -> list[str]:
    """Trade keys this source can actually deliver."""
    seen: list[str] = []
    for trade in DOMAIN_TO_TRADE.values():
        if trade not in seen:
            seen.append(trade)
    return seen


def _domains_for(trades: list[str] | None) -> list[str]:
    if not trades:
        return list(DOMAIN_TO_TRADE)
    wanted = set(trades)
    return [d for d, t in DOMAIN_TO_TRADE.items() if t in wanted]


def _quote(value: str) -> str:
    """Quote a term for the Lucene query string used by data-fair."""
    return '"' + value.replace('"', ' ') + '"'


def _build_query(*, trades: list[str] | None, departments: list[str] | None) -> str:
    # Foreign companies certified to work in France sit in the same register,
    # with ``code_postal`` "00000". PilotCore answers a French phone line for
    # French homeowners, so mailing a Belgian or Portuguese firm is off-target
    # and only costs sending reputation.
    clauses = ["email:*", "particulier:true", 'NOT code_postal:"00000"']

    domains = _domains_for(trades)
    if not domains:
        raise SourcingError("Aucun métier disponible dans cette source.")
    clauses.append("domaine:(" + " OR ".join(_quote(d) for d in domains) + ")")

    depts = [d.strip() for d in (departments or []) if d and d.strip()]
    if depts:
        prefixes = []
        for dept in depts:
            # 2A/2B (Corse) and the overseas 3-digit codes share the same rule:
            # the department code is the prefix of the postal code.
            prefixes.append(f"{re.sub(r'[^0-9AB]', '', dept.upper())}*")
        clauses.append("code_postal:(" + " OR ".join(prefixes) + ")")

    return " AND ".join(clauses)


def _iter_rows(query: str) -> Iterator[dict]:
    """Yield register rows page by page, following data-fair's ``next`` cursor."""
    url: str | None = API
    params: dict | None = {
        "size": _PAGE_SIZE,
        "select": _FIELDS,
        "qs": query,
        # Sorting by SIRET walks the register in registration order, which
        # front-loads the oldest and the foreign records — a batch of 200 came
        # back clustered instead of national. ``_rand`` is the dataset's own
        # shuffle column: stable, paginable, and evenly spread across France.
        "sort": "_rand",
    }
    for _ in range(_MAX_PAGES):
        if not url:
            return
        try:
            resp = requests.get(url, params=params, timeout=45, headers={"User-Agent": _UA})
        except requests.RequestException as exc:
            raise SourcingError(f"Registre RGE injoignable : {exc}") from exc
        if resp.status_code == 429:
            raise SourcingError("Registre RGE : quota atteint, réessayez plus tard.")
        if resp.status_code >= 400:
            raise SourcingError(f"Registre RGE : HTTP {resp.status_code}")
        try:
            payload = resp.json()
        except ValueError as exc:
            raise SourcingError("Registre RGE : réponse illisible.") from exc

        rows = payload.get("results") or []
        if not rows:
            return
        yield from rows

        # The cursor URL already carries every parameter — passing them again
        # would duplicate the query string and break the ``after`` token.
        url = payload.get("next")
        params = None


def _clean_company(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip())[:255]


def _clean_city(commune: str) -> str:
    city = re.sub(r"\s+", " ", (commune or "").strip())
    if city.isupper():
        # The register shouts every city name; a mail-merge should not.
        city = city.title().replace("-Sur-", "-sur-").replace("-Le-", "-le-")
    return city[:100]


_FRENCH_POSTAL_RE = re.compile(r"^(0[1-9]|[1-8]\d|9[0-8])\d{3}$")


def _is_french(row: dict) -> bool:
    """France (métropole + DOM) only — the register also lists EU firms."""
    return bool(_FRENCH_POSTAL_RE.match((row.get("code_postal") or "").strip()))


def _certification_live(row: dict, today: date) -> bool:
    raw = (row.get("lien_date_fin") or "").strip()
    if not raw:
        return True  # nothing declared — do not punish the record
    try:
        return date.fromisoformat(raw[:10]) >= today
    except ValueError:
        return True


def _confidence(email: str) -> str:
    local = email.split("@", 1)[0].lower()
    local = re.sub(r"[._-]?\d+$", "", local)
    return "medium" if local in _GENERIC_LOCALPARTS else "high"


def _known_emails() -> set[str]:
    """Every address we must not source again: prospects and account holders.

    Team test addresses are in here too — a cold campaign must never go out to
    our own inbox, whether or not the test account currently exists.
    """
    from app.models.user import User
    from app.services import internal_accounts

    known: set[str] = set(internal_accounts.internal_emails())
    for (email,) in db.session.query(OutreachProspect.email).filter(
        OutreachProspect.email.isnot(None), OutreachProspect.email != ""
    ):
        known.add(email.lower())
    for (email,) in db.session.query(User.email).filter(User.email.isnot(None)):
        known.add(email.lower())
    return known


def preview_available(*, trades=None, departments=None) -> int:
    """How many register rows match, before dedup — for the admin UI."""
    query = _build_query(trades=trades, departments=departments)
    try:
        resp = requests.get(
            API,
            params={"size": 0, "qs": query},
            timeout=30,
            headers={"User-Agent": _UA},
        )
        resp.raise_for_status()
        return int(resp.json().get("total") or 0)
    except (requests.RequestException, ValueError) as exc:
        raise SourcingError(f"Registre RGE injoignable : {exc}") from exc


def source_artisans(
    *,
    target: int = 200,
    trades: list[str] | None = None,
    departments: list[str] | None = None,
    status: str = "ready",
) -> dict:
    """Import up to ``target`` artisan prospects that each have a usable e-mail.

    Returns a report the admin console and the CLI both render.
    """
    target = max(1, min(int(target or 200), 1000))
    query = _build_query(trades=trades, departments=departments)
    today = date.today()

    known = _known_emails()
    seen_siret: set[str] = set()
    created: list[OutreachProspect] = []
    scanned = 0
    skipped = {
        "no_email": 0, "invalid_email": 0, "duplicate": 0,
        "expired": 0, "off_trade": 0, "foreign": 0,
    }
    by_trade: dict[str, int] = {}

    for row in _iter_rows(query):
        scanned += 1
        if len(created) >= target:
            break

        trade = DOMAIN_TO_TRADE.get((row.get("domaine") or "").strip())
        if not trade:
            skipped["off_trade"] += 1
            continue
        if not _is_french(row):
            skipped["foreign"] += 1
            continue
        if not _certification_live(row, today):
            skipped["expired"] += 1
            continue

        email = (row.get("email") or "").strip().lower()
        if not email:
            skipped["no_email"] += 1
            continue
        siret = re.sub(r"\D", "", row.get("siret") or "")
        if email in known or (siret and siret in seen_siret):
            skipped["duplicate"] += 1
            continue

        deliverable, reason = check_recipient(email)
        if not deliverable:
            skipped["invalid_email"] += 1
            logger.debug("RGE sourcing dropped %s — %s", email, reason)
            continue

        website = (row.get("site_internet") or "").strip() or None
        phone = re.sub(r"[\s.\-]", "", (row.get("telephone") or "").strip()) or None
        prospect = OutreachProspect(
            company_name=_clean_company(row.get("nom_entreprise") or ""),
            email=email,
            phone=phone,
            trade_type=trade,
            city=_clean_city(row.get("commune") or ""),
            postal_code=(row.get("code_postal") or "").strip()[:10] or None,
            website_url=website,
            source_url="https://data.ademe.fr/datasets/liste-des-entreprises-rge-2",
            source=_SOURCE,
            status=status,
            email_confidence=_confidence(email),
            search_query=query[:500],
            notes=(
                f"Registre RGE ADEME · {row.get('domaine')} · SIRET {siret or '—'} · "
                f"intervient chez les particuliers."
            ),
        )
        db.session.add(prospect)
        created.append(prospect)
        known.add(email)
        if siret:
            seen_siret.add(siret)
        by_trade[trade] = by_trade.get(trade, 0) + 1

    db.session.commit()
    return {
        "source": _SOURCE,
        "query": query,
        "scanned": scanned,
        "imported": len(created),
        "target": target,
        "by_trade": by_trade,
        "skipped": skipped,
        "prospects": [p.to_dict() for p in created[:50]],
    }
