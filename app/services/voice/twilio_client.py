from twilio.twiml.voice_response import Gather, VoiceResponse


class TwilioVoiceClient:
    """Thin wrapper around Twilio VoiceResponse for LeadPilot AI."""

    LANGUAGE = "fr-FR"

    def __init__(self):
        self.response = VoiceResponse()

    def say(self, text: str, language: str | None = None) -> "TwilioVoiceClient":
        self.response.say(text, language=language or self.LANGUAGE)
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
        )
        if prompt:
            gather.say(prompt, language=self.LANGUAGE)
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
