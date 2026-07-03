"""Bill last month's call overage for every paid tenant.

Run once at the start of each calendar month (e.g. a Scalingo scheduled task on
the 1st). For each paid tenant it posts the previous month's extra calls to
Stripe as an invoice item, added to their next invoice. Idempotent: a tenant
whose ``last_overage_period`` already matches the target month is skipped, so
re-running the job is safe.

Usage:
    python scripts/bill_overage.py            # bill the previous calendar month
    python scripts/bill_overage.py 2026 6     # bill a specific year/month
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.models.tenant import Tenant
from app.services import billing


def _previous_month():
    now = datetime.now(timezone.utc)
    if now.month == 1:
        return now.year - 1, 12
    return now.year, now.month - 1


def main(argv):
    if len(argv) >= 2:
        year, month = int(argv[0]), int(argv[1])
    else:
        year, month = _previous_month()

    app = create_app()
    with app.app_context():
        if not billing.is_configured():
            print("Stripe n'est pas configuré — aucun dépassement facturé.")
        tenants = Tenant.query.all()
        totals = {"billed": 0, "no_overage": 0, "skipped": 0, "not_configured": 0}
        total_cents = 0
        for tenant in tenants:
            if not tenant.is_paid:
                continue
            res = billing.bill_overage_for_period(tenant, year, month)
            totals[res["status"]] = totals.get(res["status"], 0) + 1
            if res["status"] == "billed":
                total_cents += res["amount_cents"]
                print(
                    f"  {tenant.name}: {res['calls']} appel(s) → "
                    f"{res['amount_cents'] / 100:.2f} €"
                )
        print(
            f"Période {year:04d}-{month:02d} — "
            f"facturés: {totals['billed']}, sans dépassement: {totals['no_overage']}, "
            f"déjà traités: {totals['skipped']}, en attente (Stripe): {totals['not_configured']}. "
            f"Total facturé: {total_cents / 100:.2f} €"
        )


if __name__ == "__main__":
    main(sys.argv[1:])
