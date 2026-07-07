"""Branded social post images for Facebook — generated alongside AI copy.

Uses OpenAI DALL·E when ``OPENAI_API_KEY`` is set; otherwise falls back to a
Pillow composite that matches PilotCore colours (#1B57E0, #06B6D4, #10B981).
"""
from __future__ import annotations

import io
import json
import logging
import uuid
from pathlib import Path

import requests
from flask import current_app

from app.services.content_ai import ContentAIError, _complete
from app.utils.seo import site_base_url

logger = logging.getLogger(__name__)

WIDTH = 1200
HEIGHT = 630
UPLOAD_PREFIX = "uploads/social"

_BRAND = (
    "PilotCore — plateforme française artisans & particuliers. "
    "Couleurs : bleu #1B57E0, cyan #06B6D4, vert #10B981, fond bleu foncé #0F2D6E. "
    "Style : moderne, flat, pro, rassurant."
)

_IMAGE_BRIEF_SYSTEM = (
    "Tu es directeur artistique pour PilotCore (standardiste IA pour artisans).\n"
    f"{_BRAND}\n"
    "Réponds UNIQUEMENT en JSON avec :\n"
    '- "headline" : accroche courte en français (6 mots max, pour overlay texte),\n'
    '- "visual_brief" : description du visuel principal SANS texte dans l\'image '
    "(icônes, artisan, téléphone, calendrier RDV, outils du bâtiment…), "
    "cohérent avec le sujet du post."
)


def _static_root() -> Path:
    return Path(current_app.static_folder or "static")


def uploads_dir() -> Path:
    directory = _static_root() / "uploads" / "social"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def image_public_url(relative_path: str) -> str:
    rel = (relative_path or "").strip().lstrip("/")
    return f"{site_base_url()}/static/{rel}"


def resolve_image_path(relative: str | None) -> Path | None:
    """Return an absolute path only for files under ``static/uploads/social/``."""
    rel = (relative or "").strip().lstrip("/").replace("\\", "/")
    if not rel or ".." in rel or not rel.startswith(f"{UPLOAD_PREFIX}/"):
        return None
    path = (_static_root() / rel).resolve()
    root = uploads_dir().resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    return path if path.is_file() else None


def _image_brief(subject: str, tone: str) -> dict:
    user = f"Sujet du post : {subject.strip()}\nTon : {tone}.\nProduis headline + visual_brief."
    raw = _complete(_IMAGE_BRIEF_SYSTEM, user, json_mode=True, max_tokens=280, temperature=0.55)
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ContentAIError("Brief visuel IA non exploitable.") from exc
    headline = (data.get("headline") or subject.strip()[:40] or "PilotCore").strip()
    visual = (data.get("visual_brief") or subject.strip()).strip()
    return {"headline": headline[:80], "visual_brief": visual[:500]}


def _dalle_prompt(visual_brief: str) -> str:
    return (
        f"Professional social media marketing illustration for a French home-services tech brand. "
        f"{visual_brief}. "
        f"Style: modern flat design, blue #1B57E0 and cyan #06B6D4 accents, clean minimalist, "
        f"soft gradients, no text, no logos, no watermarks. Landscape composition."
    )


def _try_dalle(visual_brief: str) -> bytes | None:
    api_key = (current_app.config.get("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return None
    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        resp = client.images.generate(
            model="dall-e-3",
            prompt=_dalle_prompt(visual_brief),
            size="1792x1024",
            quality="standard",
            n=1,
        )
        url = resp.data[0].url
        if not url:
            return None
        img_resp = requests.get(url, timeout=90)
        img_resp.raise_for_status()
        return img_resp.content
    except Exception:  # noqa: BLE001
        logger.exception("DALL·E image generation failed — using branded fallback")
        return None


def _hex_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


def _lerp(a: int, b: int, t: float) -> int:
    return int(a + (b - a) * t)


def _load_font(size: int, *, bold: bool = False):
    from PIL import ImageFont

    names = (
        ["DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "arialbd.ttf", "segoeuib.ttf"]
        if bold
        else ["DejaVuSans.ttf", "LiberationSans-Regular.ttf", "arial.ttf", "segoeui.ttf"]
    )
    roots = [
        Path("/usr/share/fonts/truetype/dejavu"),
        Path("/usr/share/fonts/truetype/liberation"),
        Path("C:/Windows/Fonts"),
    ]
    for root in roots:
        for name in names:
            path = root / name
            if path.is_file():
                return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _wrap_text(draw, text: str, font, max_width: int) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        if draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines[:3]


def _draw_brand_icon(draw, cx: int, cy: int) -> None:
    draw.ellipse((cx - 52, cy - 52, cx + 52, cy + 52), outline=(255, 255, 255, 70), width=2)
    draw.ellipse((cx - 36, cy - 36, cx + 36, cy + 36), outline=(255, 255, 255, 45), width=1)
    draw.polygon(
        [(cx, cy - 28), (cx + 10, cy + 14), (cx, cy + 8), (cx - 10, cy + 14)],
        fill=(255, 255, 255, 240),
    )
    draw.ellipse((cx - 8, cy - 8, cx + 8, cy + 8), fill=_hex_rgb("#1B57E0"))
    draw.line((cx - 40, cy + 36, cx + 40, cy + 36), fill=_hex_rgb("#06B6D4"), width=3)
    draw.line((cx, cy + 36, cx, cy + 52), fill=_hex_rgb("#10B981"), width=3)


def _branded_fallback(headline: str, subject: str) -> bytes:
    from PIL import Image, ImageDraw

    top = _hex_rgb("#0F2D6E")
    mid = _hex_rgb("#1B57E0")
    bottom = _hex_rgb("#2563EB")
    img = Image.new("RGB", (WIDTH, HEIGHT))
    pixels = img.load()
    for y in range(HEIGHT):
        t = y / max(HEIGHT - 1, 1)
        if t < 0.55:
            local = t / 0.55
            color = (
                _lerp(top[0], mid[0], local),
                _lerp(top[1], mid[1], local),
                _lerp(top[2], mid[2], local),
            )
        else:
            local = (t - 0.55) / 0.45
            color = (
                _lerp(mid[0], bottom[0], local),
                _lerp(mid[1], bottom[1], local),
                _lerp(mid[2], bottom[2], local),
            )
        for x in range(WIDTH):
            pixels[x, y] = color

    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rectangle((0, HEIGHT - 8, WIDTH, HEIGHT), fill=_hex_rgb("#10B981"))
    draw.rectangle((0, HEIGHT - 12, WIDTH, HEIGHT - 8), fill=_hex_rgb("#06B6D4"))
    draw.ellipse((WIDTH - 280, -80, WIDTH + 80, 200), fill=(6, 182, 212, 35))
    draw.ellipse((-120, HEIGHT - 220, 200, HEIGHT + 40), fill=(16, 185, 129, 30))
    img = Image.alpha_composite(img.convert("RGBA"), overlay)
    draw = ImageDraw.Draw(img)

    _draw_brand_icon(draw, 140, HEIGHT // 2 - 20)

    title_font = _load_font(58, bold=True)
    sub_font = _load_font(24, bold=True)
    lines = _wrap_text(draw, headline or subject[:60], title_font, WIDTH - 320)
    y = HEIGHT // 2 - 30 - (len(lines) - 1) * 34
    for line in lines:
        draw.text((300, y), line, font=title_font, fill=(255, 255, 255))
        y += 68

    badge_w = 148
    draw.rounded_rectangle((300, y + 8, 300 + badge_w, y + 48), radius=10, fill=_hex_rgb("#06B6D4"))
    draw.text((318, y + 14), "PilotCore", font=sub_font, fill=(255, 255, 255))

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _apply_brand_overlay(image_bytes: bytes, headline: str) -> bytes:
    from PIL import Image, ImageDraw

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    w, h = img.size
    target_ratio = WIDTH / HEIGHT
    current_ratio = w / h
    if current_ratio > target_ratio:
        new_w = int(h * target_ratio)
        left = (w - new_w) // 2
        img = img.crop((left, 0, left + new_w, h))
    else:
        new_h = int(w / target_ratio)
        top = (h - new_h) // 2
        img = img.crop((0, top, w, top + new_h))
    img = img.resize((WIDTH, HEIGHT), Image.LANCZOS)

    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for y in range(HEIGHT - 160, HEIGHT):
        alpha = int(200 * (y - (HEIGHT - 160)) / 160)
        draw.line([(0, y), (WIDTH, y)], fill=(15, 45, 110, alpha))
    draw.rectangle((0, HEIGHT - 6, WIDTH, HEIGHT), fill=_hex_rgb("#10B981"))
    draw.rectangle((0, HEIGHT - 10, WIDTH, HEIGHT - 6), fill=_hex_rgb("#06B6D4"))

    font = _load_font(42, bold=True)
    lines = _wrap_text(draw, headline, font, WIDTH - 80)
    y = HEIGHT - 24 - len(lines) * 46
    for line in lines:
        draw.text((40, y), line, font=font, fill=(255, 255, 255))
        y += 46
    draw.text((40, HEIGHT - 52), "PilotCore", font=_load_font(20, bold=True), fill=_hex_rgb("#06B6D4"))

    result = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    buf = io.BytesIO()
    result.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _save_png(data: bytes) -> str:
    name = f"{uuid.uuid4().hex}.png"
    path = uploads_dir() / name
    path.write_bytes(data)
    return f"{UPLOAD_PREFIX}/{name}"


def generate_for_post(subject: str, tone: str = "engageant") -> dict:
    """Generate a branded PNG and return ``image_path`` + ``image_url``."""
    brief = _image_brief(subject, tone)
    headline = brief["headline"]
    visual = brief["visual_brief"]

    raw = _try_dalle(visual)
    if raw:
        try:
            png = _apply_brand_overlay(raw, headline)
        except Exception:  # noqa: BLE001
            logger.exception("Brand overlay failed — using fallback card")
            png = _branded_fallback(headline, subject)
    else:
        png = _branded_fallback(headline, subject)

    relative = _save_png(png)
    return {
        "image_path": relative,
        "image_url": image_public_url(relative),
        "image_headline": headline,
    }
