"""Canonical public URLs and SEO helpers."""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from flask import current_app, request


def site_base_url() -> str:
    """Prefer PUBLIC_BASE_URL in prod; fall back to the current request host."""
    base = (current_app.config.get("PUBLIC_BASE_URL") or "").strip()
    if base:
        return base.rstrip("/")
    return request.url_root.rstrip("/")


def canonical_url(path: str = "/") -> str:
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{site_base_url()}{path}"


def hreflang_alternates(path: str = "/") -> list[tuple[str, str]]:
    """Return (hreflang, absolute URL) pairs for FR / EN / x-default."""
    base = canonical_url(path)
    return [
        ("fr", base),
        ("en", f"{base}?lang=en"),
        ("x-default", base),
    ]


def profile_keywords(trade_label: str, city: str, postal_code: str | None, lang: str = "fr") -> str:
    city = (city or "").strip()
    postal = (postal_code or "").strip()
    if lang == "en":
        parts = [trade_label, city, postal, "tradesperson", "emergency repair", "book online"]
    else:
        parts = [trade_label, city, postal, "artisan", "dépannage", "RDV en ligne", f"{trade_label} {city}"]
    return ", ".join(p for p in parts if p)


def format_lastmod(value: date | datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    return value.isoformat()


def logo_url() -> str:
    return f"{site_base_url()}/static/images/logo.svg"


def og_locale(lang: str = "fr") -> str:
    return "fr_FR" if lang == "fr" else "en_GB"


def json_ld_script(data: dict[str, Any] | list[Any]) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def organization_json_ld(lang: str, description: str) -> dict[str, Any]:
    return {
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": "PilotCore",
        "alternateName": "PilotCore",
        "url": site_base_url(),
        "logo": logo_url(),
        "description": description,
        "email": "contact@pilotcore.fr",
        "contactPoint": {
            "@type": "ContactPoint",
            "contactType": "customer support",
            "email": "contact@pilotcore.fr",
            "availableLanguage": ["French", "English"],
            "areaServed": "FR",
        },
        "areaServed": {"@type": "Country", "name": "France"},
        "sameAs": [],
    }
