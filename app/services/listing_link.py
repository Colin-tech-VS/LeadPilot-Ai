"""Match a signing-up artisan to an unclaimed registry listing.

Two ways in, deliberately different:

* A SIREN / SIRET is unique. If it points at a still-listed fiche, we attach
  without asking — that is the identifier the register itself uses.
* A name + city + trade collision is only a hint. We surface the candidate(s)
  and wait for an explicit confirmation. Silent merge on a name would hand a
  business page to whoever typed the same words first.
"""
from __future__ import annotations

import re
import unicodedata

from sqlalchemy import or_

from app.core.extensions import db
from app.models.registry_listing import STATUS_LISTED, RegistryListing
from app.utils.slug import slugify

_LEGAL_FORMS = {
    "sarl",
    "sas",
    "sasu",
    "sa",
    "sci",
    "scop",
    "snc",
    "eurl",
    "selarl",
    "ets",
    "ei",
    "eirl",
    "gie",
    "sem",
    "scm",
    "scp",
    "ste",
    "sté",
    "societe",
    "société",
    "etablissements",
    "établissements",
    "entreprise",
}

_MAX_SUGGESTIONS = 3
_SCAN_CAP = 24


def digits_only(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


def normalize_siret(value: str | None) -> str | None:
    digits = digits_only(value)
    return digits if len(digits) == 14 else None


def normalize_siren_or_siret(value: str | None) -> str | None:
    """Accept a SIREN (9) or SIRET (14). Anything else is rejected."""
    digits = digits_only(value)
    if len(digits) in (9, 14):
        return digits
    return None


def find_listed_by_identifier(value: str | None) -> RegistryListing | None:
    """Return the unclaimed listing for a SIREN or SIRET, if any."""
    digits = digits_only(value)
    if len(digits) not in (9, 14):
        return None

    siren = digits[:9]
    query = RegistryListing.query.filter_by(status=STATUS_LISTED, siren=siren)
    if len(digits) == 14:
        by_siret = (
            RegistryListing.query.filter_by(status=STATUS_LISTED, siret=digits)
            .one_or_none()
        )
        if by_siret is not None:
            return by_siret
    return query.one_or_none()


def suggest_listings(
    *,
    name: str,
    city: str | None,
    trade: str | None,
    limit: int = _MAX_SUGGESTIONS,
) -> list[RegistryListing]:
    """Candidates an artisan might be claiming — never attached on their own.

    No city, or a name too thin to be identifying, yields nothing: better to
    miss a merge than to propose the wrong business.
    """
    core = _name_core(name)
    if len(core) < 4:
        return []

    slug, postal = _city_keys(city)
    if not slug and not postal:
        return []

    query = RegistryListing.query.filter_by(status=STATUS_LISTED)
    if trade:
        query = query.filter(RegistryListing.trade_key == trade)

    city_filters = []
    if slug:
        city_filters.append(RegistryListing.city_slug == slug)
    if postal:
        city_filters.append(RegistryListing.postal_code == postal)
    query = query.filter(or_(*city_filters))

    rows = query.limit(_SCAN_CAP).all()
    exact: list[RegistryListing] = []
    close: list[RegistryListing] = []
    for row in rows:
        row_core = _name_core(row.name)
        if not row_core:
            continue
        if row_core == core:
            exact.append(row)
        elif _names_close(core, row_core):
            close.append(row)

    if exact:
        return exact[:limit]
    if len(close) == 1:
        return close
    if 1 < len(close) <= limit:
        return close
    return []


def resolve_signup_listing(
    *,
    siret: str = "",
    claim_siren: str = "",
    name: str = "",
    city: str = "",
    trade: str = "",
) -> tuple[RegistryListing | None, list[RegistryListing] | None, str | None]:
    """Decide what signup should do with the registry.

    Returns ``(listing_to_attach, suggestions_to_show, error_key)``.
    """
    raw = (siret or "").strip()
    if raw and not normalize_siren_or_siret(raw):
        return None, None, "register.error.siret_invalid"

    identifier = normalize_siren_or_siret(raw)
    if identifier:
        return find_listed_by_identifier(identifier), None, None

    chosen = (claim_siren or "").strip()
    if chosen == "skip":
        return None, None, None
    if chosen:
        listing = find_listed_by_identifier(chosen)
        return listing, None, None

    suggestions = suggest_listings(name=name, city=city, trade=trade)
    if suggestions:
        return None, suggestions, None
    return None, None, None


def persist_siret(tenant, identifier: str | None) -> None:
    """Store a 14-digit SIRET on the tenant when we have one and they don't."""
    siret = normalize_siret(identifier)
    if not siret or (tenant.siret or "").strip():
        return
    tenant.siret = siret
    db.session.commit()


def link_tenant(tenant, listing: RegistryListing | None):
    """Attach ``listing`` to ``tenant``. No-op when the listing is missing."""
    if listing is None or tenant is None:
        return None
    from app.services.listing_claims import attach

    return attach(listing.siren, tenant.id)


def link_tenant_by_siret(tenant, siret: str | None):
    """Settings / later-SIRET path: attach if a listed fiche matches."""
    listing = find_listed_by_identifier(siret)
    linked = link_tenant(tenant, listing)
    persist_siret(tenant, siret)
    return linked


def _fold(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = value.encode("ascii", "ignore").decode("ascii").lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _name_core(value: str) -> str:
    tokens = [t for t in _fold(value).split() if t not in _LEGAL_FORMS and len(t) > 1]
    return " ".join(tokens)


def _names_close(a: str, b: str) -> bool:
    if not a or not b:
        return False
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if len(shorter) >= 8 and shorter in longer:
        return True
    short_tokens = shorter.split()
    long_tokens = set(longer.split())
    if len(short_tokens) >= 2 and all(len(t) >= 3 and t in long_tokens for t in short_tokens):
        return True
    return False


def _city_keys(city: str | None) -> tuple[str, str | None]:
    raw = (city or "").strip()
    if not raw:
        return "", None
    from app.services.address_lookup import city_from_place_query

    term = city_from_place_query(raw) or raw
    slug = slugify(term)
    digits = digits_only(raw)
    postal = digits[:5] if len(digits) >= 5 else None
    return slug, postal
