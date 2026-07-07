from flask import current_app, has_app_context
from twilio.twiml.voice_response import Gather, VoiceResponse


class TwilioVoiceClient:
    """Thin wrapper around Twilio VoiceResponse for PilotCore."""

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
    # Domain vocabulary that biases the recognizer toward what plumbing callers
    # actually say — problems, fixtures, urgency wording, and the address/number
    # phrasing needed to capture a full postal address over a noisy phone line.
    SPEECH_HINTS = (
        "fuite, fuite d'eau, dégât des eaux, ça fuit, ça coule, ça goutte, "
        "inondation, ça inonde, il y a de l'eau partout, baignoire, douche, "
        "évier, lavabo, robinet, mitigeur, WC, toilettes, chasse d'eau, "
        "canalisation bouchée, canalisation, évacuation bouchée, tuyau percé, "
        "chaudière, chauffe-eau, ballon d'eau chaude, cumulus, radiateur, "
        "fuite de gaz, odeur de gaz, plus d'eau chaude, plus d'eau, compteur, "
        "coupure d'eau, urgent, urgence, très urgent, dès que possible, "
        "aujourd'hui, ce matin, cet après-midi, demain, tout de suite, "
        "je m'appelle, mon nom, mon numéro, mon adresse, j'habite au, "
        "numéro, rue, avenue, boulevard, impasse, allée, place, chemin, "
        "résidence, bâtiment, étage, appartement, code postal, "
        "rez-de-chaussée, Paris, Lyon, Marseille, rendez-vous"
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
            # Keep raw words: the profanity filter masks tokens (e.g. "c***"),
            # which mangles otherwise-useful French transcription. We never show
            # the transcript to the caller, so masking only hurts understanding.
            profanityFilter=False,
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
