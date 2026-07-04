import uuid

from flask import Blueprint, abort, g, jsonify, render_template, request, url_for

from app.core.errors import AppError, NotFoundError
from app.core.extensions import db
from app.core.web_auth import web_tenant_required
from app.models.tenant import Tenant
from app.services.chatbot import process_chat_turn

chatbot_bp = Blueprint("chatbot", __name__)

# Guard against runaway payloads from the public endpoint.
MAX_MESSAGE_LEN = 2000
MAX_HISTORY = 40


@chatbot_bp.route("/chatbot", methods=["GET"])
@web_tenant_required
def chatbot_console():
    """Owner console: live preview + the shareable public link to give clients."""
    tenant = db.session.get(Tenant, g.tenant_id)
    public_url = url_for("chatbot.public_chat", tenant_id=str(tenant.id), _external=True)
    return render_template(
        "chatbot.html",
        tenant=tenant,
        public_url=public_url,
    )


@chatbot_bp.route("/chat/<tenant_id>", methods=["GET"])
def public_chat(tenant_id):
    """Public, no-auth chat page a visitor opens from the shared link."""
    tenant = _load_public_tenant(tenant_id)
    return render_template(
        "chat_public.html",
        tenant=tenant,
        tenant_id=str(tenant.id),
    )


@chatbot_bp.route("/chat/<tenant_id>/message", methods=["POST"])
def public_chat_message(tenant_id):
    """One chat exchange. Used by both the public page and the owner preview."""
    tenant = _load_public_tenant(tenant_id)

    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    if not message:
        raise AppError("message is required", status_code=422)
    if len(message) > MAX_MESSAGE_LEN:
        message = message[:MAX_MESSAGE_LEN]

    history = data.get("history")
    if not isinstance(history, list):
        history = []
    history = _sanitize_history(history)

    lead_id = data.get("lead_id") or None
    if lead_id:
        try:
            uuid.UUID(str(lead_id))
        except (ValueError, TypeError):
            lead_id = None

    result = process_chat_turn(
        tenant_id=str(tenant.id),
        history=history,
        message=message,
        existing_lead_id=lead_id,
    )
    return jsonify(result), 200


def _load_public_tenant(tenant_id):
    try:
        tid = uuid.UUID(str(tenant_id))
    except (ValueError, TypeError):
        abort(404)
    tenant = db.session.get(Tenant, tid)
    if not tenant:
        abort(404)
    return tenant


def _sanitize_history(history):
    clean = []
    for turn in history[-MAX_HISTORY:]:
        if not isinstance(turn, dict):
            continue
        role = "user" if turn.get("role") == "user" else "assistant"
        text = (turn.get("text") or "").strip()
        if not text:
            continue
        clean.append({"role": role, "text": text[:MAX_MESSAGE_LEN]})
    return clean
