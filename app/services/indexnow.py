"""IndexNow — push new and updated URLs to Bing, Yandex and partners.

Waiting to be crawled costs days on a fresh domain. IndexNow inverts that: one
authenticated POST and participating engines fetch the URL within minutes. It
is a free, open protocol with no quota to speak of; Google does not take part,
which is why it complements — never replaces — the sitemap.

Ownership is proven by hosting the key as plain text at ``/<key>.txt``. The key
is derived from ``SECRET_KEY`` so it is stable across deploys without adding
another environment variable to manage, and it is not a credential: it grants
nothing beyond the right to submit URLs for this host.

Every call is best-effort and swallows its errors — a search-engine ping must
never surface as a failed publish for the editor who clicked "publish".
"""
from __future__ import annotations

import hashlib
import logging

logger = logging.getLogger(__name__)

ENDPOINT = "https://api.indexnow.org/indexnow"
_MAX_URLS = 10_000  # protocol cap per submission


def get_key() -> str:
    """Stable 32-char hex key for this site."""
    from flask import current_app

    seed = current_app.config.get("SECRET_KEY") or "pilotcore"
    return hashlib.sha256(f"indexnow:{seed}".encode()).hexdigest()[:32]


def key_file_body() -> str:
    return get_key()


def is_enabled() -> bool:
    """Only ping for a real public host — never from localhost or a preview."""
    from app.utils.seo import site_base_url

    base = site_base_url()
    return base.startswith("https://") and "localhost" not in base and "127.0.0.1" not in base


def submit(paths: list[str] | str) -> bool:
    """Submit one or more site-relative paths. Returns True when accepted."""
    if isinstance(paths, str):
        paths = [paths]
    if not paths or not is_enabled():
        return False

    from urllib.parse import urlparse

    from app.utils.seo import site_base_url

    base = site_base_url().rstrip("/")
    host = urlparse(base).netloc
    key = get_key()
    url_list = [base + p if p.startswith("/") else p for p in paths][:_MAX_URLS]

    payload = {
        "host": host,
        "key": key,
        "keyLocation": f"{base}/{key}.txt",
        "urlList": url_list,
    }
    try:
        import requests

        resp = requests.post(
            ENDPOINT,
            json=payload,
            timeout=10,
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        # 200 accepted, 202 accepted-pending-key-check; both are successes.
        if resp.status_code in (200, 202):
            logger.info("IndexNow accepted %d URL(s)", len(url_list))
            return True
        logger.warning("IndexNow rejected: HTTP %s %s", resp.status_code, resp.text[:200])
        return False
    except Exception:  # noqa: BLE001 — a ping must never break a publish
        logger.exception("IndexNow submission failed")
        return False
