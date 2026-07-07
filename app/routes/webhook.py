import hmac
import uuid

from flask import Blueprint, current_app, jsonify, request

from app.core.errors import AppError, UnauthorizedError
from app.core.security import rate_limit
from app.services.inbound_call import process_inbound_call
from app.utils.validation import require_fields, require_json

webhook_bp = Blueprint("webhook", __name__, url_prefix="/webhook")


def _verify_webhook_secret():
    expected = current_app.config.get("WEBHOOK_SECRET")
    if not expected:
        if current_app.config.get("ENV") == "production":
            raise UnauthorizedError("Webhook secret not configured")
        return
    provided = request.headers.get("X-Webhook-Secret", "")
    if not hmac.compare_digest(provided, expected):
        raise UnauthorizedError("Invalid webhook secret")


@webhook_bp.route("/inbound-call", methods=["POST"])
@rate_limit(limit=60, window=60, scope="webhook_inbound")
def inbound_call():
    _verify_webhook_secret()

    data = require_json(request.get_json(silent=True))
    require_fields(data, ["tenant_id", "phone", "transcript"])

    try:
        tenant_id = uuid.UUID(str(data["tenant_id"]))
    except ValueError:
        raise AppError("Invalid tenant_id format", status_code=422)

    result = process_inbound_call(
        tenant_id=tenant_id,
        phone=data["phone"].strip(),
        transcript=data["transcript"].strip(),
    )
    return jsonify(result), 201
