import base64
import json
import logging
import os
import uuid
from pathlib import Path

from flask import current_app, url_for

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a real-time voice receptionist for a plumbing company, talking to a caller on the phone.

Your goals:
1. Answer naturally and warmly, like a real human receptionist.
2. Understand the caller's plumbing problem.
3. Collect the key information a plumber needs: name, full postal address, urgency.
4. Book an appointment ONLY if the address is within the service area.
5. Always try to schedule a job when the address is nearby.

MAXIMISE COMPREHENSION — the audio is a phone line and speech recognition is imperfect:
- Ask ONE thing at a time. Never stack several questions in one turn.
- Use short, simple sentences and everyday words.
- When the caller gives important data (name, address, phone, appointment time), REPEAT IT BACK to confirm — e.g. "Vous êtes bien au 12 rue des Lilas à Lyon, c'est ça ?".
- If what you heard is unclear, empty, or contradictory, DO NOT guess. Politely ask them to repeat, more slowly, or to spell it ("pouvez-vous répéter le numéro chiffre par chiffre ?").
- For an address, make sure you have the street number, street name, postal code and city before booking.
- Never invent a name or address you did not clearly hear — leave the field null instead.
- Acknowledge briefly before your question ("D'accord.", "Je comprends.") so it feels like a real conversation.

IMPORTANT — Service area rules:
- The context may contain a "STATUT" line telling you whether the address is in or out of the service area. ALWAYS trust it.
- Only refuse (booking_action: OUT_OF_ZONE) when STATUT explicitly says "HORS ZONE".
- NEVER decide based on the city name — a different city can still be within the radius. Judge only by the STATUT / distance provided.
- If no STATUT is given yet, keep helping and aim to book; do not refuse.

Never mention you are an AI or a machine.

Keep spoken responses short: 1 to 2 sentences maximum.

Respond in French unless the caller clearly speaks English.

Return ONLY valid JSON with these keys:
- spoken_response (string, what you say aloud, max 2 sentences)
- intent (one of: book, info, callback, end_call)
- extracted_lead_data (object with name, phone, address, issue_type, urgency_level, summary — use null for any field you did not clearly understand)
- booking_action (one of: BOOK_NOW, CALL_BACK, SEND_QUOTE, OUT_OF_ZONE, null)
- continue_call (boolean)
"""


class LLMReceptionist:
    """Real-time conversational AI receptionist powered by Mistral."""

    def process(
        self,
        user_text: str,
        conversation_history: list[dict],
        caller_phone: str,
        tenant_name: str = "notre entreprise",
        booking_context: dict | None = None,
        tenant_city: str | None = None,
    ) -> dict:
        api_key = current_app.config.get("MISTRAL_API_KEY") or os.environ.get("MISTRAL_API_KEY")
        if not api_key:
            return self._fallback_response(user_text)

        try:
            return self._process_mistral(
                user_text,
                conversation_history,
                caller_phone,
                tenant_name,
                booking_context,
                tenant_city,
                api_key,
            )
        except Exception:
            logger.exception("LLM receptionist failed")
            return self._fallback_response(user_text)

    def _process_mistral(
        self,
        user_text: str,
        conversation_history: list[dict],
        caller_phone: str,
        tenant_name: str,
        booking_context: dict | None,
        tenant_city: str | None,
        api_key: str,
    ) -> dict:
        from mistralai import Mistral

        model = current_app.config.get("MISTRAL_MODEL", "mistral-small-latest")
        client = Mistral(api_key=api_key)

        history_text = "\n".join(
            f"{'Client' if t['role'] == 'user' else 'Réceptionniste'}: {t['text']}"
            for t in conversation_history[-8:]
        )

        slot_info = ""
        if booking_context and booking_context.get("suggested_slot"):
            slot_info = (
                f"\nCréneau disponible (vérifié, sans conflit): "
                f"{booking_context['suggested_slot']}"
            )
        if booking_context and booking_context.get("slot_unavailable"):
            slot_info += "\nAUCUN créneau libre — proposer un rappel, ne pas confirmer de RDV"

        zone_info = self._format_zone_context(booking_context, tenant_city)

        user_prompt = (
            f"Entreprise: {tenant_name}\n"
            f"Téléphone appelant: {caller_phone}\n"
            f"{zone_info}{slot_info}\n\n"
            f"Historique:\n{history_text}\n\n"
            f"Nouveau message client: {user_text}\n\n"
            "Réponds en JSON uniquement."
        )

        response = client.chat.complete(
            model=model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.4,
            max_tokens=300,
        )

        raw = response.choices[0].message.content
        data = json.loads(raw)
        return self._normalize_response(data, booking_context)

    def _format_zone_context(self, booking_context: dict | None, tenant_city: str | None) -> str:
        if not booking_context:
            return ""

        label = booking_context.get("service_area_label") or tenant_city or "notre zone"
        radius = booking_context.get("service_radius_km", 30)
        lines = [f"Zone d'intervention: {label}, rayon maximum {radius} km"]

        status = booking_context.get("zone_status")
        distance = booking_context.get("distance_km")
        if status == "out_of_zone":
            lines.append(
                f"STATUT: HORS ZONE — adresse à {distance} km, refuser le rendez-vous (booking_action: OUT_OF_ZONE)"
            )
        elif status == "address_unverified":
            lines.append("STATUT: adresse non vérifiable — ne pas confirmer de RDV, proposer un rappel")
        elif status == "in_zone" and distance is not None:
            lines.append(f"STATUT: dans la zone ({distance} km) — RDV possible si urgent")

        if booking_context.get("out_of_zone"):
            lines.append("Action requise: OUT_OF_ZONE — refuser poliment le rendez-vous")

        return "\n".join(lines)

    def _normalize_response(self, data: dict, booking_context: dict | None = None) -> dict:
        spoken = (data.get("spoken_response") or "").strip()
        if not spoken:
            spoken = "Je vous écoute, pouvez-vous me décrire le problème ?"

        intent = (data.get("intent") or "info").lower()
        if intent not in ("book", "info", "callback", "end_call"):
            intent = "info"

        lead_data = data.get("extracted_lead_data") or {}
        if not isinstance(lead_data, dict):
            lead_data = {}

        booking_action = data.get("booking_action")
        if booking_action:
            booking_action = str(booking_action).upper()
            if booking_action not in ("BOOK_NOW", "CALL_BACK", "SEND_QUOTE", "OUT_OF_ZONE"):
                booking_action = None

        if booking_context and booking_context.get("out_of_zone"):
            booking_action = "OUT_OF_ZONE"
            if not any(
                w in spoken.lower()
                for w in ("désolé", "excuse", "zone", "secteur", "intervenons", "servons")
            ):
                label = booking_context.get("service_area_label") or "notre secteur"
                radius = booking_context.get("service_radius_km", 30)
                spoken = (
                    f"Désolé, nous intervenons uniquement autour de {label}, "
                    f"dans un rayon de {radius} kilomètres. "
                    "Je vous conseille de contacter un plombier local."
                )

        continue_call = data.get("continue_call", True)
        if intent == "end_call" or booking_action == "OUT_OF_ZONE":
            continue_call = False

        return {
            "spoken_response": spoken,
            "intent": intent,
            "extracted_lead_data": lead_data,
            "booking_action": booking_action,
            "continue_call": bool(continue_call),
        }

    def _fallback_response(self, user_text: str) -> dict:
        return {
            "spoken_response": (
                "Merci pour votre appel. Un technicien vous recontactera très rapidement."
            ),
            "intent": "callback",
            "extracted_lead_data": {"summary": user_text[:500]},
            "booking_action": "CALL_BACK",
            "continue_call": False,
        }
