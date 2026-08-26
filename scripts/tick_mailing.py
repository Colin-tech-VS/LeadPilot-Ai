"""One pass over the mailing subsystem. Run from cron.

Three jobs in one entry because the host caps scheduled tasks at five, and
because they belong together anyway — each one protects the next:

1. Pull new inbound mail. Nothing else did this on a schedule, so replies and
   delivery reports only arrived when an admin happened to open the console.
2. Quarantine addresses behind a permanent bounce (the inbox sync triggers this,
   and it is re-run here so a failed sync does not skip it).
3. Advance scheduled and in-flight campaigns by one batch.

Sending to addresses that already bounced is what wrecks a domain's reputation,
so step 3 must never run ahead of steps 1 and 2.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402
from app.services import bounce_processing, campaigns, imap_mailbox  # noqa: E402


def main() -> int:
    app = create_app()
    with app.app_context():
        # A mailbox we cannot reach must not stop the campaigns from moving.
        if imap_mailbox.is_configured():
            try:
                sync = imap_mailbox.sync_inbox()
                print(f"inbox   : {sync.get('synced', 0)} nouveau(x), "
                      f"{sync.get('skipped', 0)} ignoré(s)")
            except Exception as exc:  # noqa: BLE001
                print(f"inbox   : échec — {type(exc).__name__}: {exc}", file=sys.stderr)
        else:
            print("inbox   : IMAP non configuré")

        try:
            bounces = bounce_processing.process_bounces()
            print(f"rebonds : {bounces['reports']} rapport(s), "
                  f"{bounces['marked']} adresse(s) retirée(s)")
        except Exception as exc:  # noqa: BLE001
            print(f"rebonds : échec — {type(exc).__name__}: {exc}", file=sys.stderr)

        reports = campaigns.run_due_campaigns()

    if not reports:
        print("campagnes : aucune à traiter")
        return 0
    for report in reports:
        print(
            f"campagne {report['campaign_id']} — {report['sent']} envoyés, "
            f"{report['failed']} échecs, {report['remaining']} restants "
            f"({report['status']})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
