from flask import current_app, has_app_context
from twilio.twiml.voice_response import Gather, VoiceResponse


class TwilioVoiceClient:
    """Thin wrapper around Twilio VoiceResponse for LeadPilot AI."""

    LANGUAGE = "fr-FR"
    # Amazon Polly neural voice (via Twilio) — far more natural and human than
    # the legacy fr-FR voice. "Lea" is the French neural female voice; override
    # with TWILIO_VOICE (e.g. Polly.Remi-Neural for a male voice).
    DEFAULT_VOICE = "Polly.Lea-Neural"
    # Multilingual recognition model. The old "phone_call" (enhanced) model only
    # supports English, so it was breaking French calls. "googlev2" transcribes
    # natural French speech on phone audio. Enhanced recognition only applies to
    # the English-only models, so it is enabled conditionally (see gather()).
    DEFAULT_SPEECH_MODEL = "googlev2"
    ENHANCED_MODELS = ("phone_call", "numbers_and_commands")
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

    @property
    def speech_model(self) -> str:
        if has_app_context():
            return current_app.config.get("TWILIO_SPEECH_MODEL") or self.DEFAULT_SPEECH_MODEL
        return self.DEFAULT_SPEECH_MODEL

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
        timeout: int = 7,
        speech_timeout: str = "auto",
    ) -> "TwilioVoiceClient":
        model = self.speech_model
        kwargs = dict(
            input="speech",
            action=action,
            method="POST",
            language=self.LANGUAGE,
            # Generous silence window so the caller has time to start speaking
            # and explain the problem in their own words without being cut off.
            timeout=timeout,
            speechTimeout=speech_timeout,
            speechModel=model,
            hints=self.SPEECH_HINTS,
            # Even when Twilio detects no speech, still POST to the action URL so
            # the call is handled (re-prompt / fallback lead) instead of silently
            # hanging up — this is what left callers with "nothing recorded".
            actionOnEmptyResult=True,
        )
        # "enhanced" only exists for the English-only models; setting it on a
        # multilingual model (googlev2 / deepgram) is invalid.
        if model in self.ENHANCED_MODELS:
            kwargs["enhanced"] = True
        gather = Gather(**kwargs)
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
