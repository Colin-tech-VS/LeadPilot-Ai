import uuid

from flask import Blueprint, g, jsonify, request

from app.core.auth import tenant_required
from app.core.errors import NotFoundError
from app.core.extensions import db
from app.models.lead import Lead
from app.utils.validation import require_fields, require_json, validate_lead_status

leads_bp = Blueprint("leads", __name__, url_prefix="/leads")


def _get_tenant_lead(lead_id):
    """Fetch a lead scoped to the current tenant."""
    try:
        lead_uuid = uuid.UUID(str(lead_id))
    except ValueError:
        raise NotFoundError("Lead not found")

    lead = Lead.query.filter_by(id=lead_uuid, tenant_id=g.tenant_id).first()
    if not lead:
        raise NotFoundError("Lead not found")
    return lead


@leads_bp.route("/create", methods=["POST"])
@tenant_required
def create_lead():
    data = require_json(request.get_json(silent=True))
    require_fields(data, ["name", "phone"])

    status = data.get("status", "new")
    if status:
        validate_lead_status(status)

    lead = Lead(
        tenant_id=g.tenant_id,
        name=data["name"].strip(),
        phone=data["phone"].strip(),
        address=data.get("address"),
        issue_type=data.get("issue_type"),
        urgency_level=data.get("urgency_level"),
        status=status,
        summary=data.get("summary"),
    )
    db.session.add(lead)
    db.session.commit()

    return jsonify({"lead": lead.to_dict()}), 201


@leads_bp.route("/list", methods=["GET"])
@tenant_required
def list_leads():
    include_archived = request.args.get("include_archived", "").lower() in ("1", "true", "yes")
    query = Lead.query.filter_by(tenant_id=g.tenant_id)
    if not include_archived:
        query = query.filter(Lead.archived_at.is_(None))
    leads = query.order_by(Lead.created_at.desc()).all()
    return jsonify({"leads": [lead.to_dict() for lead in leads]}), 200


@leads_bp.route("/<lead_id>", methods=["GET"])
@tenant_required
def get_lead(lead_id):
    lead = _get_tenant_lead(lead_id)
    return jsonify({"lead": lead.to_dict()}), 200
