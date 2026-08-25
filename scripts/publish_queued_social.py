"""Publish a due Facebook autopost preview, then compose the next one.

Scheduled via Scalingo cron (every 15 minutes). Never publishes without a
queued preview that was visible in /admin/social for the chosen interval.

Usage:
    python scripts/publish_queued_social.py
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("queued_social")


def run() -> int:
    from app import create_app
    from app.services import social, social_autopost

    app = create_app()
    with app.app_context():
        try:
            social.refresh_never_expiring_token()
        except Exception:
            logger.exception("Facebook token refresh failed")
        result = social_autopost.tick()
        logger.info("social autopost tick: %s", result)
        if result.get("action") == "failed":
            return 3
        return 0


if __name__ == "__main__":
    sys.exit(run())
