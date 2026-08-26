"""Generate the /pro sample-call MP3 clips with the same OpenAI TTS as live reception.

Run once when the dialogue copy changes:
    python scripts/generate_demo_audio.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from openai import OpenAI

from app.utils.i18n import TRANSLATIONS

load_dotenv(ROOT / ".env")

TURNS = (
    ("01-ai.mp3", "ai", "landing.demo_clip_1"),
    ("02-client.mp3", "client", "landing.demo_clip_2"),
    ("03-ai.mp3", "ai", "landing.demo_clip_3"),
    ("04-client.mp3", "client", "landing.demo_clip_4"),
)

AI_INSTRUCTIONS = {
    "fr": (
        "Parle en français avec une voix chaleureuse, naturelle et rassurante, "
        "comme une réceptionniste bienveillante. Débit posé, clair et poli, "
        "sans intonation robotique."
    ),
    "en": (
        "Speak English with a warm, natural, reassuring receptionist voice. "
        "Calm, clear, polite, not robotic."
    ),
}
CLIENT_INSTRUCTIONS = {
    "fr": (
        "Parle en français comme une cliente pressée mais polie, au téléphone. "
        "Voix naturelle, un peu d'urgence, pas robotique."
    ),
    "en": (
        "Speak English like a polite but hurried customer on the phone. "
        "Natural voice, a little urgency, not robotic."
    ),
}


def _synthesize(client: OpenAI, text: str, voice: str, instructions: str) -> bytes:
    params = dict(
        model="gpt-4o-mini-tts",
        voice=voice,
        input=text,
        response_format="mp3",
        instructions=instructions,
    )
    try:
        response = client.audio.speech.create(**params)
    except TypeError:
        params.pop("instructions", None)
        response = client.audio.speech.create(**params)
    return response.content


def main() -> None:
    api_key = os.environ.get("OPENAI_API_KEY") or ""
    if not api_key.startswith("sk-"):
        raise SystemExit("OPENAI_API_KEY is missing.")
    client = OpenAI(api_key=api_key)
    out_root = ROOT / "static" / "audio" / "demo"
    for lang in ("fr", "en"):
        folder = out_root / lang
        folder.mkdir(parents=True, exist_ok=True)
        strings = TRANSLATIONS[lang]
        for filename, role, key in TURNS:
            text = strings[key]
            voice = "coral" if role == "ai" else "nova"
            instructions = AI_INSTRUCTIONS[lang] if role == "ai" else CLIENT_INSTRUCTIONS[lang]
            print(f"{lang}/{filename} ({len(text)} chars)…")
            audio = _synthesize(client, text, voice, instructions)
            path = folder / filename
            path.write_bytes(audio)
            print(f"  wrote {path.stat().st_size} bytes")


if __name__ == "__main__":
    main()
