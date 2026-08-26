"""Advance scheduled and in-flight mailing campaigns by one batch.

Run from cron. Each pass starts campaigns whose scheduled time has come and
sends the next slice of every campaign still in flight, so a large send spreads
across passes instead of hammering the relay in one burst.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402
from app.services import campaigns  # noqa: E402


def main() -> int:
    app = create_app()
    with app.app_context():
        reports = campaigns.run_due_campaigns()

    if not reports:
        print("Aucune campagne à traiter.")
        return 0
    for report in reports:
        print(
            f"{report['campaign_id']} — {report['sent']} envoyés, "
            f"{report['failed']} échecs, {report['remaining']} restants "
            f"({report['status']})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
