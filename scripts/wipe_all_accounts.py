"""Supprime tous les comptes artisans (tenants + users) et leurs données.

L'admin /admin n'est PAS dans la table users — auth via ADMIN_PASSWORD env.
Les offres tarifaires (table offers) sont conservées.

Usage local :    python scripts/wipe_all_accounts.py
Usage Scalingo : scalingo --app leadpilot-ai run python scripts/wipe_all_accounts.py
Dry-run :        python scripts/wipe_all_accounts.py --dry-run
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app import create_app
from app.core.extensions import db

app = create_app()
DRY_RUN = "--dry-run" in sys.argv

TABLES = [
    "appointments",
    "quotes",
    "notifications",
    "page_views",
    "email_messages",
    "leads",
    "users",
    "tenants",
]

with app.app_context():
    if DRY_RUN:
        print("DRY RUN — rien ne sera supprimé.")
        for t in TABLES:
            try:
                n = db.session.execute(text(f'SELECT count(*) FROM "{t}"')).scalar()
                print(f"  {t}: {n}")
            except Exception as exc:
                print(f"  {t}: (skip) {exc}")
        sys.exit(0)

    deleted = {}
    for t in TABLES:
        try:
            n = db.session.execute(text(f'DELETE FROM "{t}"')).rowcount
            db.session.commit()
            deleted[t] = n
        except Exception as exc:
            db.session.rollback()
            print(f"ERR {t}: {exc}")
            sys.exit(1)

    print("Suppression terminée :")
    for t, n in deleted.items():
        print(f"  {t}: {n}")
    print("Offres (offers) et admin env conservés.")
