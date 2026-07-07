"""Tracked landing URLs for Facebook posts (UTM analytics)."""
from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from app.utils.seo import site_base_url

UTM_SOURCE = "facebook"
UTM_MEDIUM = "social"

# Curated PilotCore pages — keys used in admin UI and utm_campaign.
LANDING_TARGETS = (
    {
        "key": "home",
        "label": "Accueil particuliers",
        "path": "/",
        "campaign": "particuliers_home",
        "audience": "particuliers qui cherchent un artisan (plombier, serrurier, électricien…)",
        "cta": "Trouver un artisan et prendre RDV en ligne",
    },
    {
        "key": "pro",
        "label": "PilotCore Pro (artisans)",
        "path": "/pro",
        "campaign": "pro_landing",
        "audience": "artisans, plombiers, électriciens et indépendants du bâtiment",
        "cta": "Essayer le standard téléphonique IA gratuitement",
    },
    {
        "key": "artisans",
        "label": "Annuaire artisans",
        "path": "/artisans",
        "campaign": "annuaire",
        "audience": "particuliers à la recherche d'un professionnel près de chez eux",
        "cta": "Parcourir l'annuaire et réserver un créneau",
    },
    {
        "key": "contact",
        "label": "Contact",
        "path": "/contact",
        "campaign": "contact",
        "audience": "prospects qui ont une question sur PilotCore",
        "cta": "Nous écrire",
    },
)

_TARGETS_BY_KEY = {t["key"]: t for t in LANDING_TARGETS}


def get_target(key: str | None) -> dict | None:
    if not key:
        return None
    return _TARGETS_BY_KEY.get(key)


def canonical_page_url(path: str = "/") -> str:
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{site_base_url()}{path}"


def build_tracked_url(
    path: str = "/",
    *,
    campaign: str | None = None,
    content: str = "admin_post",
    source: str = UTM_SOURCE,
    medium: str = UTM_MEDIUM,
) -> str:
    """Return a public URL with standard UTM parameters for analytics."""
    base = canonical_page_url(path)
    parsed = urlparse(base)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    slug = (path.strip("/") or "home").replace("/", "_")
    query.update(
        {
            "utm_source": source,
            "utm_medium": medium,
            "utm_campaign": campaign or f"fb_{slug}",
            "utm_content": content,
        }
    )
    return urlunparse(parsed._replace(query=urlencode(query)))


def build_tracked_url_for_target(
    target_key: str,
    *,
    content: str = "admin_post",
) -> str | None:
    target = get_target(target_key)
    if not target:
        return None
    return build_tracked_url(
        target["path"],
        campaign=target["campaign"],
        content=content,
    )


def display_url(url: str | None) -> str:
    """Strip UTM params for admin preview (clean link shown to humans)."""
    if not url:
        return ""
    parsed = urlparse(url.strip())
    query = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if not k.lower().startswith("utm_")
    ]
    return urlunparse(parsed._replace(query=urlencode(query)))


def ensure_tracked(
    url: str | None,
    *,
    target_key: str | None = None,
    content: str = "admin_post",
) -> str | None:
    """Add UTM params when missing (e.g. user pasted a clean URL)."""
    url = (url or "").strip()
    if not url:
        if target_key:
            return build_tracked_url_for_target(target_key, content=content)
        return None
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if any(k.lower().startswith("utm_") for k in query):
        return url
    target = get_target(target_key) if target_key else None
    path = parsed.path or "/"
    campaign = target["campaign"] if target else f"fb_{(path.strip('/') or 'home').replace('/', '_')}"
    query.update(
        {
            "utm_source": UTM_SOURCE,
            "utm_medium": UTM_MEDIUM,
            "utm_campaign": campaign,
            "utm_content": content,
        }
    )
    return urlunparse(parsed._replace(query=urlencode(query)))


def targets_for_admin() -> list[dict]:
    """Serialize landing targets with clean + tracked URLs for the admin UI."""
    rows = []
    for t in LANDING_TARGETS:
        rows.append(
            {
                **t,
                "display_url": canonical_page_url(t["path"]),
                "tracked_url": build_tracked_url(t["path"], campaign=t["campaign"]),
            }
        )
    return rows
