import base64
import logging
import os
import tempfile

import requests
from flask import current_app

logger = logging.getLogger(__name__)


def transcribe(audio_url: str) -> str:
    """Transcribe audio from a URL (Twilio recording or direct link)."""
    stt = SpeechToText()
    return stt.transcribe(audio_url=audio_url)


class SpeechToText:
    """Transcribe phone audio using Whisper API (or provider pre-transcription)."""

    def transcribe(self, audio_input: bytes | str | None = None, audio_url: str | None = None) -> str:
        if isinstance(audio_input, str) and audio_input.strip():
            if not audio_input.strip().startswith("http"):
                return audio_input.strip()

        audio_bytes = self._resolve_audio(audio_input, audio_url)
        if not audio_bytes:
            return ""

        api_key = current_app.config.get("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            logger.warning("OPENAI_API_KEY not set — STT unavailable")
            return ""

        return self._transcribe_whisper(audio_bytes, api_key)

    def _resolve_audio(
        self, audio_input: bytes | str | None, audio_url: str | None
    ) -> bytes | None:
        if isinstance(audio_input, bytes):
            return audio_input

        if isinstance(audio_input, str) and audio_input.strip():
            try:
                return base64.b64decode(audio_input)
            except Exception:
                pass

        url = audio_url or (audio_input if isinstance(audio_input, str) else None)
        if url:
            return self._download_audio(url)

        return None

    def _download_audio(self, url: str) -> bytes | None:
        auth = self._twilio_auth()
        download_url = url if url.endswith(".wav") else f"{url}.wav"
        try:
            resp = requests.get(download_url, auth=auth, timeout=15)
            resp.raise_for_status()
            return resp.content
        except Exception:
            try:
                resp = requests.get(url, auth=auth, timeout=15)
                resp.raise_for_status()
                return resp.content
            except Exception:
                logger.exception("Failed to fetch audio from URL: %s", url)
        return None

    def _twilio_auth(self):
        sid = current_app.config.get("TWILIO_ACCOUNT_SID") or os.environ.get("TWILIO_ACCOUNT_SID")
        token = current_app.config.get("TWILIO_AUTH_TOKEN") or os.environ.get("TWILIO_AUTH_TOKEN")
        if sid and token:
            return (sid, token)
        return None

    def _transcribe_whisper(self, audio_bytes: bytes, api_key: str) -> str:
        from openai import OpenAI

        model = current_app.config.get("WHISPER_MODEL", "whisper-1")
        language = current_app.config.get("VOICE_LANGUAGE", "fr")
        client = OpenAI(api_key=api_key)

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        try:
            with open(tmp_path, "rb") as audio_file:
                response = client.audio.transcriptions.create(
                    model=model,
                    file=audio_file,
                    language=language,
                    response_format="text",
                )
            text = response if isinstance(response, str) else str(response)
            return text.strip()
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
