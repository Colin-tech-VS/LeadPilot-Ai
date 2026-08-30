"""Provision a dedicated AI phone number for every tenant entitled to one.

Paying tenants get their number on Stripe checkout (see billing.py) and trial
artisans get theirs the moment they press « Activer ma ligne » on the dashboard.
This backfill catches the ones a Twilio outage, a full trial-line cap or a
pre-hook subscription left without one — an artisan whose request was recorded
but never fulfilled has a dashboard that says « en cours d'attribution » and
nothing else will ever change that.

Idempotent: a tenant that already has a dedicated ``ai_phone_number`` is
skipped, and so is a trial that never asked.

Usage:
    python scripts/provision_numbers.py            # everyone entitled, missing a number
    python scripts/provision_numbers.py --dry-run  # list who would get one, buy nothing
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.core.extensions import db
from app.models.tenant import Tenant
from app.services.twilio_provisioning import (
    auto_provision_enabled,
    provision_ai_number,
    should_buy_dedicated_number,
)


def main(argv):
    dry_run = "--dry-run" in argv

    app = create_app()
    with app.app_context():
        if not auto_provision_enabled():
            print(
                "Auto-provision indisponible : vérifiez TWILIO_ACCOUNT_SID / "
                "TWILIO_AUTH_TOKEN et TWILIO_AUTO_PROVISION_NUMBERS."
            )
            return

        shared = (app.config.get("TWILIO_AI_PHONE_NUMBER") or "").strip()
        missing = []
        for tenant in Tenant.query.all():
            number = (tenant.ai_phone_number or "").strip()
            if number and not (shared and number == shared):
                continue
            if should_buy_dedicated_number(tenant):
                missing.append(tenant)

        if not missing:
            print("Tous les artisans concernés ont déjà un numéro de réception dédié. Rien à faire.")
            return

        print(f"{len(missing)} artisan(s) sans numéro de réception dédié.")
        provisioned = failed = 0
        for tenant in missing:
            if dry_run:
                print(f"  [dry-run] {tenant.name} ({tenant.plan}) ({tenant.id})")
                continue
            number = provision_ai_number(tenant)
            if number:
                db.session.commit()
                provisioned += 1
                print(f"  ✓ {tenant.name}: {number}")
            else:
                db.session.rollback()
                failed += 1
                print(f"  ✗ {tenant.name}: échec (voir les logs)")

        if not dry_run:
            print(f"\nTerminé — {provisioned} numéro(s) provisionné(s), {failed} échec(s).")


if __name__ == "__main__":
    main(sys.argv[1:])
