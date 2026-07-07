"""Voice IA — compte client particulier (lookup, création rapide, mode invité)."""

import logging
import random
import re

from app.core.errors import ConflictError
from app.core.extensions import db
from app.models.user import User
from app.utils.validation import validate_email

logger = logging.getLogger(__name__)

YES_WORDS = (
    "oui", "ouais", "yes", "ok", "d'accord", "dacord", "bien sûr", "bien sur",
    "volontiers", "avec plaisir", "carrément", "carrement", "exact", "affirmatif",
)
NO_WORDS = (
    "non", "no", "pas maintenant", "plus tard", "refuse", "refuser", "sans compte",
    "invité", "invite", "anonyme", "ne veux pas", "pas besoin",
)


def _matches_token(text: str, token: str) -> bool:
    token = (token or "").strip().lower()
    if not token:
        return False
    if " " in token:
        return token in (text or "").lower()
    return re.search(rf"(?:^|\s){re.escape(token)}(?:\s|$|[.,!?])", (text or "").lower()) is not None


def is_yes(text: str) -> bool:
    lower = (text or "").lower().strip()
    return any(_matches_token(lower, w) for w in YES_WORDS)


def is_no(text: str) -> bool:
    lower = (text or "").lower().strip()
    return any(_matches_token(lower, w) for w in NO_WORDS)


def generate_voice_password() -> str:
    """Mot de passe simple à dicter au téléphone (8+ caractères)."""
    return f"Pilot{random.randint(1000, 9999)}"


def spell_for_voice(value: str) -> str:
    """Espace chaque caractère pour une lecture TTS plus claire."""
    return " ".join(value)


def normalize_phone_digits(phone: str | None) -> str:
    if not phone:
        return ""
    digits = "".join(c for c in phone if c.isdigit())
    if digits.startswith("33") and len(digits) >= 11:
        digits = "0" + digits[2:]
    return digits[-9:] if len(digits) >= 9 else digits


def extract_email_from_transcript(transcript: str) -> str | None:
    """Extrait et normalise un e-mail depuis une réponse vocale dictée.

    C'est la réponse à « quelle est votre e-mail ? » : on n'exige donc AUCUN
    mot d'introduction et on reconstruit l'adresse épelée (« jean point dupont
    arobase gmail point com »), même sans « arobase ».
    """
    from app.services.lead_extractor import reconstruct_spoken_email

    return reconstruct_spoken_email(transcript or "")


def lookup_customer(
    *,
    email: str | None = None,
    phone: str | None = None,
    name_hint: str | None = None,
) -> User | None:
    """Retrouve un compte particulier par e-mail, téléphone ou nom."""
    if email:
        try:
            addr = validate_email(email.strip().lower())
        except Exception:
            addr = None
        if addr:
            user = User.query.filter_by(email=addr, role="customer").first()
            if user:
                return user

    caller_digits = normalize_phone_digits(phone)
    if caller_digits:
        for user in User.query.filter_by(role="customer").filter(User.phone.isnot(None)).all():
            if normalize_phone_digits(user.phone) == caller_digits:
                return user

    hint = (name_hint or "").strip().lower()
    if hint and len(hint) >= 3:
        tokens = [t for t in re.split(r"\s+", hint) if len(t) >= 2]
        if tokens:
            q = User.query.filter_by(role="customer")
            for user in q.limit(200).all():
                full = (user.full_name or "").lower()
                if full and all(t in full for t in tokens[:2]):
                    return user
    return None


def apply_customer_to_lead(user: User, lead_data: dict) -> dict:
    """Pré-remplit les infos lead depuis un compte existant."""
    merged = dict(lead_data or {})
    if user.full_name:
        merged["name"] = user.full_name
    merged["email"] = user.email
    if user.phone:
        merged["phone"] = user.phone
    merged["customer_user_id"] = str(user.id)
    return merged


def split_name(full_name: str) -> tuple[str | None, str | None]:
    parts = [p for p in (full_name or "").strip().split() if p]
    if not parts:
        return None, None
    if len(parts) == 1:
        return parts[0], None
    return parts[0], " ".join(parts[1:])


def create_customer_account(
    *,
    email: str,
    first_name: str | None,
    last_name: str | None,
    phone: str | None,
    password: str,
) -> User:
    """Crée un vrai compte client (role=customer) depuis la voix IA."""
    from app.services.signup_service import register_customer_via_voice

    try:
        user = register_customer_via_voice(
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name,
            phone=phone,
        )
        logger.info("Voice created customer account user=%s", user.id)
        return user
    except ConflictError:
        existing = User.query.filter_by(email=validate_email(email), role="customer").first()
        if existing:
            return existing
        raise


def send_credentials_email(user: User, password: str) -> None:
    from app.services.transactional_email import send_voice_customer_credentials

    try:
        send_voice_customer_credentials(user, password)
    except Exception:
        logger.exception("Voice credentials email failed user=%s", user.id)


def default_account_flow() -> dict:
    return {
        "account_done": False,
        "has_account": None,
        "wants_create": None,
        "guest_mode": False,
        "lookup_failed": False,
        "customer_user_id": None,
        "voice_password": None,
        "pending_email": None,
        "create_first_name": None,
        "create_last_name": None,
    }
