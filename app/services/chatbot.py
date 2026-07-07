"""Commercial chatbot — a text version of the AI voice receptionist.

The voice pipeline (``app.services.voice``) answers phone calls. This module
powers the written equivalent: a sales-oriented chatbot a plumber can embed or
share as a link so visitors can chat instead of call. It qualifies the visitor,
answers commercial questions, collects the lead's details and — once enough
information is gathered — creates a real ``Lead`` through the same extraction +
booking pipeline used for inbound calls.
"""

import json
import logging
import os
import uuid

from flask import current_app

from app.core.errors import NotFoundError
from app.core.extensions import db
from app.models.tenant import Tenant
from app.services.inbound_call import process_inbound_call

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Tu es l'assistant commercial en ligne (chatbot) d'une entreprise d'artisanat (plombier, serrurier, électricien, etc.). Tu discutes par écrit avec un visiteur sur le site de l'entreprise.

Tes objectifs, dans l'ordre :
1. Accueillir chaleureusement et donner envie de faire appel à l'entreprise (tu es commercial, mais jamais insistant).
2. Comprendre le besoin ou le problème du visiteur.
3. Répondre simplement à ses questions (services proposés, zone d'intervention, délais, déroulé d'une intervention). Ne donne JAMAIS de prix ferme : explique qu'un devis gratuit et sans engagement sera établi.
4. Recueillir progressivement les coordonnées nécessaires : prénom/nom, numéro de téléphone, adresse e-mail (obligatoire pour envoyer le devis), adresse (ou au moins la ville) et la nature du problème.
5. Proposer un rappel, un devis gratuit ou une prise de rendez-vous.

RÈGLES DE CONVERSATION :
- Écris comme un humain : phrases courtes, ton naturel et amical, tutoiement évité (vouvoiement).
- Pose UNE seule question à la fois. N'accable pas le visiteur.
- Quand le visiteur donne une information clé (téléphone, adresse), reformule-la brièvement pour confirmer.
- Ne demande le téléphone qu'après avoir compris le besoin, pas dès le premier message.
- Ne révèle jamais que tu es une IA si on ne te le demande pas ; reste discret et professionnel.
- Réponds dans la langue du visiteur (français par défaut, anglais s'il écrit en anglais).
- Reste TOUJOURS dans le périmètre de l'entreprise. Refuse poliment les sujets hors artisanat / dépannage.

ZONE D'INTERVENTION :
- Le contexte peut préciser la ville et le rayon d'intervention. Si le visiteur est manifestement hors zone, reste courtois et propose quand même de transmettre sa demande.

Retourne UNIQUEMENT du JSON valide avec ces clés :
- reply (string) : ta réponse écrite au visiteur (2 à 4 phrases maximum).
- lead_data (objet avec name, phone, email, address, issue_type, urgency_level, summary — mets null pour tout champ pas encore connu). issue_type parmi : general_inquiry, leak, clogged_drain, clogged_toilet, water_heater, toilet, pipe_issue, burst_pipe, flooding. urgency_level parmi : low, medium, high.
- lead_ready (boolean) : true UNIQUEMENT quand tu as au minimum un numéro de téléphone, une adresse e-mail valide ET une description du besoin.
- intent (une valeur parmi : greet, qualify, answer, capture, handoff, end).
"""


class CommercialChatbot:
    """Sales-oriented conversational assistant powered by Mistral (text)."""

    def reply(
        self,
        user_text: str,
        conversation_history: list[dict],
        tenant_name: str = "notre entreprise",
        tenant_city: str | None = None,
        service_radius_km: int | None = None,
        assistant_name: str | None = None,
        trade_type: str | None = None,
        customer_profile: dict | None = None,
    ) -> dict:
        api_key = current_app.config.get("MISTRAL_API_KEY") or os.environ.get("MISTRAL_API_KEY")
        if not api_key:
            return self._fallback(user_text, conversation_history, customer_profile)

        try:
            return self._reply_mistral(
                user_text,
                conversation_history,
                tenant_name,
                tenant_city,
                service_radius_km,
                assistant_name,
                trade_type,
                api_key,
                customer_profile,
            )
        except Exception:
            logger.exception("Commercial chatbot failed — using fallback")
            return self._fallback(user_text, conversation_history, customer_profile)

    def _reply_mistral(
        self,
        user_text: str,
        conversation_history: list[dict],
        tenant_name: str,
        tenant_city: str | None,
        service_radius_km: int | None,
        assistant_name: str | None,
        trade_type: str | None,
        api_key: str,
        customer_profile: dict | None = None,
    ) -> dict:
        from mistralai import Mistral
        from app.constants.trades import trade_label

        model = current_app.config.get("MISTRAL_MODEL", "mistral-small-latest")
        client = Mistral(api_key=api_key)

        history_text = "\n".join(
            f"{'Visiteur' if t.get('role') == 'user' else 'Assistant'}: {t.get('text', '')}"
            for t in conversation_history[-10:]
        )

        context_lines = [f"Entreprise: {tenant_name}"]
        if trade_type:
            context_lines.append(f"Métier: {trade_label(trade_type, 'fr')}")
        if assistant_name:
            context_lines.append(f"Tu te prénommes: {assistant_name}")
        if tenant_city:
            radius = service_radius_km or 30
            context_lines.append(
                f"Zone d'intervention: {tenant_city} et environs, rayon d'environ {radius} km"
            )
        if customer_profile:
            known = []
            if customer_profile.get("name"):
                known.append(f"nom: {customer_profile['name']}")
            if customer_profile.get("phone"):
                known.append(f"téléphone: {customer_profile['phone']}")
            if customer_profile.get("email"):
                known.append(f"e-mail: {customer_profile['email']}")
            if known:
                context_lines.append(
                    "Visiteur CONNECTÉ (compte client) — coordonnées déjà connues: "
                    + ", ".join(known)
                    + ". Ne redemande pas le téléphone ni l'e-mail ; confirme seulement si besoin."
                )
        context = "\n".join(context_lines)

        user_prompt = (
            f"{context}\n\n"
            f"Historique de la conversation:\n{history_text or '(nouvelle conversation)'}\n\n"
            f"Nouveau message du visiteur: {user_text}\n\n"
            "Réponds en JSON uniquement."
        )

        response = client.chat.complete(
            model=model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.5,
            max_tokens=400,
        )

        raw = response.choices[0].message.content
        data = json.loads(raw)
        return self._normalize(data, user_text, customer_profile)

    def _normalize(self, data: dict, user_text: str, customer_profile: dict | None = None) -> dict:
        reply = (data.get("reply") or "").strip()
        if not reply:
            reply = "Bien sûr, pouvez-vous m'en dire un peu plus sur votre besoin ?"

        lead_data = data.get("lead_data")
        if not isinstance(lead_data, dict):
            lead_data = {}

        intent = (data.get("intent") or "answer").lower()
        if intent not in ("greet", "qualify", "answer", "capture", "handoff", "end"):
            intent = "answer"

        phone = (lead_data.get("phone") or "").strip() if lead_data.get("phone") else None
        if not phone and customer_profile:
            phone = (customer_profile.get("phone") or "").strip() or None
        email = (lead_data.get("email") or "").strip().lower() if lead_data.get("email") else None
        if not email and customer_profile:
            email = (customer_profile.get("email") or "").strip().lower() or None
        summary = (lead_data.get("summary") or "").strip()
        lead_ready = bool(data.get("lead_ready")) and bool(phone and email and summary)
        if customer_profile and customer_profile.get("phone") and customer_profile.get("email") and summary:
            lead_ready = True

        return {
            "reply": reply,
            "lead_data": lead_data,
            "lead_ready": lead_ready,
            "intent": intent,
        }

    def _fallback(self, user_text: str, conversation_history: list[dict], customer_profile: dict | None = None) -> dict:
        """Deterministic reply when no LLM key is configured.

        Walks a simple script: greet → understand → ask phone → confirm. Enough
        to demo the widget and still capture a lead when a phone appears.
        """
        turns = [t for t in conversation_history if t.get("role") == "user"]
        text = (user_text or "").strip()
        phone = _find_phone(text) or _find_phone(
            " ".join(t.get("text", "") for t in conversation_history)
        )
        if not phone and customer_profile:
            phone = (customer_profile.get("phone") or "").strip() or None

        email = _find_email(text) or _find_email(
            " ".join(t.get("text", "") for t in conversation_history)
        )
        if not email and customer_profile:
            email = (customer_profile.get("email") or "").strip().lower() or None

        if phone and email:
            lead_data = {
                "phone": phone,
                "email": email,
                "summary": _summary(conversation_history, user_text),
            }
            if customer_profile and customer_profile.get("name"):
                lead_data["name"] = customer_profile["name"]
            return {
                "reply": (
                    "Merci beaucoup ! Je vous envoie le devis par e-mail : "
                    "signez-le et réglez l'acompte en ligne pour confirmer votre rendez-vous."
                ),
                "lead_data": lead_data,
                "lead_ready": True,
                "intent": "capture",
            }

        if phone and not email:
            return {
                "reply": (
                    "Merci pour votre numéro. Pour vous envoyer le devis, "
                    "pouvez-vous me donner votre adresse e-mail ?"
                ),
                "lead_data": {"phone": phone, "summary": _summary(conversation_history, user_text)},
                "lead_ready": False,
                "intent": "qualify",
            }

        if customer_profile and customer_profile.get("phone"):
            return {
                "reply": (
                    f"Bonjour {customer_profile.get('first_name') or ''} ! "
                    "Décrivez votre besoin et je m'occupe de transmettre votre demande."
                ).strip(),
                "lead_data": {
                    "name": customer_profile.get("name"),
                    "phone": customer_profile.get("phone"),
                    "summary": text[:500],
                },
                "lead_ready": False,
                "intent": "greet",
            }

        if len(turns) <= 1:
            return {
                "reply": (
                    "Bonjour et bienvenue ! Décrivez-moi votre besoin ou votre "
                    "problème, je vais voir comment nous pouvons vous aider."
                ),
                "lead_data": {"summary": text[:500]},
                "lead_ready": False,
                "intent": "greet",
            }

        return {
            "reply": (
                "Je comprends. Pour qu'un conseiller vous rappelle avec une "
                "solution adaptée, pouvez-vous me laisser votre numéro de téléphone ?"
            ),
            "lead_data": {"summary": _summary(conversation_history, user_text)},
            "lead_ready": False,
            "intent": "qualify",
        }


def _find_phone(text: str) -> str | None:
    import re

    if not text:
        return None
    match = re.search(r"(\+?\d[\d\s.\-]{7,}\d)", text)
    if not match:
        return None
    cleaned = "".join(c for c in match.group(1) if c.isdigit() or c == "+")
    return cleaned if len(cleaned) >= 8 else None


def _find_email(text: str) -> str | None:
    import re

    if not text:
        return None
    match = re.search(r"([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})", text)
    if match:
        return match.group(1).strip().lower()
    return None


def _summary(conversation_history: list[dict], user_text: str) -> str:
    parts = [t.get("text", "") for t in conversation_history if t.get("role") == "user"]
    parts.append(user_text or "")
    return " ".join(p for p in parts if p).strip()[:500]


def process_chat_turn(
    tenant_id: str,
    history: list[dict],
    message: str,
    existing_lead_id: str | None = None,
    customer_profile: dict | None = None,
) -> dict:
    """One chatbot exchange: produce a reply and capture a lead when ready.

    Stateless by design — the browser holds the transcript and passes it back
    each turn, along with any ``lead_id`` already created for the conversation
    so a captured lead is refreshed instead of duplicated.
    """
    try:
        tid = uuid.UUID(str(tenant_id))
    except (ValueError, TypeError):
        raise NotFoundError("Unknown chatbot")

    tenant = db.session.get(Tenant, tid)
    if not tenant:
        raise NotFoundError("Unknown chatbot")

    message = (message or "").strip()
    history = history if isinstance(history, list) else []

    bot = CommercialChatbot()
    result = bot.reply(
        user_text=message,
        conversation_history=history,
        tenant_name=tenant.name or "notre entreprise",
        tenant_city=tenant.city,
        service_radius_km=tenant.service_radius_km,
        assistant_name=tenant.ai_assistant_name,
        trade_type=tenant.trade_type,
        customer_profile=customer_profile,
    )

    lead_id = existing_lead_id
    lead_captured = False

    if result["lead_ready"] and not existing_lead_id:
        lead_data = result.get("lead_data") or {}
        phone = (lead_data.get("phone") or "").strip()
        email = (lead_data.get("email") or "").strip().lower()
        if not phone and customer_profile:
            phone = (customer_profile.get("phone") or "").strip()
        if not email and customer_profile:
            email = (customer_profile.get("email") or "").strip().lower()
        if phone and email:
            if customer_profile and not lead_data.get("name"):
                lead_data["name"] = customer_profile.get("name")
            if customer_profile and not lead_data.get("email"):
                lead_data["email"] = customer_profile.get("email")
            transcript = _build_transcript(history, message, lead_data)
            try:
                pipeline = process_inbound_call(
                    tenant_id=tid,
                    phone=phone,
                    transcript=transcript,
                )
                lead_id = pipeline.get("lead_id")
                lead_captured = True
                logger.info("Chatbot captured lead=%s tenant=%s", lead_id, tid)
            except Exception:
                logger.exception("Chatbot lead capture failed tenant=%s", tid)

    return {
        "reply": result["reply"],
        "intent": result["intent"],
        "lead_id": lead_id,
        "lead_captured": lead_captured,
    }


def _build_transcript(history: list[dict], message: str, lead_data: dict) -> str:
    """Fold the visitor's side of the chat (plus known fields) into a transcript
    the existing lead extractor can parse."""
    lines = []
    name = (lead_data.get("name") or "").strip()
    address = (lead_data.get("address") or "").strip()
    email = (lead_data.get("email") or "").strip()
    if name:
        lines.append(f"Je m'appelle {name}.")
    if email:
        lines.append(f"Mon e-mail : {email}.")
    if address:
        lines.append(f"Mon adresse : {address}.")
    for turn in history:
        if turn.get("role") == "user" and turn.get("text"):
            lines.append(turn["text"].strip())
    if message:
        lines.append(message)
    return "\n".join(lines)[:2000]
