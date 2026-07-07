import json
import logging
import os
import re

from flask import current_app

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_EN = (
    "You are an assistant that extracts structured lead data from phone call "
    "transcripts for a plumbing company. Return ONLY valid JSON with these keys: "
    "name (string or null), phone (string), email (string or null), "
    "address (string or null), "
    "issue_type (one of: general_inquiry, leak, clogged_drain, clogged_toilet, "
    "water_heater, toilet, pipe_issue, burst_pipe, flooding), "
    "urgency_level (low|medium|high), summary (string). "
    "No explanations. No markdown."
)

SYSTEM_PROMPT_FR = (
    "Tu es un assistant qui extrait des données structurées de transcriptions "
    "d'appels (parfois imparfaites, issues de reconnaissance vocale) pour une "
    "entreprise de plomberie. Retourne UNIQUEMENT du JSON valide "
    "avec ces clés : name (string ou null), phone (string), email (string ou null), "
    "address (string ou null), "
    "issue_type (un parmi : general_inquiry, leak, clogged_drain, clogged_toilet, "
    "water_heater, toilet, pipe_issue, burst_pipe, flooding), "
    "urgency_level (low|medium|high), summary (string en français). "
    "Pour l'adresse, garde l'adresse la plus complète possible (numéro, rue, code "
    "postal, ville) telle que dite. Pour l'e-mail, garde l'adresse telle que "
    "dictée (ex. jean point dupont arobase gmail point com). "
    "Ne devine jamais un nom, une adresse ou un e-mail qui "
    "n'est pas clairement présent : mets null. Corrige les hésitations évidentes "
    "(euh, ben) mais n'invente rien. Pas d'explications. Pas de markdown."
)

URGENCY_KEYWORDS = {
    "high": [
        "urgent", "emergency", "flooding", "flood", "burst", "no water",
        "gas leak", "immediately", "asap", "right away", "inondation",
        "urgence", "tout de suite",
    ],
    "medium": ["leak", "dripping", "blocked", "clogged", "fuite", "bouché"],
}

FALLBACK_ISSUE_PATTERNS = [
    (r"\b(burst|éclat|perc).*(pipe|tuyau|canalis|évier)", "burst_pipe"),
    (r"\b(leak|fuite|drip|goutte)\b", "leak"),
    (r"\b(clog|blocked|bouch).*(toilet|wc)", "clogged_toilet"),
    (r"\b(clog|blocked|bouch|drain|évier|évacuation)\b", "clogged_drain"),
    (r"\b(water heater|chauffe-eau|boiler)\b", "water_heater"),
    (r"\b(toilet|wc)\b", "toilet"),
    (r"\b(pipe|tuyau|canalis)\b", "pipe_issue"),
    (r"\b(flood|inond)\b", "flooding"),
]


class LeadExtractor:
    """Extract structured lead data from call transcripts via Mistral LLM."""

    def extract(self, transcript: str, phone: str) -> dict:
        transcript = (transcript or "").strip()
        phone = (phone or "").strip()

        api_key = current_app.config.get("MISTRAL_API_KEY") or os.environ.get("MISTRAL_API_KEY")
        if api_key:
            try:
                return self._extract_with_mistral(transcript, phone, api_key)
            except Exception:
                logger.exception("Mistral extraction failed, using fallback parser")
                return self._extract_fallback(transcript, phone)

        logger.warning("MISTRAL_API_KEY not set — using fallback parser")
        return self._extract_fallback(transcript, phone)

    def _extract_with_mistral(self, transcript: str, phone: str, api_key: str) -> dict:
        from mistralai import Mistral

        model = current_app.config.get("MISTRAL_MODEL", "mistral-small-latest")
        client = Mistral(api_key=api_key)
        lang = self._detect_language(transcript)
        system_prompt = SYSTEM_PROMPT_FR if lang == "fr" else SYSTEM_PROMPT_EN

        response = client.chat.complete(
            model=model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": (
                        f"Caller phone: {phone}\n\n"
                        f"Transcript:\n{transcript}\n\n"
                        "Return JSON only."
                    ),
                },
            ],
            temperature=0.1,
        )

        raw = response.choices[0].message.content
        data = json.loads(raw)
        return self._normalize(data, phone, transcript)

    def _extract_fallback(self, transcript: str, phone: str) -> dict:
        """Rule-based extraction when LLM is unavailable."""
        lower = transcript.lower()
        urgency = "low"
        for level, keywords in URGENCY_KEYWORDS.items():
            if any(kw in lower for kw in keywords):
                urgency = level
                if level == "high":
                    break

        issue_type = "general_inquiry"
        for pattern, slug in FALLBACK_ISSUE_PATTERNS:
            if re.search(pattern, lower):
                issue_type = slug
                break

        name = self._guess_name(transcript)
        address = self._guess_address(transcript)
        email = self._guess_email(transcript)
        summary = transcript[:500] if transcript else "Inbound call received"

        return self._normalize(
            {
                "name": name,
                "phone": phone,
                "email": email,
                "address": address,
                "issue_type": issue_type,
                "urgency_level": urgency,
                "summary": summary,
            },
            phone,
            transcript,
        )

    def _detect_language(self, transcript: str) -> str:
        lower = transcript.lower()
        french_markers = (
            "bonjour", "je m'appelle", "j'habite", "rue", "urgent", "fuite",
            "bouché", "évier", "merci", "allô", "plombier",
        )
        french_score = sum(1 for m in french_markers if m in lower)
        return "fr" if french_score >= 2 or re.search(r"[àâçéèêëîïôùûü]", lower) else "en"

    def _guess_name(self, transcript: str) -> str | None:
        patterns = [
            r"(?:my name is|i am|i'm|je m'appelle|je suis)\s+([A-ZÀ-ÿ][a-zà-ÿ]+(?:\s+[A-ZÀ-ÿ][a-zà-ÿ]+)?)",
            r"(?:this is)\s+([A-ZÀ-ÿ][a-zà-ÿ]+(?:\s+[A-ZÀ-ÿ][a-zà-ÿ]+)?)",
        ]
        for pattern in patterns:
            match = re.search(pattern, transcript, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return None

    def _guess_address(self, transcript: str) -> str | None:
        patterns = [
            r"(?:at|address is|live at|j'habite|adresse)\s+(.{10,80}?)(?:\.|,|$)",
            r"(\d+\s+[A-Za-zÀ-ÿ\s]+(?:street|st|avenue|ave|road|rd|rue|boulevard|blvd))",
        ]
        for pattern in patterns:
            match = re.search(pattern, transcript, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return None

    def _guess_email(self, transcript: str) -> str | None:
        if not transcript:
            return None
        spoken = re.search(
            r"(?:e-?mail|mail|courriel|adresse mail)\s*(?:est\s+)?"
            r"([^\s,;]+(?:\s+(?:point|arobase|at)\s+[^\s,;]+)+)",
            transcript,
            re.IGNORECASE,
        )
        if spoken:
            return self._normalize_spoken_email(spoken.group(1))
        direct = re.search(
            r"([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})",
            transcript,
        )
        if direct:
            return direct.group(1).strip().lower()
        return None

    def _normalize_spoken_email(self, raw: str) -> str | None:
        text = (raw or "").strip().lower()
        text = text.replace(" arobase ", "@").replace(" at ", "@")
        text = text.replace("arobase", "@").replace(" point ", ".")
        text = re.sub(r"\s+", "", text)
        if "@" in text and "." in text.split("@")[-1]:
            return text
        return None

    def _normalize(self, data: dict, phone: str, transcript: str) -> dict:
        urgency = (data.get("urgency_level") or "low").lower()
        if urgency not in ("low", "medium", "high"):
            urgency = "low"

        name = data.get("name")
        if name and isinstance(name, str):
            name = name.strip() or None

        address = data.get("address")
        if address and isinstance(address, str):
            address = address.strip() or None

        email = data.get("email")
        if email and isinstance(email, str):
            email = email.strip().lower() or None
            if email and "@" not in email:
                email = self._normalize_spoken_email(email)

        issue_type = data.get("issue_type") or "general_inquiry"
        if isinstance(issue_type, str):
            issue_type = issue_type.strip() or "general_inquiry"

        from app.utils.i18n import canonicalize_issue
        issue_type = canonicalize_issue(issue_type)

        summary = data.get("summary") or transcript[:500] or "Inbound call received"
        if isinstance(summary, str):
            summary = summary.strip()

        return {
            "name": name,
            "phone": (data.get("phone") or phone or "").strip(),
            "email": email,
            "address": address,
            "issue_type": issue_type,
            "urgency_level": urgency,
            "summary": summary,
        }
