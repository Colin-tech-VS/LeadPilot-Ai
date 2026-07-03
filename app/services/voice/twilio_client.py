from flask import current_app, has_app_context
from twilio.twiml.voice_response import Gather, VoiceResponse


class TwilioVoiceClient:
    """Thin wrapper around Twilio VoiceResponse for LeadPilot AI."""

    LANGUAGE = "fr-FR"
    # Amazon Polly neural voice (via Twilio) — far more natural and human than
    # the legacy fr-FR voice. "Lea" is the French neural female voice; override
    # with TWILIO_VOICE (e.g. Polly.Remi-Neural for a male voice).
    DEFAULT_VOICE = "Polly.Lea-Neural"
    # Telephony-optimized recognition model + domain vocabulary to bias
    # Twilio's speech-to-text toward plumbing calls (improves accuracy on
    # low-quality phone audio).
    SPEECH_MODEL = "phone_call"
    SPEECH_HINTS = (
        "fuite, fuite d'eau, dégât des eaux, baignoire, douche, évier, lavabo, "
        "robinet, WC, toilettes, chasse d'eau, canalisation bouchée, tuyau, "
        "chaudière, chauffe-eau, ballon d'eau chaude, radiateur, fuite de gaz, "
        "urgent, urgence, ça inonde, plus d'eau chaude, rue, avenue, boulevard, "
        "impasse, place, code postal, rendez-vous, dès que possible"
    )

    def __init__(self):
        self.response = VoiceResponse()

    @property
    def voice(self) -> str:
        if has_app_context():
            return current_app.config.get("TWILIO_VOICE") or self.DEFAULT_VOICE
        return self.DEFAULT_VOICE

    def say(self, text: str, language: str | None = None) -> "TwilioVoiceClient":
        self.response.say(text, voice=self.voice, language=language or self.LANGUAGE)
        return self

    def record(
        self,
        action: str,
        max_length: int = 10,
        play_beep: bool = True,
        timeout: int = 3,
    ) -> "TwilioVoiceClient":
        self.response.record(
            action=action,
            method="POST",
            maxLength=max_length,
            playBeep=play_beep,
            timeout=timeout,
        )
        return self

    def gather(
        self,
        action: str,
        prompt: str | None = None,
        timeout: int = 5,
        speech_timeout: str = "auto",
    ) -> "TwilioVoiceClient":
        gather = Gather(
            input="speech",
            action=action,
            method="POST",
            language=self.LANGUAGE,
            timeout=timeout,
            speechTimeout=speech_timeout,
            speechModel=self.SPEECH_MODEL,
            enhanced=True,
            hints=self.SPEECH_HINTS,
        )
        if prompt:
            gather.say(prompt, voice=self.voice, language=self.LANGUAGE)
        self.response.append(gather)
        return self

    def hangup(self) -> "TwilioVoiceClient":
        self.response.hangup()
        return self

    def redirect(self, url: str) -> "TwilioVoiceClient":
        self.response.redirect(url, method="POST")
        return self

    def to_xml(self) -> str:
        return str(self.response)
