"""Ingestion of artisan businesses from the official French company registry.

Source: ``recherche-entreprises.api.gouv.fr`` — the government's open search API
over the INSEE Sirene base. No key, no quota to speak of, Licence Ouverte.

Only two kinds of record are ever written:

* ``etat_administratif == "A"`` — the business is still active. Closed
  businesses have no place in a directory a customer will act on.
* ``statut_diffusion == "O"`` — INSEE marks the record publicly diffusible. A
  business that opted out of diffusion at the source stays out here; that
  choice is theirs, not ours to override.

A listing already marked ``opted_out`` is never revived, and a listing already
``claimed`` is never overwritten by registry data — the artisan's own edits win.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Iterator

import requests

from app.core.extensions import db
from app.models.registry_listing import (
    STATUS_CLAIMED,
    STATUS_LISTED,
    STATUS_OPTED_OUT,
    RegistryListing,
)

logger = logging.getLogger(__name__)

API = "https://recherche-entreprises.api.gouv.fr/search"
_UA = "PilotCore-DirectorySeed/1.0 (+https://www.pilotcore.fr)"
_PER_PAGE = 25          # API maximum
_MAX_PAGE = 400         # API refuses beyond page 400 (10 000 results)
_THROTTLE_S = 0.18      # stay well under the published rate limit

# NAF (APE) code -> our trade key. One code can only map to one trade; where a
# NAF bucket covers two of our trades (peinture *et* vitrerie), the dominant
# one wins and the other is served by its own guide pages.
NAF_TO_TRADE: dict[str, str] = {
    "43.22A": "plombier",
    "43.22B": "chauffagiste",
    "43.21A": "electricien",
    "43.32A": "menuisier",
    "43.32B": "serrurier",
    "43.34Z": "peintre",
    "43.33Z": "carreleur",
    "43.91A": "charpentier",
    "43.91B": "couvreur",
    "43.99C": "macon",
    "81.30Z": "paysagiste",
}
TRADE_TO_NAF: dict[str, str] = {v: k for k, v in NAF_TO_TRADE.items()}


class RegistryImportError(RuntimeError):
    pass


def _get(params: dict[str, Any]) -> dict:
    try:
        resp = requests.get(API, params=params, timeout=25, headers={"User-Agent": _UA})
    except requests.RequestException as exc:
        raise RegistryImportError(f"Registre injoignable : {exc}") from exc
    if resp.status_code == 429:
        raise RegistryImportError("Registre : quota atteint, réessayer plus tard.")
    if resp.status_code >= 400:
        raise RegistryImportError(f"Registre : HTTP {resp.status_code}")
    try:
        return resp.json()
    except ValueError as exc:
        raise RegistryImportError("Registre : réponse illisible.") from exc


def iter_registry(
    *, naf: str, dept: str | None = None, postal_code: str | None = None, max_records: int = 500
) -> Iterator[dict]:
    """Yield raw registry records for a NAF code, filtered by area."""
    seen = 0
    page = 1
    while seen < max_records and page <= _MAX_PAGE:
        params: dict[str, Any] = {
            "activite_principale": naf,
            "etat_administratif": "A",
            "per_page": _PER_PAGE,
            "page": page,
        }
        if dept:
            params["departement"] = dept
        if postal_code:
            params["code_postal"] = postal_code
        data = _get(params)
        results = data.get("results") or []
        if not results:
            return
        for record in results:
            yield record
            seen += 1
            if seen >= max_records:
                return
        if len(results) < _PER_PAGE:
            return
        page += 1
        time.sleep(_THROTTLE_S)


def _extract(record: dict, trade_key: str) -> dict | None:
    """Map a registry record to listing fields, or None if it must be skipped."""
    from app.constants.cities import city_slugify

    # Respect the source's own diffusion flag.
    if (record.get("statut_diffusion") or "O").upper() != "O":
        return None
    if (record.get("etat_administratif") or "").upper() != "A":
        return None

    siren = (record.get("siren") or "").strip()
    name = (record.get("nom_complet") or record.get("nom_raison_sociale") or "").strip()
    if not siren or not name:
        return None

    siege = record.get("siege") or {}
    lat = lon = None
    coords = (siege.get("coordonnees") or "").split(",")
    if len(coords) == 2:
        try:
            lat, lon = float(coords[0]), float(coords[1])
        except ValueError:
            lat = lon = None

    city = (siege.get("libelle_commune") or "").strip() or None
    return {
        "siren": siren,
        "siret": (siege.get("siret") or "").strip() or None,
        "name": name[:255],
        "trade_key": trade_key,
        "naf_code": (siege.get("activite_principale") or record.get("activite_principale") or "")[:10] or None,
        "address": (siege.get("adresse") or "").strip()[:400] or None,
        "postal_code": (siege.get("code_postal") or "").strip()[:10] or None,
        "city": city[:120] if city else None,
        "city_slug": city_slugify(city) if city else None,
        "dept_code": (siege.get("departement") or "").strip()[:5] or None,
        "latitude": lat,
        "longitude": lon,
        "date_creation": (record.get("date_creation") or "")[:10] or None,
        "employee_range": (record.get("tranche_effectif_salarie") or "")[:10] or None,
    }


def import_trade(
    trade_key: str,
    *,
    dept: str | None = None,
    postal_code: str | None = None,
    max_records: int = 500,
) -> dict:
    """Ingest one trade for one area. Returns counters, never raises on a
    single bad record."""
    naf = TRADE_TO_NAF.get(trade_key)
    if not naf:
        raise RegistryImportError(f"Métier inconnu : {trade_key}")

    created = updated = skipped = 0
    # A company with several establishments comes back more than once in the
    # same page of results. Until the batch is committed, a second occurrence
    # would not be found by the existence query and would be inserted again,
    # tripping the unique constraint at commit time — which fails the whole
    # batch, not just the duplicate.
    pending: set[str] = set()

    for record in iter_registry(
        naf=naf, dept=dept, postal_code=postal_code, max_records=max_records
    ):
        fields = _extract(record, trade_key)
        if fields is None:
            skipped += 1
            continue
        siren = fields["siren"]
        if siren in pending:
            skipped += 1
            continue
        existing = RegistryListing.query.filter_by(siren=siren).one_or_none()
        if existing is None:
            db.session.add(RegistryListing(**fields))
            pending.add(siren)
            created += 1
            continue
        # A delisting request is permanent, and a claimed listing belongs to
        # its artisan — registry data must not overwrite either.
        if existing.status in (STATUS_OPTED_OUT, STATUS_CLAIMED):
            skipped += 1
            continue
        for key, value in fields.items():
            setattr(existing, key, value)
        updated += 1

    try:
        db.session.commit()
    except Exception:
        # Roll back so the session is usable again: without this, one failed
        # batch leaves the transaction poisoned and every later trade and
        # department in the same run fails too.
        db.session.rollback()
        logger.exception(
            "Registry import failed for %s (dept=%s, cp=%s)", trade_key, dept, postal_code
        )
        raise
    return {"trade": trade_key, "created": created, "updated": updated, "skipped": skipped}


def listings_for(trade_key: str, city_slug: str, limit: int = 12) -> list[RegistryListing]:
    """Public, unclaimed listings for a trade in a town — oldest businesses
    first, since years in business is the only credibility signal the registry
    actually supports."""
    return (
        RegistryListing.query.filter_by(
            trade_key=trade_key, city_slug=city_slug, status=STATUS_LISTED
        )
        .order_by(RegistryListing.date_creation.asc().nullslast())
        .limit(limit)
        .all()
    )


def search_listings(
    *,
    trade_key: str | None = None,
    city: str | None = None,
    city_slug: str | None = None,
    dept_code: str | None = None,
    q: str | None = None,
    limit: int = 12,
) -> list[RegistryListing]:
    """Public, unclaimed listings matching an optional trade and area.

    Backs the main directory and the trade/department landing pages, where the
    filters arrive as free text rather than as a resolved slug.

    ``q`` is the free-text box. It must be applied: without it an unmatched
    search fell through to *no* filter at all and returned the first twelve rows
    in the table, so typing « chaville » answered with Dax and Pontlevoy — worse
    than an empty result, because the towns look like real answers.
    """
    from app.constants.cities import city_slugify

    query = RegistryListing.query.filter_by(status=STATUS_LISTED)
    if trade_key:
        query = query.filter(RegistryListing.trade_key == trade_key)
    if dept_code:
        query = query.filter(RegistryListing.dept_code == dept_code)
    if city_slug:
        query = query.filter(RegistryListing.city_slug == city_slug)
    elif city:
        term = city.strip()
        slug = city_slugify(term)
        # A postal code typed into the city box is a common shortcut.
        if term.isdigit():
            query = query.filter(RegistryListing.postal_code.startswith(term))
        elif slug:
            query = query.filter(RegistryListing.city_slug == slug)
    if q:
        term = q.strip()
        like = f"%{term}%"
        # Match the same fields the registered-artisan query matches on, so one
        # search box behaves the same either side of the results page. The slug
        # comparison is what makes an unaccented « dunieres » find « Dunières ».
        conditions = [
            RegistryListing.name.ilike(like),
            RegistryListing.city.ilike(like),
            RegistryListing.postal_code.ilike(like),
        ]
        slug = city_slugify(term)
        if slug:
            conditions.append(RegistryListing.city_slug == slug)
        query = query.filter(db.or_(*conditions))
    return (
        query.order_by(RegistryListing.date_creation.asc().nullslast())
        .limit(limit)
        .all()
    )


def count_for(trade_key: str, city_slug: str) -> int:
    return RegistryListing.query.filter_by(
        trade_key=trade_key, city_slug=city_slug, status=STATUS_LISTED
    ).count()


def stats() -> dict:
    from sqlalchemy import func

    rows = (
        db.session.query(RegistryListing.status, func.count(RegistryListing.id))
        .group_by(RegistryListing.status)
        .all()
    )
    by_status = {status: count for status, count in rows}
    return {
        "total": sum(by_status.values()),
        "listed": by_status.get(STATUS_LISTED, 0),
        "claimed": by_status.get(STATUS_CLAIMED, 0),
        "opted_out": by_status.get(STATUS_OPTED_OUT, 0),
        "cities": db.session.query(func.count(func.distinct(RegistryListing.city_slug))).scalar() or 0,
    }


# Official NAF (APE) wording, quoted verbatim from the nomenclature. Stating the
# declared activity in the register's own words is both accurate and a genuine
# entity signal on a company page.
NAF_LABELS: dict[str, str] = {
    "43.22A": "Travaux d'installation d'eau et de gaz en tous locaux",
    "43.22B": "Travaux d'installation d'équipements thermiques et de climatisation",
    "43.21A": "Travaux d'installation électrique dans tous locaux",
    "43.32A": "Travaux de menuiserie bois et PVC",
    "43.32B": "Travaux de menuiserie métallique et serrurerie",
    "43.34Z": "Travaux de peinture et vitrerie",
    "43.33Z": "Travaux de revêtement des sols et des murs",
    "43.91A": "Travaux de charpente",
    "43.91B": "Travaux de couverture par éléments",
    "43.99C": "Travaux de maçonnerie générale et gros œuvre de bâtiment",
    "81.30Z": "Services d'aménagement paysager",
}


def naf_label(code: str | None) -> str | None:
    return NAF_LABELS.get((code or "").strip().upper())


def find_by_name(query: str, *, limit: int = 10) -> list[RegistryListing]:
    """Look up a business the way its owner would: by name, or by SIREN.

    Powers the "is my company already listed?" flow, which is how an artisan
    reaches their own page without us sending anything.
    """
    import re as _re

    q = (query or "").strip()
    if len(q) < 3:
        return []

    digits = _re.sub(r"\D", "", q)
    if len(digits) in (9, 14):
        # A SIREN (9) or SIRET (14) identifies exactly one business.
        rows = RegistryListing.query.filter(
            RegistryListing.status == STATUS_LISTED,
            db.or_(
                RegistryListing.siren == digits[:9],
                RegistryListing.siret == digits,
            ),
        ).limit(limit).all()
        if rows:
            return rows

    like = f"%{q}%"
    return (
        RegistryListing.query.filter(
            RegistryListing.status == STATUS_LISTED,
            RegistryListing.name.ilike(like),
        )
        .order_by(RegistryListing.date_creation.asc().nullslast())
        .limit(limit)
        .all()
    )
