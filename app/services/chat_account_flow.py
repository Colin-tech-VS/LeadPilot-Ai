"""Chatbot account flow — same journey as the voice IA (lookup / create / guest)."""

from __future__ import annotations

import logging

from app.services.voice import customer_account as vca

logger = logging.getLogger(__name__)


class ChatAccountFlow:
    """Deterministic account steps for the stateless chat widget."""

    def __init__(
        self,
        account_flow: dict | None = None,
        asked_slots: list[str] | None = None,
        phone_hint: str | None = None,
    ):
        self.account_flow = (
            account_flow if isinstance(account_flow, dict) else vca.default_account_flow()
        )
        self.asked_slots = list(asked_slots) if isinstance(asked_slots, list) else []
        self.phone_hint = phone_hint

    def should_run(self, summary: str | None, logged_in: bool) -> bool:
        if logged_in or self.account_flow.get("account_done"):
            return False
        return bool((summary or "").strip())

    def last_account_slot(self) -> str | None:
        for slot in reversed(self.asked_slots):
            if slot.startswith("account:"):
                return slot
        return None

    def update_from_message(self, text: str, lead_data: dict) -> str | None:
        """Apply the visitor reply to the last account step. Returns an optional notice."""
        last = self.last_account_slot()
        if not last:
            return None

        af = self.account_flow
        lower = (text or "").strip().lower()

        if last == "account:has_account":
            if vca.is_yes(lower):
                af["has_account"] = True
            elif vca.is_no(lower):
                af["has_account"] = False
            return None

        if last == "account:lookup":
            email = vca.extract_email_from_transcript(text) or lead_data.get("email")
            if email:
                lead_data["email"] = email
            user = vca.lookup_customer(
                email=email,
                phone=self.phone_hint,
                name_hint=text,
            )
            if user:
                lead_data.update(vca.apply_customer_to_lead(user, lead_data))
                af["customer_user_id"] = str(user.id)
                af["account_done"] = True
                af["lookup_failed"] = False
                name = user.first_name or user.full_name or ""
                if name:
                    return f"Parfait, je vous retrouve {name} !"
                return "Parfait, je retrouve votre compte client."
            af["lookup_failed"] = True
            return None

        if last == "account:lookup_retry":
            if vca.is_yes(lower):
                af["wants_create"] = True
                af["lookup_failed"] = False
            elif vca.is_no(lower):
                af["guest_mode"] = True
            return None

        if last == "account:create_pitch":
            if vca.is_yes(lower):
                af["wants_create"] = True
            elif vca.is_no(lower):
                af["guest_mode"] = True
            return None

        if last == "account:create_name":
            name = (lead_data.get("name") or text).strip()
            if name and name.lower() not in ("unknown", "inconnu"):
                lead_data["name"] = name
                first, last_name = vca.split_name(name)
                af["create_first_name"] = first
                af["create_last_name"] = last_name
            return None

        if last == "account:create_email":
            email = vca.extract_email_from_transcript(text)
            if email:
                af["pending_email"] = email
                lead_data["email"] = email
            return None

        if last == "account:email_confirm":
            if vca.is_yes(lower):
                af["email_confirmed"] = True
            elif vca.is_no(lower):
                af["pending_email"] = None
                lead_data.pop("email", None)
                af.pop("email_confirmed", None)
            else:
                email = vca.extract_email_from_transcript(text)
                if email:
                    af["pending_email"] = email
                    lead_data["email"] = email
                    af["email_confirmed"] = True
            return None

        if last == "account:guest_email":
            email = vca.extract_email_from_transcript(text) or lead_data.get("email")
            if email:
                lead_data["email"] = email
                af["collected_email"] = email
                af["guest_email"] = email
                af["account_done"] = True
            return None

        return None

    def maybe_finalize(self, lead_data: dict) -> str | None:
        af = self.account_flow
        if not af.get("wants_create") or af.get("customer_user_id"):
            return None
        if not af.get("email_confirmed") or not af.get("pending_email"):
            return None
        if not af.get("create_first_name"):
            return None

        password = vca.generate_voice_password()
        try:
            user = vca.create_customer_account(
                email=af["pending_email"],
                first_name=af.get("create_first_name"),
                last_name=af.get("create_last_name"),
                phone=self.phone_hint,
                password=password,
            )
        except Exception:
            logger.exception("Chat account creation failed email=%s", af.get("pending_email"))
            af["create_failed"] = True
            return (
                "Je n'ai pas pu créer le compte pour le moment. "
                "Nous continuons sans compte pour l'instant."
            )

        lead_data.update(vca.apply_customer_to_lead(user, lead_data))
        af["customer_user_id"] = str(user.id)
        af["account_done"] = True
        vca.send_credentials_email(user, password)
        return (
            "Votre compte PilotCore est créé ! "
            "Vous allez recevoir un e-mail avec vos identifiants de connexion."
        )

    def next_question(self, lead_data: dict) -> tuple[str, str] | None:
        af = self.account_flow

        if af.get("account_done"):
            return None

        if af.get("has_account") is None:
            if "account:has_account" in self.asked_slots:
                return (
                    "account:has_account",
                    "Je n'ai pas bien compris. Avez-vous déjà un compte client PilotCore ? "
                    "Répondez oui ou non.",
                )
            return (
                "account:has_account",
                "Avant de continuer, avez-vous déjà un compte client PilotCore ? "
                "Répondez oui ou non.",
            )

        if af.get("has_account") and not af.get("wants_create") and not af.get("guest_mode"):
            if not af.get("customer_user_id"):
                if af.get("lookup_failed"):
                    if "account:lookup_retry" not in self.asked_slots:
                        return (
                            "account:lookup_retry",
                            "Je ne trouve pas de compte avec ces informations. "
                            "Souhaitez-vous créer un compte gratuit maintenant ? Répondez oui ou non.",
                        )
                    if af.get("wants_create") is None:
                        return (
                            "account:lookup_retry",
                            "Répondez oui pour créer un compte, ou non pour continuer sans compte.",
                        )
                elif lead_data.get("email"):
                    user = vca.lookup_customer(
                        email=lead_data.get("email"),
                        phone=self.phone_hint,
                    )
                    if user:
                        lead_data.update(vca.apply_customer_to_lead(user, lead_data))
                        af["customer_user_id"] = str(user.id)
                        af["account_done"] = True
                        return None
                    af["lookup_failed"] = True
                elif "account:lookup" not in self.asked_slots:
                    return (
                        "account:lookup",
                        "Très bien. Quelle est l'adresse e-mail de votre compte ?",
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
                if "account:create_name" not in self.asked_slots or not lead_data.get("name"):
                    return (
                        "account:create_name",
                        "Parfait ! Quel est votre prénom et votre nom, s'il vous plaît ?",
                    )
            if not af.get("pending_email"):
                if lead_data.get("email"):
                    af["pending_email"] = lead_data["email"]
                elif "account:create_email" not in self.asked_slots:
                    return (
                        "account:create_email",
                        "Merci. Quelle est votre adresse e-mail ?",
                    )
            if af.get("pending_email") and not af.get("email_confirmed"):
                email = af["pending_email"]
                return (
                    "account:email_confirm",
                    f"Je note l'adresse {email}. Est-ce correct ? Répondez oui, ou renvoyez la bonne adresse.",
                )
            if af.get("email_confirmed") and not af.get("customer_user_id"):
                self.maybe_finalize(lead_data)
            if af.get("customer_user_id"):
                af["account_done"] = True
            return None

        if af.get("guest_mode"):
            email = (
                lead_data.get("email")
                or af.get("guest_email")
                or af.get("collected_email")
            )
            if email:
                lead_data["email"] = email
                af["account_done"] = True
                return None
            return (
                "account:guest_email",
                "Pas de souci. Indiquez-moi simplement votre adresse e-mail pour recevoir le devis.",
            )

        if not af.get("has_account"):
            if "account:create_pitch" not in self.asked_slots:
                return (
                    "account:create_pitch",
                    "Avec un compte gratuit PilotCore, vous suivez vos devis et rendez-vous en ligne, "
                    "et c'est beaucoup plus rapide la prochaine fois. "
                    "Je peux vous créer un compte en une minute. Souhaitez-vous que je le fasse ? "
                    "Répondez oui ou non.",
                )
            return (
                "account:create_pitch",
                "Souhaitez-vous créer un compte client gratuit ? Répondez oui ou non.",
            )

        return None
