"""Canonical public URLs and SEO helpers."""
from __future__ import annotations

import json
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
        "name": "LeadPilot AI",
        "alternateName": "PilotCore",
        "url": site_base_url(),
        "logo": logo_url(),
        "description": description,
        "email": "contact@pilotcore.fr",
        "areaServed": {"@type": "Country", "name": "France"},
        "sameAs": [],
    }
