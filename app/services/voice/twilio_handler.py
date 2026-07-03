import logging
import uuid
from datetime import datetime

from flask import url_for

from app.core.extensions import db
from app.services.availability import book_appointment
from app.models.tenant import Tenant
from app.services.booking_engine import ACTION_BOOK_NOW, ACTION_OUT_OF_ZONE, BookingEngine
from app.services.inbound_call import process_inbound_call
from app.services.lead_extractor import LeadExtractor
from app.services.voice.llm_receptionist import LLMReceptionist
from app.services.voice.speech_to_text import transcribe
from app.services.voice.state import conversation_store, get_call_state
from app.services.voice.twilio_client import TwilioVoiceClient

logger = logging.getLogger(__name__)

MAX_FAILURES = 2
RECORD_MAX_LENGTH = 10


class TwilioVoiceHandler:
    """Production Twilio voice flow: record → transcribe → book → respond."""

    def __init__(self):
        self.extractor = LeadExtractor()
        self.booking_engine = BookingEngine()
        self.llm = LLMReceptionist()

    def handle_inbound(self, tenant_id: str, call_sid: str, caller_phone: str) -> str:
        tenant = db.session.get(Tenant, uuid.UUID(tenant_id))
        company = tenant.name if tenant else "notre service de plomberie"

        get_call_state(call_sid, tenant_id, caller_phone)
        process_url = self._action_url("voice.process_recording", tenant_id)

        client = TwilioVoiceClient()
        client.gather(action=process_url, prompt=f"Bonjour, {company}. Je vous écoute.")
        client.say("Je n'ai pas entendu votre demande. Au revoir.")
        client.hangup()
        return client.to_xml()

    def handle_process(
        self,
        tenant_id: str,
        call_sid: str,
        caller_phone: str,
        recording_url: str | None = None,
        speech_text: str | None = None,
    ) -> str:
        tenant = db.session.get(Tenant, uuid.UUID(tenant_id))
        if not tenant:
            return self._error_twiml("Service temporairement indisponible.")

        state = get_call_state(call_sid, tenant_id, caller_phone)
        process_url = self._action_url("voice.process_recording", tenant_id)
        continue_url = self._action_url("voice.continue_call", tenant_id)

        transcript = (speech_text or "").strip()
        if not transcript and recording_url:
            transcript = transcribe(recording_url)

        if not transcript:
            return self._handle_failure(state, tenant_id, caller_phone, process_url)

        state.failure_count = 0
        state.append_transcript("user", transcript)

        full_transcript = state.full_transcript()
        extracted = self.extractor.extract(full_transcript, caller_phone)
        state.extracted_lead_data = {
            **state.extracted_lead_data,
            **{k: v for k, v in extracted.items() if v},
        }

        booking = self.booking_engine.process_lead(state.extracted_lead_data, tenant)
        state.booking_result = booking
        state.urgency_score = booking.get("priority_score", 0)
        state.booking_action = booking.get("action")

        llm_result = self.llm.process(
            user_text=transcript,
            conversation_history=state.transcripts,
            caller_phone=caller_phone,
            tenant_name=tenant.name,
            tenant_city=tenant.city,
            booking_context=booking,
        )

        ai_response = self._shorten(llm_result.get("spoken_response", ""))
        state.last_ai_response = ai_response
        state.last_intent = llm_result.get("intent")
        state.append_transcript("assistant", ai_response)

        if llm_result.get("booking_action") and booking.get("action") != ACTION_OUT_OF_ZONE:
            state.booking_action = llm_result["booking_action"]
        elif booking.get("action") == ACTION_OUT_OF_ZONE:
            state.booking_action = ACTION_OUT_OF_ZONE

        client = TwilioVoiceClient()
        client.say(ai_response)

        if booking.get("action") == ACTION_OUT_OF_ZONE:
            client.say("Merci pour votre appel. Bonne journée.")
            client.hangup()
            self._persist_lead_only(state, tenant_id, caller_phone, full_transcript, booking)
            conversation_store.save(state)
            return client.to_xml()

        if booking.get("action") == ACTION_BOOK_NOW and not state.lead_id:
            self._persist_lead_and_appointment(state, tenant_id, caller_phone, full_transcript, booking)
            client.say("Parfait, un rendez-vous est confirmé.")
            client.say("Merci pour votre appel. À bientôt.")
            client.hangup()
            conversation_store.save(state)
            return client.to_xml()

        if state.lead_id:
            client.say("Merci pour votre appel. Un technicien vous contactera rapidement.")
            client.hangup()
            conversation_store.save(state)
            return client.to_xml()

        client.gather(action=continue_url, prompt="Souhaitez-vous prendre rendez-vous ?", timeout=6)
        client.say("Je n'ai pas entendu votre réponse. Un plombier vous recontactera. Au revoir.")
        client.hangup()
        conversation_store.save(state)
        return client.to_xml()

    def handle_continue(
        self,
        tenant_id: str,
        call_sid: str,
        caller_phone: str,
        speech_text: str | None = None,
    ) -> str:
        state = get_call_state(call_sid, tenant_id, caller_phone)
        process_url = self._action_url("voice.process_recording", tenant_id)

        if speech_text:
            affirmative = any(
                w in speech_text.lower()
                for w in ("oui", "yes", "d'accord", "ok", "rendez", "confirme", "parfait")
            )
            if affirmative and state.booking_result:
                full_transcript = state.full_transcript()
                booking = state.booking_result
                if booking.get("action") == ACTION_OUT_OF_ZONE:
                    client = TwilioVoiceClient()
                    client.say("Désolé, nous n'intervenons pas dans cette zone.")
                    client.say("Merci pour votre appel. Bonne journée.")
                    client.hangup()
                    conversation_store.save(state)
                    return client.to_xml()
                if not state.lead_id:
                    self._persist_lead_and_appointment(
                        state, tenant_id, caller_phone, full_transcript, booking
                    )
                client = TwilioVoiceClient()
                if booking.get("action") == ACTION_BOOK_NOW:
                    client.say("Parfait, un rendez-vous est confirmé.")
                else:
                    client.say("Très bien, nous vous recontactons rapidement.")
                client.say("Merci pour votre appel. À bientôt.")
                client.hangup()
                conversation_store.save(state)
                return client.to_xml()

        return self.handle_process(
            tenant_id=tenant_id,
            call_sid=call_sid,
            caller_phone=caller_phone,
            speech_text=speech_text,
        )

    def _handle_failure(
        self, state, tenant_id: str, caller_phone: str, process_url: str
    ) -> str:
        state.failure_count += 1
        client = TwilioVoiceClient()

        if state.failure_count < MAX_FAILURES:
            client.gather(action=process_url, prompt="Pouvez-vous répéter votre demande ?")
            conversation_store.save(state)
            return client.to_xml()

        raw_transcript = state.full_transcript() or f"Appel de {caller_phone}"
        try:
            result = process_inbound_call(
                uuid.UUID(tenant_id),
                caller_phone,
                raw_transcript,
            )
            state.lead_id = result.get("lead_id")
            state.booking_status = "failsafe_lead"
            state.failsafe_mode = True
        except Exception:
            logger.exception("Failsafe lead creation failed call=%s", state.call_id)

        client.say(
            "Merci pour votre appel. Un plombier vous recontactera très rapidement."
        )
        client.hangup()
        conversation_store.save(state)
        return client.to_xml()

    def _persist_lead_only(
        self, state, tenant_id: str, caller_phone: str, transcript: str, booking: dict
    ):
        if state.lead_id:
            return
        result = process_inbound_call(uuid.UUID(tenant_id), caller_phone, transcript)
        state.lead_id = result.get("lead_id")
        state.booking_result = result.get("booking", booking)
        state.booking_status = "out_of_zone"

    def _persist_lead_and_appointment(
        self, state, tenant_id: str, caller_phone: str, transcript: str, booking: dict
    ):
        if state.lead_id:
            return

        result = process_inbound_call(uuid.UUID(tenant_id), caller_phone, transcript)
        state.lead_id = result.get("lead_id")
        state.booking_result = result.get("booking", booking)
        state.booking_status = "booked" if booking.get("action") == ACTION_BOOK_NOW else "lead_stored"

        if booking.get("action") == ACTION_BOOK_NOW and booking.get("suggested_slot"):
            slot = datetime.fromisoformat(
                booking["suggested_slot"].replace("Z", "+00:00")
            )
            lead_uuid = uuid.UUID(state.lead_id)
            appointment = book_appointment(uuid.UUID(tenant_id), lead_uuid, slot)
            if appointment:
                booking["suggested_slot"] = appointment.date_time.isoformat()
                state.appointment_id = str(appointment.id)
                state.booking_status = "booked"
                logger.info(
                    "Twilio BOOK_NOW appointment=%s lead=%s slot=%s",
                    appointment.id,
                    state.lead_id,
                    appointment.date_time.isoformat(),
                )
            else:
                state.booking_status = "callback"
                booking["action"] = "CALL_BACK"
                logger.warning(
                    "Twilio slot unavailable lead=%s preferred=%s",
                    state.lead_id,
                    slot.isoformat(),
                )

    def _action_url(self, endpoint: str, tenant_id: str) -> str:
        return url_for(endpoint, tenant_id=tenant_id, _external=True)

    def _shorten(self, text: str, max_sentences: int = 2) -> str:
        if not text:
            return "Je vous écoute, pouvez-vous préciser votre problème ?"
        parts = [p.strip() for p in text.replace("!", ".").replace("?", ".").split(".") if p.strip()]
        return ". ".join(parts[:max_sentences]) + ("." if parts else "")

    def _error_twiml(self, message: str) -> str:
        client = TwilioVoiceClient()
        client.say(message)
        client.hangup()
        return client.to_xml()
