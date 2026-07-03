"""Supprime tous les prospects et rendez-vous (appels test)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.core.extensions import db
from app.models.appointment import Appointment
from app.models.lead import Lead

app = create_app()

with app.app_context():
    appts = Appointment.query.delete()
    leads = Lead.query.delete()
    db.session.commit()
    print(f"Supprimé : {leads} prospect(s), {appts} RDV.")
