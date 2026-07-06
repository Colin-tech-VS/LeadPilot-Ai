import uuid
from datetime import datetime

from flask import Blueprint, g, jsonify, request

from app.core.auth import tenant_required
from app.core.errors import AppError, NotFoundError
from app.core.extensions import db
from app.models.appointment import Appointment
from app.models.lead import Lead
from app.services.availability import is_slot_available, normalize_slot
from app.utils.validation import require_fields, require_json

appointments_bp = Blueprint("appointments", __name__, url_prefix="/appointments")


def _parse_datetime(value):
    if not value:
        raise AppError("date_time is required", status_code=422)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        raise AppError("Invalid date_time format. Use ISO 8601.", status_code=422)


@appointments_bp.route("/create", methods=["POST"])
@tenant_required
def create_appointment():
    data = require_json(request.get_json(silent=True))
    require_fields(data, ["lead_id", "date_time"])

    try:
        lead_uuid = uuid.UUID(str(data["lead_id"]))
    except ValueError:
        raise NotFoundError("Lead not found")

    lead = Lead.query.filter_by(id=lead_uuid, tenant_id=g.tenant_id).first()
    if not lead:
        raise NotFoundError("Lead not found")

    slot = normalize_slot(_parse_datetime(data["date_time"]))
    if not is_slot_available(g.tenant_id, slot):
        raise AppError(
            "This time slot is already booked or outside business hours. "
            "Choose another slot.",
            status_code=409,
        )

    appointment = Appointment(
        tenant_id=g.tenant_id,
        lead_id=lead.id,
        date_time=slot,
        status=data.get("status", "scheduled"),
    )
    db.session.add(appointment)
    db.session.commit()

    return jsonify({"appointment": appointment.to_dict()}), 201


@appointments_bp.route("/list", methods=["GET"])
@tenant_required
def list_appointments():
    appointments = (
        Appointment.active_query(g.tenant_id)
        .order_by(Appointment.date_time.asc())
        .all()
    )
    return jsonify(
        {"appointments": [appt.to_dict() for appt in appointments]}
    ), 200
