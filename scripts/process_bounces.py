"""Read delivery reports and quarantine addresses that bounced for good.

Run from cron. Keeps the sending domain's bounce rate low, which is what keeps
the transactional mail (signup, password reset) out of the spam folder.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402
from app.services import bounce_processing  # noqa: E402


def main() -> int:
    app = create_app()
    with app.app_context():
        result = bounce_processing.process_bounces()

    print(f"{result['reports']} rapport(s) de rebond lus")
    print(f"  retirées des envois : {result['marked']}")
    print(f"  déjà traitées       : {result['already_marked']}")
    print(f"  temporaires ignorés : {result['temporary_ignored']}")
    print(f"  hors base           : {result['unknown_recipient']}")
    for addr in result["addresses"]:
        print(f"    · {addr}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
