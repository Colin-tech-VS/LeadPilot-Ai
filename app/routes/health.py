from flask import Blueprint, jsonify

health_bp = Blueprint("health", __name__)

_API_INDEX = {
    "name": "LeadPilot AI API",
    "status": "ok",
    "endpoints": {
        "health": "GET /health",
        "api_index": "GET /api",
        "register": "POST /auth/register",
        "login": "POST /auth/login",
        "create_tenant": "POST /tenant/create",
        "my_tenant": "GET /tenant/me",
        "create_lead": "POST /leads/create",
        "list_leads": "GET /leads/list",
        "get_lead": "GET /leads/<id>",
        "create_appointment": "POST /appointments/create",
        "list_appointments": "GET /appointments/list",
        "inbound_call": "POST /webhook/inbound-call",
        "voice_inbound": "POST /voice/inbound (Twilio TwiML)",
        "voice_process": "POST /voice/process (Twilio TwiML)",
        "voice_continue": "POST /voice/continue (Twilio TwiML)",
        "voice_inbound_call": "POST /voice/inbound-call (JSON API)",
        "voice_session": "GET /voice/session/<call_id>",
        "test_call": "GET /test-call",
    },
}


@health_bp.route("/api", methods=["GET"])
def api_index():
    return jsonify(_API_INDEX), 200


@health_bp.route("/health", methods=["GET"])
def health_check():
    return jsonify({"status": "ok"}), 200
