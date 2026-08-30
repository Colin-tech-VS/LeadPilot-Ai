"""Give back the receptionist number of a trial that ended without subscribing.

Trials now get a real dedicated line — the only way a free trial can
demonstrate anything. The other side of that is this: a line nobody pays for
and nobody uses any more is a monthly Twilio bill and a seat held against
``TWILIO_MAX_TRIAL_LINES``, so it goes back after a grace period.

The host caps this app at five scheduled tasks, so the daily run happens inside
``scripts/tick_founding_program.py``. This script is the manual handle for the
same routine.

Usage:
    python scripts/release_expired_trial_lines.py            # release what is due
    python scripts/release_expired_trial_lines.py --dry-run  # list it, release nothing
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.services import twilio_provisioning


def main(argv):
    dry_run = "--dry-run" in argv

    app = create_app()
    with app.app_context():
        due = twilio_provisioning.expired_trial_lines()
        if not due:
            print("Aucune ligne d'essai à libérer.")
            return

        grace = twilio_provisioning.TRIAL_LINE_GRACE_DAYS
        print(f"{len(due)} ligne(s) d'essai expirée(s) depuis plus de {grace} jours.")
        if dry_run:
            for tenant in due:
                print(f"  [dry-run] {tenant.name} ({tenant.id}) : {tenant.ai_phone_number}")
            return

        released, failed = twilio_provisioning.release_expired_trial_lines()
        print(f"Terminé — {released} libérée(s), {failed} en échec.")


if __name__ == "__main__":
    main(sys.argv[1:])
