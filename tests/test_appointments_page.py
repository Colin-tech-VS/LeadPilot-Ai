"""Regression tests for the artisan /appointments page."""
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

from app.core.extensions import db
from app.models.appointment import Appointment
from app.models.lead import Lead
from app.models.tenant import Tenant
from app.models.user import User


def _seed_appointments(app):
    with app.app_context():
        tid = uuid.uuid4()
        uid = uuid.uuid4()
        tenant = Tenant(
            id=tid,
            name="Test Co",
            latitude=48.8566,
            longitude=2.3522,
            address="Paris",
            city="Paris",
            plan="pro",
        )
        user = User(
            id=uid,
            tenant_id=tid,
            email=f"appointments-{uid}@example.com",
            password_hash="test",
            role="owner",
        )
        db.session.add(tenant)
        db.session.add(user)

        lead = Lead(
            id=uuid.uuid4(),
            tenant_id=tid,
            name="Client Test",
            phone="+33600000001",
            address="10 rue de Rivoli Paris",
            latitude=48.856,
            longitude=2.352,
        )
        db.session.add(lead)
        db.session.add(
            Appointment(
                tenant_id=tid,
                lead_id=lead.id,
                date_time=datetime(2026, 7, 8, 10, 0, tzinfo=timezone.utc),
                status="tentative",
            )
        )
        db.session.commit()
        return uid, tid


def test_appointments_page_renders(client, app):
    uid, tid = _seed_appointments(app)
    with patch("app.utils.geocoding.geocode_address", return_value=None):
        with client.session_transaction() as sess:
            sess["user_id"] = str(uid)
            sess["tenant_id"] = str(tid)
        response = client.get("/appointments")

    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert "Rendez-vous" in body or "Appointments" in body
    assert "APPOINTMENT_MARKERS" in body
    assert "tentative" in body or "En attente" in body or "Awaiting" in body
