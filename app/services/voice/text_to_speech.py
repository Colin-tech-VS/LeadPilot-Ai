import base64
import logging
import os
import uuid
from pathlib import Path

from flask import current_app, url_for

logger = logging.getLogger(__name__)


class TextToSpeech:
    """Synthesize natural speech from text (OpenAI TTS or text-only fallback)."""

    def synthesize(self, text: str, call_id: str | None = None) -> dict:
        text = (text or "").strip()
        if not text:
            return {"audio_url": None, "audio_base64": None, "text": ""}

        api_key = current_app.config.get("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if api_key:
            try:
                return self._synthesize_openai(text, call_id, api_key)
            except Exception:
                logger.exception("OpenAI TTS failed")

        return {
            "audio_url": None,
            "audio_base64": None,
            "text": text,
            "provider": "text_only",
        }

    def _synthesize_openai(self, text: str, call_id: str | None, api_key: str) -> dict:
        from openai import OpenAI

        voice = current_app.config.get("TTS_VOICE", "coral")
        model = current_app.config.get("TTS_MODEL", "gpt-4o-mini-tts")
        client = OpenAI(api_key=api_key)

        params = dict(model=model, voice=voice, input=text, response_format="mp3")
        # Tone steering (a warm, natural French receptionist) is only supported by
        # the gpt-4o TTS models — passing it to tts-1 / tts-1-hd raises an error.
        instructions = current_app.config.get("TTS_INSTRUCTIONS")
        if instructions and "gpt-4o" in model:
            params["instructions"] = instructions

        response = client.audio.speech.create(**params)

        audio_bytes = response.content
        audio_dir = self._audio_output_dir()
        filename = f"{call_id or uuid.uuid4().hex}_{uuid.uuid4().hex[:8]}.mp3"
        filepath = audio_dir / filename
        filepath.write_bytes(audio_bytes)

        audio_url = url_for("voice.serve_audio", filename=filename, _external=True)

        return {
            "audio_url": audio_url,
            "audio_base64": base64.b64encode(audio_bytes).decode("ascii"),
            "text": text,
            "provider": "openai",
        }

    def _audio_output_dir(self) -> Path:
        static_root = Path(current_app.static_folder)
        audio_dir = static_root / "audio" / "voice"
        audio_dir.mkdir(parents=True, exist_ok=True)
        return audio_dir
