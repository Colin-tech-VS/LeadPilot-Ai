"""Regression: archive lead from artisan dashboard must not 500."""

import uuid
from datetime import datetime, timezone

from app.core.extensions import db
from app.models.appointment import Appointment
from app.models.lead import Lead
from app.models.tenant import Tenant
from app.models.user import User


def _seed_lead_with_appointment(app):
    with app.app_context():
        tid = uuid.uuid4()
        uid = uuid.uuid4()
        tenant = Tenant(
            id=tid,
            name="Archive Test Co",
            latitude=48.8566,
            longitude=2.3522,
            address="Paris",
            city="Paris",
            plan="pro",
        )
        user = User(
            id=uid,
            tenant_id=tid,
            email=f"archive-{uid}@example.com",
            password_hash="test",
            role="owner",
        )
        lead = Lead(
            id=uuid.uuid4(),
            tenant_id=tid,
            name="Client Archive",
            phone="+33600000002",
            address="12 rue de Rivoli Paris",
        )
        appt = Appointment(
            tenant_id=tid,
            lead_id=lead.id,
            date_time=datetime(2026, 7, 9, 14, 0, tzinfo=timezone.utc),
            status="confirmed",
        )
        db.session.add_all([tenant, user, lead, appt])
        db.session.commit()
        return uid, tid, lead.id


def test_archive_lead_marks_completed(client, app):
    uid, tid, lead_id = _seed_lead_with_appointment(app)
    with client.session_transaction() as sess:
        sess["user_id"] = str(uid)
        sess["tenant_id"] = str(tid)

    response = client.post(
        f"/leads/{lead_id}/archive",
        headers={"Referer": "/dashboard"},
    )
    assert response.status_code == 302

    with app.app_context():
        lead = db.session.get(Lead, lead_id)
        appt = Appointment.query.filter_by(lead_id=lead_id).first()
        assert lead.archived_at is not None
        assert appt.status == "completed"
