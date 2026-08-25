"""Daily founding-programme tick: statuses, expiry mails, conversion flags.

Usage:
    python scripts/tick_founding_program.py
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("founding_program")


def run() -> int:
    from app import create_app
    from app.services import founding_program

    app = create_app()
    with app.app_context():
        result = founding_program.tick()
        logger.info("founding tick: %s", result)
        return 0


if __name__ == "__main__":
    sys.exit(run())
