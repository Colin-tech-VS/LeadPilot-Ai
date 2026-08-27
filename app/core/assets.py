"""Content-hashed URLs for static files, so they can be cached forever.

Flask serves ``/static`` with ``Cache-Control: no-cache`` by default, which
makes the browser revalidate every stylesheet, script and image on every single
navigation. On a phone that is a round trip per asset, per page.

The usual fix is a ``?v=`` token, and the templates carried 61 of them, bumped
by hand. They had already drifted: ``auth.js`` was referenced as ``v=5`` from
one template and ``v=7`` from another, and three other files disagreed the same
way. That is exactly what makes a long cache dangerous — freeze a year of
caching onto a token nobody remembered to bump and visitors keep a stale script
until it expires.

So the token is derived from the file's own bytes instead. It changes when, and
only when, the file changes: a deploy that does not touch a stylesheet leaves
its URL — and every visitor's cached copy — untouched. Nothing to bump, nothing
to forget, and ``immutable`` becomes true rather than hopeful.
"""
from __future__ import annotations

import hashlib
import logging
import os

from flask import current_app, request

logger = logging.getLogger(__name__)

# filename -> short digest. Static files never change within a process (a
# deploy starts a new one), so one read per file per worker is enough.
_VERSIONS: dict[str, str] = {}

CACHE_FOREVER = "public, max-age=31536000, immutable"
# A static file fetched without a version token could be anything; keep it
# fresh enough that a mistake is not permanent.
CACHE_SHORT = "public, max-age=3600"


def asset_version(filename: str) -> str | None:
    """Short content digest for a file under ``static/``, or None if unreadable."""
    cached = _VERSIONS.get(filename)
    if cached is not None:
        return cached

    root = current_app.static_folder
    if not root:
        return None

    root_abs = os.path.abspath(root)
    path = os.path.abspath(os.path.join(root_abs, filename))
    # url_for() is called with template-controlled names, but a traversal here
    # would hash files outside static/ and leak their existence through the URL.
    if path != root_abs and not path.startswith(root_abs + os.sep):
        return None

    try:
        with open(path, "rb") as handle:
            digest = hashlib.blake2s(handle.read(), digest_size=4).hexdigest()
    except OSError:
        # Missing file: let url_for build the plain URL and 404 honestly,
        # rather than failing the whole page render.
        return None

    _VERSIONS[filename] = digest
    return digest


def register_asset_versioning(app) -> None:
    @app.url_defaults
    def _stamp_static_urls(endpoint, values):
        """Append ?v=<digest> to every url_for('static', …)."""
        if endpoint != "static" or "v" in values:
            return
        filename = values.get("filename")
        if not filename:
            return
        version = asset_version(filename)
        if version:
            values["v"] = version

    @app.after_request
    def _cache_static(response):
        """Cache versioned assets forever; they can never go stale in place.

        Scoped to /static/ on purpose: the service worker is served from /sw.js
        and must stay revalidated, or a bad release would be unrecoverable for
        anyone who already had it.
        """
        if not request.path.startswith("/static/"):
            return response
        if response.status_code != 200:
            return response
        response.headers["Cache-Control"] = (
            CACHE_FOREVER if request.args.get("v") else CACHE_SHORT
        )
        return response
