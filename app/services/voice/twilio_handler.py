import logging
import uuid

from flask import url_for

from app.core.extensions import db
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

# Information a plumber needs before dispatching — asked one by one until the
# caller has given everything. Order matters (problem first, then identity,
# address and finally urgency).
REQUIRED_SLOTS = [
    (
        "issue",
        "Pouvez-vous me décrire précisément le problème ? "
        "Par exemple une fuite d'eau, une canalisation bouchée, ou un chauffe-eau en panne.",
    ),
    (
        "name",
        "Pouvez-vous me donner votre nom et votre prénom, s'il vous plaît ?",
    ),
    (
        "address",
        "Quelle est l'adresse complète de l'intervention, "
        "avec le numéro, la rue, le code postal et la ville ?",
    ),
    (
        "urgency",
        "Est-ce que c'est urgent ? "
        "Y a-t-il par exemple une fuite importante ou un dégât des eaux en ce moment ?",
    ),
]
# Safety guard against an endless loop (counts both caller and assistant turns).
MAX_QUESTION_TURNS = 12

ISSUE_LABELS_FR = {
    "leak": "fuite d'eau",
    "clogged_drain": "canalisation bouchée",
    "clogged_toilet": "WC bouché",
    "water_heater": "chauffe-eau",
    "toilet": "WC",
    "pipe_issue": "canalisation",
    "burst_pipe": "canalisation percée",
    "flooding": "dégât des eaux",
    "no_water": "coupure d'eau",
}


class TwilioVoiceHandler:
    """Production Twilio voice flow: record → transcribe → book → respond."""

    def __init__(self):
        self.extractor = LeadExtractor()
        self.booking_engine = BookingEngine()
        self.llm = LLMReceptionist()

    def handle_inbound(self, tenant_id: str, call_sid: str, caller_phone: str) -> str:
        tenant = db.session.get(Tenant, uuid.UUID(tenant_id))

        # Free-trial gate: once the trial has expired (and no paid plan) the AI
        # line no longer answers — it politely points the caller to the plumber's
        # own number instead of taking the request.
        if tenant and not tenant.subscription_active:
            return self._subscription_expired_twiml(tenant)

        get_call_state(call_sid, tenant_id, caller_phone)
        process_url = self._action_url("voice.process_recording", tenant_id)

        client = TwilioVoiceClient()
        client.gather(
            action=process_url,
            prompt=self._greeting(tenant),
        )
        # Safety net only: with actionOnEmptyResult the gather posts to /process
        # even on silence, so this runs only if that redirect never fires.
        client.redirect(process_url)
        return client.to_xml()

    def _greeting(self, tenant) -> str:
        """Opening line: the AI introduces itself by the name the plumber chose
        and as the assistant of the plumber, e.g. "Bonjour, je suis Léa,
        l'assistante de Martin, comment puis-je vous aider ?"."""
        # The plumber's first name when known, otherwise the company name.
        plumber_name = None
        if tenant:
            plumber_name = (tenant.first_name or "").strip() or (tenant.name or "").strip() or None
        ai_name = (tenant.ai_assistant_name or "").strip() if tenant else ""

        if ai_name and plumber_name:
            return f"Bonjour, je suis {ai_name}, l'assistante de {plumber_name}, comment puis-je vous aider ?"
        if ai_name:
            return f"Bonjour, je suis {ai_name}, votre assistante de dépannage, comment puis-je vous aider ?"
        if plumber_name:
            return f"Bonjour, je suis l'assistante de {plumber_name}, comment puis-je vous aider ?"
        return "Bonjour, je suis votre assistante de dépannage, comment puis-je vous aider ?"

    def _subscription_expired_twiml(self, tenant) -> str:
        client = TwilioVoiceClient()
        direct = (tenant.phone_number or "").strip()
        company = (tenant.name or "").strip()
        message = "Bonjour, notre assistant vocal n'est pas disponible pour le moment. "
        if direct:
            digits = " ".join(direct)
            who = f"directement {company}" if company else "directement votre plombier"
            message += f"Vous pouvez joindre {who} au {digits}. Merci et à bientôt."
        else:
            message += "Merci de rappeler ultérieurement. À bientôt."
        client.say(message)
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

        transcript = (speech_text or "").strip()
        if not transcript and recording_url:
            transcript = transcribe(recording_url)

        if not transcript:
            return self._handle_failure(state, tenant_id, caller_phone, process_url)

        state.failure_count = 0
        state.append_transcript("user", transcript)

        # Extract only from what the caller said — never from the receptionist's
        # own questions (otherwise "quelle est votre adresse" pollutes the parse).
        caller_transcript = state.user_transcript()
        extracted = self.extractor.extract(caller_transcript, caller_phone)
        state.extracted_lead_data = {
            **state.extracted_lead_data,
            **{k: v for k, v in extracted.items() if v},
        }

        booking = self.booking_engine.process_lead(state.extracted_lead_data, tenant)
        state.booking_result = booking
        state.urgency_score = booking.get("priority_score", 0)
        state.booking_action = booking.get("action")

        client = TwilioVoiceClient()

        # Once we know the address, refuse politely if it is out of the service
        # zone. The decision is deterministic (computed distance), never the raw
        # LLM text — the model sometimes refuses an in-zone address by city name.
        if state.extracted_lead_data.get("address") and booking.get("action") == ACTION_OUT_OF_ZONE:
            state.booking_action = ACTION_OUT_OF_ZONE
            self._persist_lead_only(state, tenant_id, caller_phone, caller_transcript, booking)
            label = tenant.city or "notre secteur"
            client.say(
                f"Je suis désolée, nous intervenons uniquement autour de {label}. "
                "Je vous conseille de contacter un plombier plus proche de chez vous. "
                "Merci de votre appel et bonne journée."
            )
            client.hangup()
            conversation_store.save(state)
            return client.to_xml()

        # Keep asking until every piece of information a plumber needs is
        # collected — problem, name, address and urgency.
        nxt = self._next_question(state)
        if nxt and state.turn_count < MAX_QUESTION_TURNS:
            slot, question = nxt
            state.asked_slots.append(slot)
            prompt = f"{self._acknowledge(state)} {question}"
            state.last_ai_response = prompt
            state.append_transcript("assistant", prompt)
            client.gather(action=process_url, prompt=prompt)
            client.say(
                "Je n'ai pas bien entendu. "
                "Un plombier vous rappellera très rapidement. Merci de votre appel."
            )
            client.hangup()
            conversation_store.save(state)
            return client.to_xml()

        # All information gathered → capture the lead (and book the appointment
        # when the call qualifies for BOOK_NOW) so every call lands in the dashboard.
        if not state.lead_id:
            self._persist_lead_and_appointment(state, tenant_id, caller_phone, caller_transcript, booking)

        closing = self._closing_message(state)
        state.last_ai_response = closing
        state.append_transcript("assistant", closing)
        client.say(closing)
        client.hangup()
        conversation_store.save(state)
        return client.to_xml()

    def _next_question(self, state) -> tuple[str, str] | None:
        """Return the next (slot, question) still missing, or None when done."""
        lead = state.extracted_lead_data
        for slot, question in REQUIRED_SLOTS:
            if slot in state.asked_slots:
                continue
            if self._slot_filled(slot, lead):
                continue
            return slot, question
        return None

    def _slot_filled(self, slot: str, lead: dict) -> bool:
        if slot == "issue":
            return (lead.get("issue_type") or "general_inquiry") not in (None, "", "general_inquiry")
        if slot == "name":
            name = (lead.get("name") or "").strip().lower()
            return bool(name) and name not in ("unknown caller", "unknown", "inconnu")
        if slot == "address":
            return bool((lead.get("address") or "").strip())
        if slot == "urgency":
            # The extractor always returns an urgency; only skip the question
            # when the caller clearly flagged an emergency on their own.
            return (lead.get("urgency_level") or "").lower() == "high"
        return True

    def _acknowledge(self, state) -> str:
        lead = state.extracted_lead_data
        if (lead.get("urgency_level") or "").lower() == "high":
            return "Je comprends, c'est une urgence, je note tout de suite."
        acks = ["Entendu.", "Très bien.", "D'accord, je note.", "Parfait."]
        return acks[state.turn_count % len(acks)]

    def _closing_message(self, state) -> str:
        lead = state.extracted_lead_data
        name = (lead.get("name") or "").strip()
        first = name.split()[0] if name else ""
        greeting = (
            f"Merci {first}."
            if first and first.lower() not in ("unknown", "inconnu")
            else "Merci."
        )

        recap_bits = []
        issue = lead.get("issue_type")
        if issue and issue != "general_inquiry":
            recap_bits.append(f"votre problème de {ISSUE_LABELS_FR.get(issue, 'plomberie')}")
        if lead.get("address"):
            recap_bits.append(f"à l'adresse {lead['address']}")
        recap = ("J'ai bien noté " + ", ".join(recap_bits) + ". ") if recap_bits else ""

        if state.booking_status == "booked":
            outcome = (
                "Votre demande est enregistrée et un rendez-vous est planifié. "
                "Un plombier vous rappelle pour confirmer l'horaire."
            )
        else:
            outcome = (
                "Votre demande est bien enregistrée. "
                "Un plombier vous rappelle très rapidement."
            )
        return f"{greeting} {recap}{outcome} Bonne journée."

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
            prompt = (
                "Je vous écoute, prenez votre temps. "
                "Expliquez-moi simplement ce qui se passe, avec vos mots."
                if state.turn_count == 0
                else "Je ne vous ai pas bien entendu. Pouvez-vous répéter, s'il vous plaît ?"
            )
            client.gather(action=process_url, prompt=prompt)
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

        # process_inbound_call already creates the lead AND books the appointment
        # when the call qualifies for BOOK_NOW — don't book a second one here.
        result = process_inbound_call(uuid.UUID(tenant_id), caller_phone, transcript)
        state.lead_id = result.get("lead_id")
        state.booking_result = result.get("booking", booking)
        state.appointment_id = result.get("appointment_id")
        state.booking_status = "booked" if result.get("appointment_id") else "lead_stored"

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
