import logging
import uuid
from datetime import datetime

from app.core.extensions import db
from app.services.availability import book_appointment
from app.models.lead import Lead
from app.models.tenant import Tenant
from app.services.booking_engine import (
    ACTION_BOOK_NOW,
    ACTION_CALL_BACK,
    ACTION_OUT_OF_ZONE,
    ACTION_SEND_QUOTE,
    BookingEngine,
)
from app.services.lead_extractor import LeadExtractor
from app.services.notifications import notify_high_urgency_lead
from app.services.voice.conversation_state import ConversationState, conversation_store
from app.services.voice.llm_receptionist import LLMReceptionist
from app.services.voice.speech_to_text import SpeechToText
from app.services.voice.text_to_speech import TextToSpeech
from app.utils.i18n import canonicalize_issue

logger = logging.getLogger(__name__)

FAILSAFE_SMS_MESSAGE = (
    "Nous avons bien reçu votre message. Un plombier vous rappellera sous peu."
)


class VoiceCallHandler:
    """Orchestrate real-time voice call pipeline: STT → LLM → TTS → booking."""

    def __init__(self):
        self.stt = SpeechToText()
        self.llm = LLMReceptionist()
        self.tts = TextToSpeech()
        self.booking_engine = BookingEngine()
        self.lead_extractor = LeadExtractor()

    def handle(
        self,
        call_id: str,
        tenant_id: str,
        caller_phone: str,
        audio_chunk: str | bytes | None = None,
        audio_stream_url: str | None = None,
        speech_text: str | None = None,
        end_call: bool = False,
    ) -> dict:
        tenant = db.session.get(Tenant, uuid.UUID(tenant_id))
        if not tenant:
            raise ValueError("Tenant not found")

        state = conversation_store.get_or_create(call_id, tenant_id, caller_phone)

        user_text = self._get_user_text(speech_text, audio_chunk, audio_stream_url, state)
        is_greeting_turn = not user_text and not end_call and state.turn_count == 0

        if not user_text and not end_call and not is_greeting_turn:
            return self._failsafe_response(
                state,
                tenant,
                caller_phone,
                reason="stt_failed",
            )

        if user_text:
            state.append_transcript("user", user_text)
            self._merge_extracted_data(state, user_text, caller_phone)

        booking_preview = None
        if state.extracted_lead_data:
            booking_preview = self.booking_engine.process_lead(
                state.extracted_lead_data, tenant
            )
            state.urgency_score = booking_preview.get("priority_score", 0)
            state.booking_action = booking_preview.get("action")

        llm_input = user_text if user_text else "[APPEL ENTRANT — accueillir le client chaleureusement]"
        llm_result = self.llm.process(
            user_text=llm_input,
            conversation_history=state.transcripts,
            caller_phone=caller_phone,
            tenant_name=tenant.name,
            tenant_city=tenant.city,
            booking_context=booking_preview,
        )

        spoken = llm_result["spoken_response"]
        state.last_ai_response = spoken
        state.last_intent = llm_result["intent"]
        state.append_transcript("assistant", spoken)

        if llm_result.get("extracted_lead_data"):
            state.extracted_lead_data.update(
                {k: v for k, v in llm_result["extracted_lead_data"].items() if v}
            )

        if llm_result.get("booking_action") and (
            not booking_preview or booking_preview.get("action") != ACTION_OUT_OF_ZONE
        ):
            state.booking_action = llm_result["booking_action"]
        elif booking_preview and booking_preview.get("action") == ACTION_OUT_OF_ZONE:
            state.booking_action = ACTION_OUT_OF_ZONE

        continue_call = llm_result.get("continue_call", True) and not end_call
        if llm_result["intent"] == "end_call":
            continue_call = False

        if not continue_call or end_call:
            self._finalize_call(state, tenant, caller_phone)

        conversation_store.save(state)

        tts_result = self.tts.synthesize(spoken, call_id=call_id)

        return {
            "audio_response": tts_result.get("audio_base64"),
            "audio_url": tts_result.get("audio_url"),
            "text": spoken,
            "continue_call": continue_call,
            "intent": llm_result["intent"],
            "call_state": state.to_dict(),
        }

    def _get_user_text(
        self,
        speech_text: str | None,
        audio_chunk: str | bytes | None,
        audio_stream_url: str | None,
        state: ConversationState,
    ) -> str:
        if speech_text and speech_text.strip():
            return speech_text.strip()

        if audio_chunk or audio_stream_url:
            text = self.stt.transcribe(audio_chunk, audio_stream_url)
            if text:
                return text

        return ""

    def _merge_extracted_data(self, state: ConversationState, user_text: str, caller_phone: str):
        extracted = self.lead_extractor.extract(user_text, caller_phone)
        for key, value in extracted.items():
            if value and not state.extracted_lead_data.get(key):
                state.extracted_lead_data[key] = value
        if caller_phone and not state.extracted_lead_data.get("phone"):
            state.extracted_lead_data["phone"] = caller_phone

    def _finalize_call(self, state: ConversationState, tenant: Tenant, caller_phone: str):
        full_transcript = state.full_transcript()
        lead_data = dict(state.extracted_lead_data)

        if not lead_data.get("summary"):
            lead_data["summary"] = full_transcript[:1000]

        if not lead_data.get("phone"):
            lead_data["phone"] = caller_phone

        if lead_data.get("issue_type"):
            lead_data["issue_type"] = canonicalize_issue(lead_data["issue_type"])

        booking = self.booking_engine.process_lead(lead_data, tenant)
        if state.booking_action and booking.get("action") != ACTION_OUT_OF_ZONE:
            booking["action"] = state.booking_action

        state.booking_result = booking
        state.urgency_score = booking.get("priority_score", 0)

        lead = Lead(
            tenant_id=uuid.UUID(state.tenant_id),
            name=lead_data.get("name") or "Unknown Caller",
            phone=lead_data.get("phone") or caller_phone,
            address=lead_data.get("address"),
            latitude=lead_data.get("latitude"),
            longitude=lead_data.get("longitude"),
            issue_type=lead_data.get("issue_type"),
            urgency_level=lead_data.get("urgency_level"),
            summary=lead_data.get("summary"),
            status="new",
        )
        lead.set_booking(booking)
        db.session.add(lead)
        db.session.flush()

        state.lead_id = str(lead.id)
        action = booking.get("action")

        if action == ACTION_BOOK_NOW and booking.get("suggested_slot"):
            slot = datetime.fromisoformat(
                booking["suggested_slot"].replace("Z", "+00:00")
            )
            appointment = book_appointment(uuid.UUID(state.tenant_id), lead.id, slot)
            if appointment:
                booking["suggested_slot"] = appointment.date_time.isoformat()
                lead.status = "booked"
                state.appointment_id = str(appointment.id)
                state.booking_status = "booked"
                logger.info(
                    "Voice call booked appointment %s for lead %s slot=%s",
                    appointment.id,
                    lead.id,
                    appointment.date_time.isoformat(),
                )
            else:
                state.booking_status = "callback"
                booking["action"] = ACTION_CALL_BACK
                logger.warning("Voice call slot unavailable for lead %s", lead.id)
        elif action == ACTION_CALL_BACK:
            state.booking_status = "callback"
        elif action == ACTION_SEND_QUOTE:
            state.booking_status = "quote"
        elif action == ACTION_OUT_OF_ZONE:
            state.booking_status = "out_of_zone"
        else:
            state.booking_status = "lead_stored"

        db.session.commit()

        if lead_data.get("urgency_level") == "high":
            notify_high_urgency_lead(lead, tenant)

    def _failsafe_response(
        self,
        state: ConversationState,
        tenant: Tenant,
        caller_phone: str,
        reason: str,
    ) -> dict:
        state.failsafe_mode = True
        raw = state.full_transcript() or f"Appel entrant de {caller_phone}"
        state.extracted_lead_data = {
            "phone": caller_phone,
            "summary": raw,
            "urgency_level": "medium",
        }
        state.append_transcript("assistant", FAILSAFE_SMS_MESSAGE)
        state.last_ai_response = FAILSAFE_SMS_MESSAGE
        state.booking_action = ACTION_CALL_BACK
        self._finalize_call(state, tenant, caller_phone)
        conversation_store.save(state)

        tts_result = self.tts.synthesize(FAILSAFE_SMS_MESSAGE, call_id=state.call_id)

        logger.warning("Voice failsafe triggered call=%s reason=%s", state.call_id, reason)

        return {
            "audio_response": tts_result.get("audio_base64"),
            "audio_url": tts_result.get("audio_url"),
            "text": FAILSAFE_SMS_MESSAGE,
            "continue_call": False,
            "intent": "callback",
            "failsafe": True,
            "call_state": state.to_dict(),
        }
