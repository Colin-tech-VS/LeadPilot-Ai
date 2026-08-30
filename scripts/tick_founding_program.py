"""Daily maintenance tick: founding statuses, expiry mails, conversion flags —
and the trial receptionist lines that are due to be handed back.

The two live together because the host caps this app at five scheduled tasks
and both are daily, best-effort and independent of each other.

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
    from app.services import founding_program, twilio_provisioning

    app = create_app()
    with app.app_context():
        result = founding_program.tick()
        logger.info("founding tick: %s", result)
        try:
            released, failed = twilio_provisioning.release_expired_trial_lines()
            logger.info("trial lines: released=%s failed=%s", released, failed)
        except Exception:
            # A Twilio outage must not stop the founding tick from having run.
            logger.exception("trial line release failed")
        return 0


if __name__ == "__main__":
    sys.exit(run())
