"""Canonical public URLs for SEO (sitemap, OG tags, JSON-LD)."""
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
