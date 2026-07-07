import uuid

from flask import Blueprint, abort, g, jsonify, render_template, request, session, url_for

from app.core.errors import AppError, NotFoundError
from app.core.extensions import db
from app.core.security import rate_limit
from app.core.web_auth import web_tenant_required
from app.models.tenant import Tenant
from app.services.chatbot import process_chat_turn

chatbot_bp = Blueprint("chatbot", __name__)


def _customer_profile_from_session():
    from app.routes.customer import customer_session_payload
    return customer_session_payload()

# Guard against runaway payloads from the public endpoint.
MAX_MESSAGE_LEN = 2000
MAX_HISTORY = 40


@chatbot_bp.route("/chatbot", methods=["GET"])
@web_tenant_required
def chatbot_console():
    """Owner console: live preview + the shareable public link to give clients."""
    tenant = db.session.get(Tenant, g.tenant_id)
    public_url = (
        url_for("web.artisan_profile", slug=tenant.public_slug, _external=True)
        if tenant.is_public and tenant.public_slug
        else url_for("chatbot.public_chat", tenant_id=str(tenant.id), _external=True)
    )
    return render_template(
        "artisan/chatbot.html",
        tenant=tenant,
        public_url=public_url,
    )


@chatbot_bp.route("/artisans/<slug>/chat", methods=["GET"])
def public_chat_by_slug(slug):
    """Public booking chat resolved by artisan slug (annuaire)."""
    from app.services.artisan_directory import get_public_artisan_by_slug

    tenant = get_public_artisan_by_slug(slug)
    if not tenant:
        abort(404)
    return render_template(
        "public/chat_public.html",
        tenant=tenant,
        tenant_id=str(tenant.id),
        artisan_slug=slug,
        customer_profile=_customer_profile_from_session(),
    )


@chatbot_bp.route("/chat/<tenant_id>", methods=["GET"])
def public_chat(tenant_id):
    """Public, no-auth chat page a visitor opens from the shared link."""
    tenant = _load_public_tenant(tenant_id)
    return render_template(
        "public/chat_public.html",
        tenant=tenant,
        tenant_id=str(tenant.id),
        artisan_slug=tenant.public_slug,
        customer_profile=_customer_profile_from_session(),
    )


@chatbot_bp.route("/chat/<tenant_id>/message", methods=["POST"])
@rate_limit(limit=30, window=60, scope="public_chat")
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

    customer_profile = data.get("customer_profile")
    if not isinstance(customer_profile, dict):
        customer_profile = _customer_profile_from_session()

    result = process_chat_turn(
        tenant_id=str(tenant.id),
        history=history,
        message=message,
        existing_lead_id=lead_id,
        customer_profile=_customer_profile_from_session(),
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
