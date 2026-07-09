"""Supprime UN compte artisan (tenant) et toutes ses données dépendantes.

Contrairement à wipe_all_accounts.py (qui vide toutes les tables), ce script ne
touche qu'au tenant dont le nom est passé en argument, et à ses lignes liées
(RDV, devis, notifications, leads, users, e-mails, events).

Sécurité :
  - dry-run par défaut : affiche ce qui serait supprimé, ne supprime rien ;
  - il faut ajouter --yes pour exécuter réellement la suppression ;
  - refus s'il y a 0 ou plusieurs tenants pour ce nom (évite de supprimer le
    mauvais compte ou plusieurs comptes d'un coup).

Usage local :
  python scripts/delete_tenant.py "Coli Plomberie"            # dry-run
  python scripts/delete_tenant.py "Coli Plomberie" --yes      # supprime

Usage Scalingo (production) :
  scalingo --app PilotCore-ai run python scripts/delete_tenant.py "Coli Plomberie"
  scalingo --app PilotCore-ai run python scripts/delete_tenant.py "Coli Plomberie" --yes
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app import create_app
from app.core.extensions import db

# Tables scoped par tenant_id, dans l'ordre de suppression : les tables qui
# référencent leads/tenants d'abord, puis leads, puis le tenant lui-même.
#   appointments -> leads + tenants
#   quotes       -> leads + tenants
#   notifications-> tenants
#   users        -> tenants
#   email_messages / events : tenant_id sans contrainte FK (nettoyage orphelins)
#   leads        -> tenants (supprimé en avant-dernier)
DEPENDENT_TABLES = [
    "appointments",
    "quotes",
    "notifications",
    "users",
    "email_messages",
    "events",
    "leads",
]


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    do_delete = "--yes" in sys.argv

    if not args:
        print('Usage : python scripts/delete_tenant.py "<nom du compte>" [--yes]')
        sys.exit(2)

    name = args[0]

    app = create_app()
    with app.app_context():
        rows = db.session.execute(
            text('SELECT id, name, plan FROM tenants WHERE lower(name) = lower(:n)'),
            {"n": name},
        ).all()

        if not rows:
            print(f'Aucun tenant nommé « {name} ». Rien à supprimer.')
            # Aide : lister les noms proches pour repérer une faute de frappe.
            like = db.session.execute(
                text("SELECT name FROM tenants WHERE lower(name) LIKE lower(:p) ORDER BY name"),
                {"p": f"%{name.split()[0]}%"},
            ).all()
            if like:
                print("Noms approchants trouvés :")
                for (n,) in like:
                    print(f"  - {n}")
            sys.exit(1)

        if len(rows) > 1:
            print(f'{len(rows)} tenants portent le nom « {name} » — refus par sécurité :')
            for r in rows:
                print(f"  - {r.id} (plan={r.plan})")
            print("Supprime-les via leur id avec l'admin, ou affine le nom.")
            sys.exit(1)

        tenant = rows[0]
        tid = tenant.id
        print(f'Tenant ciblé : « {tenant.name} »  id={tid}  plan={tenant.plan}')
        print()

        # Compte des lignes dépendantes.
        counts = {}
        for tbl in DEPENDENT_TABLES:
            counts[tbl] = db.session.execute(
                text(f'SELECT count(*) FROM "{tbl}" WHERE tenant_id = :tid'),
                {"tid": tid},
            ).scalar()

        print("Données liées :")
        for tbl in DEPENDENT_TABLES:
            print(f"  {tbl}: {counts[tbl]}")
        print("  tenants: 1")
        print()

        if not do_delete:
            print("DRY RUN — rien n'a été supprimé. Ajoute --yes pour exécuter.")
            return

        deleted = {}
        try:
            for tbl in DEPENDENT_TABLES:
                deleted[tbl] = db.session.execute(
                    text(f'DELETE FROM "{tbl}" WHERE tenant_id = :tid'),
                    {"tid": tid},
                ).rowcount
            deleted["tenants"] = db.session.execute(
                text('DELETE FROM tenants WHERE id = :tid'),
                {"tid": tid},
            ).rowcount
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            print(f"ERREUR — suppression annulée (rollback) : {exc}")
            sys.exit(1)

        print("Suppression terminée :")
        for tbl in DEPENDENT_TABLES + ["tenants"]:
            print(f"  {tbl}: {deleted.get(tbl, 0)}")


if __name__ == "__main__":
    main()
