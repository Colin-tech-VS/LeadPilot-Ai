import uuid
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request, send_from_directory

from app.core.errors import AppError, ForbiddenError, UnauthorizedError
from app.core.security import validate_twilio_request
from app.services.voice import VoiceCallHandler, conversation_store
from app.services.voice.twilio_handler import TwilioVoiceHandler

voice_bp = Blueprint("voice", __name__, url_prefix="/voice")

# Twilio-signed TwiML webhooks. The JSON API endpoints (/inbound-call, tests)
# are excluded — they carry no Twilio signature.
_TWILIO_SIGNED_ENDPOINTS = {"voice.inbound", "voice.process_recording", "voice.continue_call"}


@voice_bp.before_request
def _guard_twilio_webhooks():
    from flask import request as _req

    if _req.endpoint in _TWILIO_SIGNED_ENDPOINTS and not validate_twilio_request():
        raise ForbiddenError("Invalid Twilio signature")


def _verify_webhook_secret():
    expected = current_app.config.get("WEBHOOK_SECRET")
    if not expected:
        return
    provided = request.headers.get("X-Webhook-Secret", "")
    if provided != expected:
        raise UnauthorizedError("Invalid webhook secret")


def _normalize_phone_digits(value):
    if not value:
        return ""
    return "".join(c for c in value if c.isdigit())


def _get_tenant_id() -> str:
    from app.core.extensions import db
    from app.models.tenant import Tenant

    tenant_id = request.args.get("tenant_id") or request.form.get("tenant_id")
    if not tenant_id:
        to_number = request.form.get("To") or request.args.get("To")
        if to_number:
            to_digits = _normalize_phone_digits(to_number)
            for tenant in Tenant.query.filter(Tenant.ai_phone_number.isnot(None)).all():
                if _normalize_phone_digits(tenant.ai_phone_number) == to_digits:
                    return str(tenant.id)
        tenant_id = current_app.config.get("TWILIO_DEFAULT_TENANT_ID")
    if not tenant_id:
        raise AppError("tenant_id is required (query param or TWILIO_DEFAULT_TENANT_ID)", status_code=422)
    try:
        uuid.UUID(str(tenant_id))
    except ValueError:
        raise AppError("Invalid tenant_id format", status_code=422)
    return str(tenant_id)


def _twilio_form() -> dict:
    form = request.form or {}
    return {
        "call_sid": form.get("CallSid", ""),
        "caller_phone": form.get("From", ""),
        "recording_url": form.get("RecordingUrl"),
        "speech_text": form.get("SpeechResult"),
    }


def _twiml_response(xml: str):
    return xml, 200, {"Content-Type": "text/xml"}


# --- Twilio production endpoints ---


@voice_bp.route("/inbound", methods=["POST"])
def inbound():
    """Twilio webhook: answer call, greet, start recording."""
    tenant_id = _get_tenant_id()
    data = _twilio_form()
    call_sid = data["call_sid"]
    caller_phone = data["caller_phone"]

    if not call_sid or not caller_phone:
        raise AppError("Missing CallSid or From", status_code=422)

    handler = TwilioVoiceHandler()
    xml = handler.handle_inbound(tenant_id, call_sid, caller_phone)
    return _twiml_response(xml)


@voice_bp.route("/process", methods=["POST"])
def process_recording():
    """Twilio webhook: process recorded audio → AI → respond."""
    tenant_id = _get_tenant_id()
    data = _twilio_form()
    call_sid = data["call_sid"]
    caller_phone = data["caller_phone"]

    if not call_sid:
        raise AppError("Missing CallSid", status_code=422)

    handler = TwilioVoiceHandler()
    xml = handler.handle_process(
        tenant_id=tenant_id,
        call_sid=call_sid,
        caller_phone=caller_phone,
        recording_url=data.get("recording_url"),
        speech_text=data.get("speech_text"),
    )
    return _twiml_response(xml)


@voice_bp.route("/continue", methods=["POST"])
def continue_call():
    """Twilio webhook: handle speech confirmation (Gather)."""
    tenant_id = _get_tenant_id()
    data = _twilio_form()
    call_sid = data["call_sid"]
    caller_phone = data["caller_phone"]

    if not call_sid:
        raise AppError("Missing CallSid", status_code=422)

    handler = TwilioVoiceHandler()
    xml = handler.handle_continue(
        tenant_id=tenant_id,
        call_sid=call_sid,
        caller_phone=caller_phone,
        speech_text=data.get("speech_text"),
    )
    return _twiml_response(xml)


# --- JSON API endpoints (testing / advanced integrations) ---


@voice_bp.route("/inbound-call", methods=["POST"])
def inbound_voice_call():
    _verify_webhook_secret()

    if request.is_json:
        data = request.get_json(silent=True) or {}
    else:
        data = {
            "call_id": request.form.get("CallSid"),
            "tenant_id": request.args.get("tenant_id"),
            "caller_phone": request.form.get("From"),
            "speech_text": request.form.get("SpeechResult"),
            "audio_stream_url": request.form.get("RecordingUrl"),
            "end_call": request.form.get("end_call", "false").lower() == "true",
        }

    if not data.get("call_id"):
        raise AppError("call_id is required", status_code=422)
    tenant_id = data.get("tenant_id") or _get_tenant_id()
    if not data.get("caller_phone"):
        raise AppError("caller_phone is required", status_code=422)

    handler = VoiceCallHandler()
    result = handler.handle(
        call_id=str(data["call_id"]),
        tenant_id=str(tenant_id),
        caller_phone=str(data["caller_phone"]).strip(),
        audio_chunk=data.get("audio_chunk"),
        audio_stream_url=data.get("audio_stream_url"),
        speech_text=data.get("speech_text"),
        end_call=bool(data.get("end_call")),
    )
    return jsonify(result), 200


@voice_bp.route("/session/<call_id>", methods=["GET"])
def get_session(call_id):
    _verify_webhook_secret()
    state = conversation_store.get(call_id)
    if not state:
        raise AppError("Session not found", status_code=404)
    return jsonify(state.to_dict()), 200


@voice_bp.route("/audio/<filename>", methods=["GET"])
def serve_audio(filename):
    audio_dir = Path(current_app.static_folder) / "audio" / "voice"
    return send_from_directory(audio_dir, filename, mimetype="audio/mpeg")
