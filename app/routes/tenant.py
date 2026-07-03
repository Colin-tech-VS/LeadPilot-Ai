from flask import Blueprint, g, jsonify, request

from app.core.auth import jwt_required, tenant_required
from app.core.errors import ConflictError, NotFoundError
from app.core.extensions import db
from app.models.tenant import Tenant
from app.utils.validation import require_fields, require_json

tenant_bp = Blueprint("tenant", __name__, url_prefix="/tenant")


@tenant_bp.route("/create", methods=["POST"])
@jwt_required
def create_tenant():
    """Create a tenant and associate it with the current user (first-time setup)."""
    if g.tenant_id:
        raise ConflictError("User already belongs to a tenant")

    data = require_json(request.get_json(silent=True))
    require_fields(data, ["name"])

    tenant = Tenant(
        name=data["name"].strip(),
        phone_number=data.get("phone_number"),
    )
    db.session.add(tenant)
    db.session.flush()

    g.current_user.tenant_id = tenant.id
    g.current_user.role = "admin"
    db.session.commit()

    return jsonify({"tenant": tenant.to_dict()}), 201


@tenant_bp.route("/me", methods=["GET"])
@tenant_required
def get_my_tenant():
    tenant = db.session.get(Tenant, g.tenant_id)
    if not tenant:
        raise NotFoundError("Tenant not found")
    return jsonify({"tenant": tenant.to_dict()}), 200
