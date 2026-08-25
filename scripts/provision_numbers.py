"""Provision a dedicated AI phone number for **paying** tenants that lack one.

New paying tenants get their number on Stripe checkout (see billing.py). This
backfill is for artisans who already paid before that hook existed.

Idempotent: a tenant that already has a dedicated ``ai_phone_number`` is
skipped. Trial / test accounts are skipped on purpose — they share
``TWILIO_AI_PHONE_NUMBER``.

Usage:
    python scripts/provision_numbers.py            # paid tenants missing a number
    python scripts/provision_numbers.py --dry-run  # list who would get one, buy nothing
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.core.extensions import db
from app.models.tenant import Tenant
from app.services.twilio_provisioning import auto_provision_enabled, provision_ai_number


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
        paid = [t for t in Tenant.query.all() if t.is_paid]
        missing = []
        for tenant in paid:
            number = (tenant.ai_phone_number or "").strip()
            if not number or (shared and number == shared):
                missing.append(tenant)

        if not missing:
            print("Tous les artisans payants ont déjà un numéro IA dédié. Rien à faire.")
            return

        print(f"{len(missing)} artisan(s) payant(s) sans numéro IA dédié.")
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
