"""Audit — and optionally repair — the mapping between Twilio numbers and tenants.

Why this exists
---------------
Every Twilio number carries the tenant it belongs to inside its voice webhook
(``/voice/inbound?tenant_id=…``). Delete the accounts and the numbers keep
pointing at ids that resolve to nothing: the line answers, greets the caller and
then cuts them off. Nothing raises, nothing alerts, and the numbers keep being
billed every month.

That is exactly what happened here — eleven numbers, all dangling. This script
makes that state visible, and can repair it by handing an orphaned number to a
tenant that has none, which is both faster and cheaper than buying another.

Read-only by default; nothing is changed without an explicit flag.

Usage:
    python scripts/audit_twilio_numbers.py              # report only
    python scripts/audit_twilio_numbers.py --reassign   # give orphans to tenants lacking a number
"""
import sys
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import current_app

from app import create_app
from app.core.extensions import db
from app.models.tenant import Tenant
from app.services.twilio_provisioning import twilio_configured, voice_webhook_url


def _client():
    from twilio.rest import Client

    cfg = current_app.config
    return Client(cfg["TWILIO_ACCOUNT_SID"], cfg["TWILIO_AUTH_TOKEN"])


def _tenant_id_in(voice_url: str) -> str | None:
    if not voice_url:
        return None
    ids = parse_qs(urlparse(voice_url).query).get("tenant_id")
    return ids[0] if ids else None


def _resolve(tenant_id: str | None):
    if not tenant_id:
        return None
    try:
        return db.session.get(Tenant, uuid.UUID(tenant_id))
    except (ValueError, AttributeError):
        return None


def main(argv):
    reassign = "--reassign" in argv

    app = create_app()
    with app.app_context():
        if not twilio_configured():
            print("Twilio non configuré (TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN).")
            return 1

        shared = (current_app.config.get("TWILIO_AI_PHONE_NUMBER") or "").strip()
        client = _client()
        numbers = client.incoming_phone_numbers.list(limit=200)

        orphans, healthy = [], []
        for num in numbers:
            tenant_id = _tenant_id_in(num.voice_url or "")
            tenant = _resolve(tenant_id)
            row = (num, tenant_id, tenant)
            (healthy if tenant else orphans).append(row)

        print(f"{len(numbers)} numéro(s) Twilio\n")
        for num, tenant_id, tenant in healthy:
            print(f"  OK       {num.phone_number}  → {tenant.name}")
        for num, tenant_id, _ in orphans:
            tag = " (numéro partagé)" if num.phone_number == shared else ""
            print(f"  ORPHELIN {num.phone_number}{tag}  → tenant_id={tenant_id or '—'} introuvable")

        # The shared fallback number is routed by TWILIO_DEFAULT_TENANT_ID rather
        # than by a tenant_id in its URL, so check that separately.
        default_id = (current_app.config.get("TWILIO_DEFAULT_TENANT_ID") or "").strip()
        default_tenant = _resolve(default_id)
        print()
        if not default_id:
            print("  TWILIO_DEFAULT_TENANT_ID absente — les appels au numéro partagé échoueront.")
        elif default_tenant is None:
            print(f"  TWILIO_DEFAULT_TENANT_ID={default_id} introuvable — le numéro partagé raccroche.")
        else:
            print(f"  TWILIO_DEFAULT_TENANT_ID → {default_tenant.name}")

        waiting = (
            Tenant.query.filter(Tenant.ai_phone_number.is_(None)).all()
            if orphans
            else []
        )
        print(f"\n{len(orphans)} orphelin(s), {len(waiting)} compte(s) sans numéro.")

        if not reassign:
            if orphans and waiting:
                print("\nRelancer avec --reassign pour réattribuer les numéros déjà payés.")
            return 0

        if not orphans or not waiting:
            print("Rien à réattribuer.")
            return 0

        for (num, _tid, _t), tenant in zip(orphans, waiting):
            if num.phone_number == shared:
                continue  # the shared number belongs to no single tenant
            url = voice_webhook_url(str(tenant.id))
            if not url:
                print("PUBLIC_BASE_URL manquante — impossible de reconstruire le webhook.")
                return 1
            num.update(voice_url=url, voice_method="POST",
                       friendly_name=f"PilotCore — {tenant.name}"[:64])
            tenant.ai_phone_number = num.phone_number
            db.session.commit()
            print(f"  RÉATTRIBUÉ {num.phone_number} → {tenant.name}")
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
