"""Branded social post images for Facebook — generated alongside AI copy.

Uses OpenAI DALL·E when ``OPENAI_API_KEY`` is set; otherwise falls back to a
Pillow composite that matches PilotCore colours (paper #EFE9DC, ink #1A2332).
"""
from __future__ import annotations

import hashlib
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
MEDIA_PREFIX = "media/social"

_BRAND = (
    "PilotCore — plateforme française artisans & particuliers. "
    "Couleurs : papier #EFE9DC / #FBF7EE, encre #1A2332, pas de bleu. "
    "Style : atelier, ledger, éditorial, Fraunces + IBM Plex, coins 4px."
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
    configured = (current_app.config.get("SOCIAL_UPLOAD_DIR") or "").strip()
    if configured:
        directory = Path(configured)
    else:
        directory = _static_root() / "uploads" / "social"
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        import tempfile

        directory = Path(tempfile.gettempdir()) / "pilotcore-social"
        directory.mkdir(parents=True, exist_ok=True)
        logger.warning("Using temp social upload dir: %s", directory)
    return directory


def image_public_url(relative_path: str) -> str:
    rel = (relative_path or "").strip().lstrip("/").replace("\\", "/")
    if rel.startswith("media/social/"):
        return f"{site_base_url()}/{rel}"
    return f"{site_base_url()}/static/{rel}"


def resolve_image_path(relative: str | None) -> Path | None:
    """Return an absolute path only for files under the social upload directory."""
    rel = (relative or "").strip().lstrip("/").replace("\\", "/")
    if not rel or ".." in rel:
        return None
    if rel.startswith(f"{MEDIA_PREFIX}/"):
        name = rel.split("/", 2)[-1]
        path = uploads_dir() / name
        return path if path.is_file() else None
    if not rel.startswith(f"{UPLOAD_PREFIX}/"):
        return None
    path = (_static_root() / rel).resolve()
    root = uploads_dir().resolve()
    try:
        path.relative_to(root)
    except ValueError:
        # File may live in temp upload dir while path key stays uploads/social/…
        alt = root / Path(rel).name
        return alt if alt.is_file() else None
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
        f"Professional social media marketing illustration for a French home-services brand. "
        f"{visual_brief}. "
        f"Style: editorial workshop, warm cream paper #EFE9DC, dark ink #1A2332, "
        f"no blue, no cyan, clean typography space, no text, no logos, no watermarks. "
        f"Landscape 1.91:1 composition."
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


def _text_width(draw, text: str, font) -> float:
    try:
        return float(draw.textlength(text, font=font))
    except Exception:  # noqa: BLE001 — bitmap default font on some hosts
        bbox = draw.textbbox((0, 0), text, font=font)
        return float(bbox[2] - bbox[0])


def _wrap_text(draw, text: str, font, max_width: int) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        if _text_width(draw, trial, font) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines[:3]


def _draw_brand_icon(draw, cx: int, cy: int) -> None:
    paper = _hex_rgb("#F6F1E6")
    ink = _hex_rgb("#1A2332")
    draw.rounded_rectangle((cx - 48, cy - 48, cx + 48, cy + 48), radius=4, fill=ink)
    draw.ellipse((cx - 30, cy - 30, cx + 30, cy + 30), outline=paper, width=3)
    draw.polygon(
        [(cx, cy - 22), (cx + 8, cy + 12), (cx, cy + 6), (cx - 8, cy + 12)],
        fill=paper,
    )
    draw.ellipse((cx - 6, cy - 6, cx + 6, cy + 6), fill=ink)
    draw.rectangle((cx - 28, cy + 34, cx + 28, cy + 38), fill=paper)


def _branded_fallback(headline: str, subject: str) -> bytes:
    from PIL import Image, ImageDraw

    paper = _hex_rgb("#EFE9DC")
    cream = _hex_rgb("#FBF7EE")
    ink = _hex_rgb("#1A2332")
    rule = _hex_rgb("#D7CDB8")
    img = Image.new("RGB", (WIDTH, HEIGHT), paper)
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, 280, HEIGHT), fill=ink)
    draw.rectangle((280, 0, WIDTH, HEIGHT), fill=cream)
    draw.rectangle((280, 0, 284, HEIGHT), fill=rule)
    draw.rectangle((0, HEIGHT - 10, WIDTH, HEIGHT), fill=ink)

    _draw_brand_icon(draw, 140, HEIGHT // 2 - 24)
    mark_font = _load_font(22, bold=True)
    draw.text((72, HEIGHT // 2 + 36), "PilotCore", font=mark_font, fill=_hex_rgb("#F6F1E6"))

    title_font = _load_font(54, bold=True)
    lines = _wrap_text(draw, headline or subject[:60], title_font, WIDTH - 380)
    y = HEIGHT // 2 - 20 - (len(lines) - 1) * 32
    for line in lines:
        draw.text((332, y), line, font=title_font, fill=ink)
        y += 64

    sub_font = _load_font(22, bold=True)
    draw.rounded_rectangle((332, y + 10, 332 + 168, y + 48), radius=4, fill=ink)
    draw.text((348, y + 16), "RDV en ligne", font=sub_font, fill=_hex_rgb("#F6F1E6"))

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
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
    for y in range(HEIGHT - 170, HEIGHT):
        alpha = int(220 * (y - (HEIGHT - 170)) / 170)
        draw.line([(0, y), (WIDTH, y)], fill=(26, 35, 50, alpha))
    draw.rectangle((0, HEIGHT - 8, WIDTH, HEIGHT), fill=_hex_rgb("#1A2332"))

    font = _load_font(40, bold=True)
    lines = _wrap_text(draw, headline, font, WIDTH - 80)
    y = HEIGHT - 28 - len(lines) * 44
    for line in lines:
        draw.text((40, y), line, font=font, fill=_hex_rgb("#F6F1E6"))
        y += 44
    draw.text((40, HEIGHT - 50), "PilotCore", font=_load_font(18, bold=True), fill=_hex_rgb("#E8E4D6"))

    result = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    buf = io.BytesIO()
    result.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _save_png(data: bytes) -> str:
    name = f"{uuid.uuid4().hex}.png"
    path = uploads_dir() / name
    path.write_bytes(data)
    static_target = _static_root() / "uploads" / "social" / name
    try:
        if path.resolve() != static_target.resolve():
            return f"{MEDIA_PREFIX}/{name}"
    except OSError:
        return f"{MEDIA_PREFIX}/{name}"
    return f"{UPLOAD_PREFIX}/{name}"


def generate_for_post(
    subject: str,
    tone: str = "engageant",
    *,
    headline: str | None = None,
    visual_brief: str | None = None,
    use_dalle: bool = False,
) -> dict:
    """Generate a branded PNG and return ``image_path`` + ``image_url``."""
    if headline and visual_brief:
        brief = {"headline": headline[:80], "visual_brief": visual_brief[:500]}
    else:
        try:
            brief = _image_brief(subject, tone)
        except ContentAIError:
            logger.warning("Image brief IA unavailable — fallback local brief")
            brief = {
                "headline": (subject or "PilotCore")[:80],
                "visual_brief": (subject or "PilotCore")[:500],
            }

    headline_text = brief["headline"]
    visual = brief["visual_brief"]

    raw = _try_dalle(visual) if use_dalle else None
    try:
        if raw:
            png = _apply_brand_overlay(raw, headline_text)
        else:
            png = _branded_fallback(headline_text, subject)
    except Exception:  # noqa: BLE001
        logger.exception("Social image render failed — minimal fallback")
        png = _branded_fallback("PilotCore", subject)

    relative = _save_png(png)
    return {
        "image_path": relative,
        "image_url": image_public_url(relative),
        "image_headline": headline_text,
    }


# ── Per-artisan Open Graph / social card ──────────────────────────────────────
# A branded 1200×630 preview (name + trade + city + trust badges) used as the
# og:image / twitter:image of each public artisan profile. Local clients mostly
# discover artisans through shared links (WhatsApp, SMS, Facebook, Google), where
# a keyword-rich, on-brand preview card lifts click-through far above a generic
# square logo. Cards are disk-cached; the filename hash busts when content changes.
PROFILE_CARD_VERSION = 3


def _brand_gradient():
    """PilotCore paper field as an RGBA base image."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (WIDTH, HEIGHT), _hex_rgb("#EFE9DC"))
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, WIDTH, HEIGHT), fill=_hex_rgb("#EFE9DC"))
    draw.rectangle((0, 0, 18, HEIGHT), fill=_hex_rgb("#1A2332"))
    draw.rectangle((0, HEIGHT - 10, WIDTH, HEIGHT), fill=_hex_rgb("#1A2332"))
    return img.convert("RGBA")


def _pill(draw, x: int, y: int, text: str, font, *, fill, text_fill, height: int = 52, pad_x: int = 24) -> int:
    """Draw a rounded 'chip' and return the x just past it."""
    box_w = int(_text_width(draw, text, font) + 2 * pad_x)
    draw.rounded_rectangle((x, y, x + box_w, y + height), radius=4, fill=fill)
    ty = y + (height - getattr(font, "size", 28)) / 2 - 3
    draw.text((x + pad_x, ty), text, font=font, fill=text_fill)
    return x + box_w


def _render_profile_card(tenant, lang: str = "fr") -> bytes:
    from PIL import Image, ImageDraw

    from app.constants.trades import trade_label

    is_fr = (lang or "fr") != "en"
    name = (getattr(tenant, "name", "") or "PilotCore").strip()
    trade = trade_label(getattr(tenant, "trade_type", None), lang)
    city = (getattr(tenant, "city", "") or "").strip()
    postal = (getattr(tenant, "postal_code", "") or "").strip()
    radius = getattr(tenant, "service_radius_km", None)

    base = _brand_gradient()
    # Translucent decorative glows need alpha compositing → draw on an overlay.
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    odraw.ellipse((WIDTH - 300, -110, WIDTH + 90, 220), fill=(246, 241, 230, 80))
    odraw.ellipse((-150, HEIGHT - 250, 230, HEIGHT + 70), fill=(26, 35, 50, 18))
    img = Image.alpha_composite(base, overlay)
    draw = ImageDraw.Draw(img)

    # Solid accent bars along the bottom edge.
    draw.rectangle((0, HEIGHT - 10, WIDTH, HEIGHT), fill=_hex_rgb("#1A2332"))

    _draw_brand_icon(draw, 132, 118)

    PAD = 82
    white = _hex_rgb("#F6F1E6")
    ink = _hex_rgb("#1A2332")
    muted = _hex_rgb("#6B6458")

    # Trade chip.
    _pill(draw, PAD, 196, trade.upper(), _load_font(30, bold=True), fill=ink, text_fill=white)

    # Business name (up to two lines).
    name_font = _load_font(74, bold=True)
    lines = _wrap_text(draw, name, name_font, WIDTH - 2 * PAD)[:2]
    y = 272
    for line in lines:
        draw.text((PAD, y), line, font=name_font, fill=ink)
        y += 86

    # Location subline.
    loc = city or ("France" if is_fr else "France")
    if city and postal:
        loc = f"{city} · {postal}"
    if radius:
        loc += f" · {'zone ' + str(radius) + ' km' if is_fr else str(radius) + ' km radius'}"
    draw.text((PAD, y + 6), loc, font=_load_font(36, bold=False), fill=muted)

    # Trust badges.
    badges = (
        ["RDV en ligne 24h/24", "Réponse immédiate", "Devis sans engagement"]
        if is_fr
        else ["Online booking 24/7", "Instant response", "No-commitment quote"]
    )
    badge_font = _load_font(26, bold=True)
    bx = PAD
    for label in badges:
        bx = _pill(draw, bx, HEIGHT - 150, label, badge_font, fill=ink, text_fill=white, height=50, pad_x=22)
        bx += 16

    # Footer tagline + brand mark.
    tagline = (
        "PilotCore · Trouvez le bon artisan près de chez vous"
        if is_fr
        else "PilotCore · Find the right tradesperson near you"
    )
    draw.text((PAD, HEIGHT - 66), tagline, font=_load_font(24, bold=True), fill=ink)

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _profile_card_filename(tenant, lang: str) -> str:
    key = "|".join(
        str(part or "")
        for part in (
            getattr(tenant, "public_slug", ""),
            getattr(tenant, "name", ""),
            getattr(tenant, "trade_type", ""),
            getattr(tenant, "city", ""),
            getattr(tenant, "postal_code", ""),
            getattr(tenant, "service_radius_km", ""),
            lang or "fr",
            PROFILE_CARD_VERSION,
        )
    )
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
    slug = (getattr(tenant, "public_slug", None) or "artisan")[:60]
    return f"profile-{slug}-{digest}.png"


def profile_card_url(tenant, lang: str = "fr") -> str | None:
    """Cached, branded OG card URL for an artisan profile.

    Renders and disk-caches the 1200×630 PNG on first access; returns ``None`` if
    rendering is unavailable so callers can fall back to the default social image.
    """
    try:
        filename = _profile_card_filename(tenant, lang)
        target = uploads_dir() / filename
        if not target.is_file():
            target.write_bytes(_render_profile_card(tenant, lang))
        return image_public_url(f"{MEDIA_PREFIX}/{filename}")
    except Exception:  # noqa: BLE001 — never break profile rendering over an image
        logger.exception("Profile social card failed for %s", getattr(tenant, "public_slug", "?"))
        return None
