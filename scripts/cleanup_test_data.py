"""Supprime toutes les données de test (leads, RDV, devis, notifications).

Garde intacts les comptes : tenants (artisans) et users (identifiants,
abonnement, signature, numéro IA…). Après ce script le compte existe toujours
mais est « vide », prêt pour la vraie production.

Usage local :   python scripts/cleanup_test_data.py
Usage Scalingo : scalingo --app <votre-app> run python scripts/cleanup_test_data.py

Ajoutez --dry-run pour voir ce qui serait supprimé sans rien effacer.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.core.extensions import db
from app.models.appointment import Appointment
from app.models.lead import Lead
from app.models.notification import Notification
from app.models.quote import Quote

app = create_app()

DRY_RUN = "--dry-run" in sys.argv

with app.app_context():
    counts = {
        "devis/factures": Quote.query.count(),
        "RDV": Appointment.query.count(),
        "notifications": Notification.query.count(),
        "prospects": Lead.query.count(),
    }

    if DRY_RUN:
        print("DRY RUN — rien ne sera supprimé.")
        for label, n in counts.items():
            print(f"  {n} {label}")
        sys.exit(0)

    # Ordre important : on efface d'abord ce qui référence un lead (clé
    # étrangère), sinon PostgreSQL refuse la suppression du lead.
    quotes = Quote.query.delete()
    appts = Appointment.query.delete()
    notifs = Notification.query.delete()
    leads = Lead.query.delete()
    db.session.commit()

    print(
        f"Supprimé : {leads} prospect(s), {appts} RDV, "
        f"{quotes} devis/facture(s), {notifs} notification(s)."
    )
    print("Comptes (artisans + identifiants + abonnement) conservés.")
