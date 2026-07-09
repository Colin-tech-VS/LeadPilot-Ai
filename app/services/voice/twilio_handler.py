import logging
import uuid

from flask import url_for

from app.core.extensions import db
from app.models.tenant import Tenant
from app.services.booking_engine import ACTION_BOOK_NOW, ACTION_OUT_OF_ZONE, BookingEngine
from app.services.inbound_call import process_inbound_call
from app.services.lead_extractor import LeadExtractor
from app.services.voice import customer_account as vca
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
        "email",
        "Quelle est votre adresse e-mail pour vous envoyer le devis ?",
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
MAX_QUESTION_TURNS = 20
# How many times we re-ask the caller to describe the problem before giving up.
# Without this cap the "issue" slot loops forever whenever the extractor can't
# map a description onto a known issue type (noisy line, unusual wording).
MAX_ISSUE_ASKS = 2

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

        if tenant:
            blocked, reason = self._inbound_blocked(tenant)
            if blocked:
                return self._blocked_twiml(tenant, reason)

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
        return self._blocked_twiml(tenant, "expired")

    def _inbound_blocked(self, tenant) -> tuple[bool, str | None]:
        from app.services.plan_features import inbound_allowed

        allowed, reason = inbound_allowed(tenant)
        return (not allowed, reason)

    def _blocked_twiml(self, tenant, reason: str) -> str:
        client = TwilioVoiceClient()
        direct = (tenant.phone_number or "").strip()
        company = (tenant.name or "").strip()
        if reason == "quota":
            message = (
                "Bonjour, nous avons atteint le nombre d'appels inclus ce mois-ci "
                "pour notre assistant vocal. "
            )
        else:
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
        try:
            return self._handle_process_impl(
                tenant_id, call_sid, caller_phone, recording_url, speech_text
            )
        except Exception:
            logger.exception("Voice handle_process failed call=%s", call_sid)
            return self._error_twiml(
                "Désolé, un problème technique est survenu. "
                "Votre demande est notée et un plombier vous rappellera très rapidement."
            )

    def _handle_process_impl(
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

        blocked, reason = self._inbound_blocked(tenant)
        if blocked:
            return self._blocked_twiml(tenant, reason)

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
        self._ensure_account_flow(state)
        self._update_account_flow(state, transcript)
        self._maybe_finalize_account_creation(state)

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
            if slot not in state.asked_slots:
                state.asked_slots.append(slot)
            if slot == "issue":
                state.issue_ask_count += 1
            elif slot.startswith("account:"):
                counts = state.account_flow.setdefault("ask_counts", {})
                counts[slot] = counts.get(slot, 0) + 1
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
        if not self._slot_filled("issue", state.extracted_lead_data):
            # Only re-ask a bounded number of times. If the extractor still can't
            # classify the problem, stop looping and move on — the full transcript
            # is saved and re-parsed when the lead is created, so nothing is lost.
            if state.issue_ask_count < MAX_ISSUE_ASKS:
                return self._question_for_slot("issue")

        account_q = self._next_account_question(state)
        if account_q:
            return account_q

        lead = state.extracted_lead_data
        af = state.account_flow
        skip = set()
        if af.get("customer_user_id"):
            skip.update({"name", "email"})
        elif af.get("guest_mode") and lead.get("email"):
            skip.add("email")

        for slot, _question in REQUIRED_SLOTS:
            if slot in ("issue",) or slot in skip:
                continue
            if slot in state.asked_slots:
                continue
            if self._slot_filled(slot, lead):
                continue
            return self._question_for_slot(slot)
        return None

    def _question_for_slot(self, slot: str) -> tuple[str, str]:
        for key, question in REQUIRED_SLOTS:
            if key == slot:
                return slot, question
        return slot, ""

    def _ensure_account_flow(self, state) -> None:
        flow = getattr(state, "account_flow", None)
        if not flow:
            state.account_flow = vca.default_account_flow()

    def _last_account_slot(self, state) -> str | None:
        for slot in reversed(state.asked_slots):
            if slot.startswith("account:"):
                return slot
        return None

    def _update_account_flow(self, state, transcript: str) -> None:
        """Interprète la dernière réponse pour le parcours compte client."""
        self._ensure_account_flow(state)
        af = state.account_flow
        lead = state.extracted_lead_data
        last = self._last_account_slot(state)
        if not last:
            return

        text = (transcript or "").strip()
        lower = text.lower()

        if last == "account:has_account":
            if vca.is_yes(lower):
                af["has_account"] = True
            elif vca.is_no(lower):
                af["has_account"] = False
            return

        if last == "account:lookup":
            email = vca.extract_email_from_transcript(text) or lead.get("email")
            if email:
                lead["email"] = email
            user = vca.lookup_customer(
                email=email,
                phone=state.caller_phone,
                name_hint=text,
            )
            if user:
                state.extracted_lead_data = vca.apply_customer_to_lead(user, lead)
                af["customer_user_id"] = str(user.id)
                af["account_done"] = True
                af["lookup_failed"] = False
            else:
                af["lookup_failed"] = True
            return

        if last == "account:lookup_retry":
            if vca.is_yes(lower):
                af["wants_create"] = True
                af["lookup_failed"] = False
            elif vca.is_no(lower):
                af["guest_mode"] = True
            return

        if last == "account:create_pitch":
            if vca.is_yes(lower):
                af["wants_create"] = True
            elif vca.is_no(lower):
                af["guest_mode"] = True
            return

        if last == "account:create_name":
            name = (lead.get("name") or text).strip()
            if name and name.lower() not in ("unknown", "inconnu"):
                lead["name"] = name
                first, last_name = vca.split_name(name)
                af["create_first_name"] = first
                af["create_last_name"] = last_name
            else:
                af["create_name_attempts"] = af.get("create_name_attempts", 0) + 1
            return

        if last == "account:create_email":
            email = vca.extract_email_from_transcript(text)
            if email:
                af["pending_email"] = email
                lead["email"] = email
            else:
                af["create_email_attempts"] = af.get("create_email_attempts", 0) + 1
            return

        if last == "account:email_confirm":
            if vca.is_yes(lower):
                af["email_confirmed"] = True
            elif vca.is_no(lower):
                af["pending_email"] = None
                lead.pop("email", None)
                af.pop("email_confirmed", None)
            else:
                # Not a clear yes/no — the caller likely re-dictated the address.
                new_email = vca.extract_email_from_transcript(text)
                if new_email and new_email != af.get("pending_email"):
                    af["pending_email"] = new_email
                    lead["email"] = new_email
            return

        if last == "account:guest_email":
            email = vca.extract_email_from_transcript(text)
            if email:
                lead["email"] = email
                af["account_done"] = True
            else:
                af["guest_email_attempts"] = af.get("guest_email_attempts", 0) + 1

    def _maybe_finalize_account_creation(self, state) -> None:
        af = state.account_flow
        if not af.get("wants_create") or af.get("customer_user_id"):
            return
        if not af.get("email_confirmed") or not af.get("pending_email"):
            return
        if not af.get("create_first_name"):
            return

        password = vca.generate_voice_password()
        try:
            user = vca.create_customer_account(
                email=af["pending_email"],
                first_name=af.get("create_first_name"),
                last_name=af.get("create_last_name"),
                phone=state.caller_phone,
                password=password,
            )
        except Exception:
            logger.exception("Voice account creation failed call=%s", state.call_id)
            af["create_failed"] = True
            return

        state.extracted_lead_data = vca.apply_customer_to_lead(user, state.extracted_lead_data)
        af["customer_user_id"] = str(user.id)
        af["voice_password"] = password
        af["account_done"] = True
        vca.send_credentials_email(user, password)

    def _resolve_account_stalls(self, state) -> None:
        """Break out of yes/no questions the caller never answers clearly.

        After a slot has been asked twice without a usable reply, pick a sensible
        default so the call keeps moving instead of repeating the same question."""
        af = state.account_flow
        counts = af.get("ask_counts", {})

        def stuck(slot: str) -> bool:
            return counts.get(slot, 0) >= 2

        # Can't tell whether they have an account → assume none and offer to help.
        if af.get("has_account") is None and stuck("account:has_account"):
            af["has_account"] = False
        # Can't confirm creating an account after a failed lookup → continue as guest.
        if (
            af.get("lookup_failed")
            and af.get("wants_create") is None
            and not af.get("guest_mode")
            and stuck("account:lookup_retry")
        ):
            af["guest_mode"] = True
        # No clear answer to the account pitch → continue as guest.
        if (
            not af.get("has_account")
            and af.get("wants_create") is None
            and not af.get("guest_mode")
            and stuck("account:create_pitch")
        ):
            af["guest_mode"] = True
        # Keeps not confirming the e-mail → accept the address as dictated.
        if (
            af.get("pending_email")
            and not af.get("email_confirmed")
            and stuck("account:email_confirm")
        ):
            af["email_confirmed"] = True

    def _next_account_question(self, state) -> tuple[str, str] | None:
        self._ensure_account_flow(state)
        af = state.account_flow
        lead = state.extracted_lead_data

        if af.get("account_done"):
            return None

        self._resolve_account_stalls(state)

        if af.get("has_account") is None:
            if "account:has_account" in state.asked_slots:
                if af.get("has_account") is None:
                    return (
                        "account:has_account",
                        "Je n'ai pas bien compris. Avez-vous déjà un compte client PilotCore ? Dites oui ou non.",
                    )
            return (
                "account:has_account",
                "Avant de continuer, avez-vous déjà un compte client PilotCore ? Dites oui ou non.",
            )

        if af.get("has_account") and not af.get("wants_create") and not af.get("guest_mode"):
            if not af.get("customer_user_id"):
                if af.get("lookup_failed"):
                    if "account:lookup_retry" not in state.asked_slots:
                        return (
                            "account:lookup_retry",
                            "Je ne trouve pas de compte avec ces informations. "
                            "Souhaitez-vous créer un compte gratuit maintenant ? Dites oui ou non.",
                        )
                    if af.get("wants_create") is None:
                        return (
                            "account:lookup_retry",
                            "Dites oui pour créer un compte, ou non pour continuer sans compte.",
                        )
                elif "account:lookup" not in state.asked_slots or not lead.get("email"):
                    return (
                        "account:lookup",
                        "Très bien. Quelle est l'adresse e-mail de votre compte ? "
                        "Vous pouvez l'épeler, par exemple « jean point dupont arobase gmail point com ».",
                    )
            if af.get("customer_user_id"):
                af["account_done"] = True
                return None

        if af.get("wants_create") and not af.get("customer_user_id"):
            if af.get("create_failed"):
                af["guest_mode"] = True
                af["wants_create"] = False
                return None
            if not af.get("create_first_name"):
                # Give up on account creation after two tries: fall back to guest
                # mode so we can still capture the lead without looping.
                if af.get("create_name_attempts", 0) >= 2:
                    af["wants_create"] = False
                    af["guest_mode"] = True
                    return None
                if "account:create_name" not in state.asked_slots or not lead.get("name"):
                    return (
                        "account:create_name",
                        "Parfait ! Quel est votre prénom et votre nom, s'il vous plaît ?",
                    )
            if not af.get("pending_email"):
                # After two failed attempts, stop asking for the e-mail — the
                # lead is saved and the plumber collects it on callback.
                if af.get("create_email_attempts", 0) >= 2:
                    af["email_capture_failed"] = True
                    af["account_done"] = True
                    if "email" not in state.asked_slots:
                        state.asked_slots.append("email")
                    return None
                if "account:create_email" not in state.asked_slots or not lead.get("email"):
                    prompt = (
                        "Merci. Quelle est votre adresse e-mail ? "
                        "Épellez-la clairement, par exemple « jean point dupont arobase gmail point com »."
                        if af.get("create_email_attempts", 0) == 0
                        else "Je n'ai pas bien saisi. Redites votre e-mail lentement : "
                        "d'abord ce qui est avant l'arobase, puis le fournisseur, "
                        "par exemple « gmail point com »."
                    )
                    return ("account:create_email", prompt)
            if af.get("pending_email") and not af.get("email_confirmed"):
                email = af["pending_email"]
                spelled = email.replace("@", " arobase ").replace(".", " point ")
                return (
                    "account:email_confirm",
                    f"Je note l'adresse {spelled}. Est-ce correct ? Dites oui, ou répétez l'e-mail.",
                )
            if af.get("email_confirmed") and not af.get("customer_user_id"):
                self._maybe_finalize_account_creation(state)
            if af.get("customer_user_id"):
                af["account_done"] = True
            return None

        if af.get("guest_mode"):
            if not lead.get("email"):
                # Two attempts max, then proceed without the e-mail so the call
                # never gets stuck re-asking the same question.
                if af.get("guest_email_attempts", 0) >= 2:
                    af["email_capture_failed"] = True
                    af["account_done"] = True
                    if "email" not in state.asked_slots:
                        state.asked_slots.append("email")
                    return None
                prompt = (
                    "Pas de souci. Donnez-moi simplement votre adresse e-mail pour recevoir le devis. "
                    "Épellez-la si besoin, par exemple « marie point martin arobase orange point fr »."
                    if af.get("guest_email_attempts", 0) == 0
                    else "Je n'ai pas bien entendu votre e-mail. Redites-le doucement, "
                    "en disant « arobase » et « point », "
                    "par exemple « marie point martin arobase orange point fr »."
                )
                return ("account:guest_email", prompt)
            af["account_done"] = True
            return None

        if not af.get("has_account"):
            if "account:create_pitch" not in state.asked_slots:
                return (
                    "account:create_pitch",
                    "Avec un compte gratuit PilotCore, vous suivez vos devis et rendez-vous en ligne, "
                    "et c'est beaucoup plus rapide la prochaine fois. "
                    "Je peux vous créer un compte en une minute. Souhaitez-vous que je le fasse ? Dites oui ou non.",
                )
            return (
                "account:create_pitch",
                "Souhaitez-vous créer un compte client gratuit ? Dites oui ou non.",
            )

        return None

    def _slot_filled(self, slot: str, lead: dict) -> bool:
        if slot == "issue":
            return (lead.get("issue_type") or "general_inquiry") not in (None, "", "general_inquiry")
        if slot == "name":
            name = (lead.get("name") or "").strip().lower()
            return bool(name) and name not in ("unknown caller", "unknown", "inconnu")
        if slot == "email":
            email = (lead.get("email") or "").strip()
            return bool(email) and "@" in email
        if slot == "address":
            return bool((lead.get("address") or "").strip())
        if slot == "urgency":
            # The extractor always returns an urgency; only skip the question
            # when the caller clearly flagged an emergency on their own.
            return (lead.get("urgency_level") or "").lower() == "high"
        return True

    def _acknowledge(self, state) -> str:
        lead = state.extracted_lead_data
        # Acknowledge the emergency ONCE — repeating "c'est une urgence" on every
        # question sounds robotic. After that, use short natural fillers.
        if (lead.get("urgency_level") or "").lower() == "high" and not state.urgency_ack_done:
            state.urgency_ack_done = True
            return "Je comprends, c'est urgent, je m'en occupe en priorité."
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

        if state.booking_status in ("booked", "pending_signature"):
            outcome = (
                "Votre demande est enregistrée. "
                "Je vous envoie le devis par e-mail : signez-le en ligne "
                "et réglez l'acompte pour confirmer votre rendez-vous."
            )
        else:
            outcome = (
                "Votre demande est bien enregistrée. "
                "Un plombier vous rappelle très rapidement."
            )

        af = state.account_flow or {}
        if af.get("voice_password"):
            pwd = af["voice_password"]
            outcome += (
                f" Votre compte PilotCore est créé. "
                f"Votre mot de passe temporaire est {vca.spell_for_voice(pwd)}. "
                "Je vous l'envoie aussi par e-mail : pensez à le modifier "
                "dès votre première connexion sur le site PilotCore."
            )
        elif af.get("customer_user_id") and not af.get("voice_password"):
            outcome += " J'ai retrouvé votre compte client PilotCore."

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
                    client.say(
                        "Parfait. Je vous envoie le devis par e-mail : "
                        "signez-le et réglez l'acompte pour confirmer votre rendez-vous."
                    )
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
        result = process_inbound_call(
            uuid.UUID(tenant_id),
            caller_phone,
            transcript,
            lead_override=state.extracted_lead_data,
            send_devis_if_email=True,
        )
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
        result = process_inbound_call(
            uuid.UUID(tenant_id),
            caller_phone,
            transcript,
            lead_override=state.extracted_lead_data,
            send_devis_if_email=True,
        )
        state.lead_id = result.get("lead_id")
        state.booking_result = result.get("booking", booking)
        state.appointment_id = result.get("appointment_id")
        state.booking_status = (
            "pending_signature" if result.get("appointment_id") else "lead_stored"
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
