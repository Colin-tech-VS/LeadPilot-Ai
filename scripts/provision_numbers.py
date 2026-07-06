"""Provision a dedicated AI phone number for existing tenants.

New tenants get their number automatically at signup (see signup_service). This
one-off/backfill job gives an AI number to any tenant created before that, or to
tenants whose earlier provisioning attempt failed.

Idempotent: a tenant that already has ``ai_phone_number`` is skipped, so it is
safe to re-run. Requires TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN and, for the
webhook URL, SERVER_NAME to be configured.

Usage:
    python scripts/provision_numbers.py            # provision every tenant missing a number
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

        # A tenant that still carries the shared fallback number (persisted by an
        # older settings form) is NOT dedicated — treat it as missing so it gets
        # its own number, otherwise call routing collides between tenants.
        from sqlalchemy import or_

        shared = app.config.get("TWILIO_AI_PHONE_NUMBER")
        for t in Tenant.query.filter(Tenant.ai_phone_number == shared).all():
            t.ai_phone_number = None
        db.session.commit()

        missing = Tenant.query.filter(
            or_(Tenant.ai_phone_number.is_(None), Tenant.ai_phone_number == "")
        ).all()
        if not missing:
            print("Tous les tenants ont déjà un numéro IA dédié. Rien à faire.")
            return

        print(f"{len(missing)} tenant(s) sans numéro IA.")
        provisioned = failed = 0
        for tenant in missing:
            if dry_run:
                print(f"  [dry-run] {tenant.name} ({tenant.id})")
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
