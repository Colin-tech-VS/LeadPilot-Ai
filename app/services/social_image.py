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
    '- "headline" : accroche vendeuse en français (5 mots max, overlay),\n'
    '- "visual_brief" : scène PHOTO publicitaire SANS aucun texte dans l\'image '
    "(artisan au travail, téléphone qui sonne, créneau d'agenda, atelier chaud, "
    "outils du bâtiment, particulier soulagé). Lumière d'atelier, papier crème, "
    "encre sombre, pas de bleu."
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


def _safe_image_name(relative: str | None) -> str | None:
    rel = (relative or "").strip().lstrip("/").replace("\\", "/")
    if not rel or ".." in rel:
        return None
    if rel.startswith(f"{MEDIA_PREFIX}/"):
        name = rel.split("/", 2)[-1]
    elif rel.startswith(f"{UPLOAD_PREFIX}/"):
        name = Path(rel).name
    else:
        return None
    if not name or name != Path(name).name or not name.endswith(".png"):
        return None
    return name


def destination_for_relative(relative: str | None) -> Path | None:
    """Where a stored ``image_path`` should live on this host (file may be absent)."""
    name = _safe_image_name(relative)
    return uploads_dir() / name if name else None


def write_image_bytes(relative: str | None, data: bytes) -> Path | None:
    """Write PNG bytes to the path Graph publish expects, even after an ephemeral disk wipe."""
    if not data:
        return None
    dest = destination_for_relative(relative)
    if dest is None:
        name = f"{uuid.uuid4().hex}.png"
        dest = uploads_dir() / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(bytes(data))
    return dest if dest.is_file() else None


def resolve_image_path(relative: str | None) -> Path | None:
    """Return an absolute path only for files under the social upload directory."""
    dest = destination_for_relative(relative)
    if dest is not None and dest.is_file():
        return dest
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
        "Cinematic Facebook ad still for a premium French home-services brand "
        "(plumber, locksmith, electrician). "
        f"{visual_brief}. "
        "Warm cream paper #EFE9DC, dark ink #1A2332, no blue, no cyan, no purple. "
        "Photoreal editorial workshop light, shallow depth of field, trustworthy, converting. "
        "No text, no letters, no logos, no watermarks, no UI mockups. "
        "Landscape 1.91:1. Keep the lower third slightly darker for a caption overlay."
    )


def _try_dalle(visual_brief: str) -> bytes | None:
    api_key = (current_app.config.get("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return None
    try:
        import base64

        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        prompt = _dalle_prompt(visual_brief)
        last_error = None
        for model, size in (("gpt-image-1", "1536x1024"), ("dall-e-3", "1792x1024")):
            try:
                kwargs = {"model": model, "prompt": prompt, "size": size, "n": 1}
                if model == "dall-e-3":
                    kwargs["quality"] = "standard"
                resp = client.images.generate(**kwargs)
                item = resp.data[0]
                b64 = getattr(item, "b64_json", None)
                if b64:
                    return base64.b64decode(b64)
                url = getattr(item, "url", None)
                if not url:
                    continue
                img_resp = requests.get(url, timeout=90)
                img_resp.raise_for_status()
                return img_resp.content
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning("OpenAI image model %s failed: %s", model, exc)
        if last_error:
            logger.info("OpenAI image generation unavailable — branded fallback")
    except Exception:  # noqa: BLE001
        logger.exception("OpenAI image generation failed — using branded fallback")
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


def _scene_kind(subject: str, theme: str | None = None) -> str:
    text = f"{theme or ''} {subject or ''}".lower()
    if any(token in text for token in ("pro", "appel", "décroche", "decroche", "standard", "occupé", "occupe")):
        return "call"
    if any(token in text for token in ("annuaire", "trouver", "rdv", "home", "particulier", "près", "pres")):
        return "booking"
    return "trade"


def _selling_copy(kind: str, headline: str, subject: str) -> tuple[str, str, str]:
    title = (headline or subject or "PilotCore").strip()
    if kind == "call":
        return title, "Le standard décroche. Vous restez sur le chantier.", "Essayer 14 jours"
    if kind == "booking":
        return title, "Un artisan près de chez vous — un créneau en 2 minutes.", "Réserver un créneau"
    return title, "Dépannage le jour même. Agenda en ligne 24h/24.", "Trouver un artisan"


def _draw_phone(draw, x: int, y: int, *, ink, paper, cream) -> None:
    draw.rounded_rectangle((x, y, x + 168, y + 300), radius=22, fill=ink)
    draw.rounded_rectangle((x + 12, y + 36, x + 156, y + 268), radius=8, fill=cream)
    draw.rounded_rectangle((x + 64, y + 12, x + 104, y + 22), radius=4, fill=paper)
    # missed-call pulse
    draw.ellipse((x + 118, y + 48, x + 142, y + 72), fill=_hex_rgb("#C45C26"))
    draw.rounded_rectangle((x + 28, y + 88, x + 140, y + 112), radius=4, fill=ink)
    draw.rounded_rectangle((x + 28, y + 128, x + 140, y + 152), radius=4, fill=_hex_rgb("#D7CDB8"))
    draw.rounded_rectangle((x + 28, y + 168, x + 140, y + 192), radius=4, fill=_hex_rgb("#D7CDB8"))
    draw.ellipse((x + 68, y + 276, x + 100, y + 290), outline=paper, width=2)


def _draw_calendar(draw, x: int, y: int, *, ink, cream, paper) -> None:
    draw.rounded_rectangle((x, y, x + 220, y + 200), radius=8, fill=cream, outline=ink, width=3)
    draw.rectangle((x, y, x + 220, y + 44), fill=ink)
    cell = 28
    ox, oy = x + 18, y + 64
    for row in range(4):
        for col in range(6):
            cx, cy = ox + col * 32, oy + row * 32
            fill = ink if (row, col) in {(1, 2), (2, 4)} else paper
            draw.rounded_rectangle((cx, cy, cx + cell, cy + cell), radius=4, fill=fill)


def _draw_tools(draw, x: int, y: int, *, ink, cream) -> None:
    draw.ellipse((x, y, x + 160, y + 160), fill=cream, outline=ink, width=4)
    draw.rectangle((x + 72, y + 28, x + 88, y + 132), fill=ink)
    draw.polygon([(x + 40, y + 48), (x + 120, y + 88), (x + 40, y + 108)], fill=ink)
    draw.rounded_rectangle((x + 24, y + 176, x + 200, y + 248), radius=8, fill=cream, outline=ink, width=3)
    draw.ellipse((x + 48, y + 196, x + 88, y + 228), outline=ink, width=3)
    draw.ellipse((x + 136, y + 196, x + 176, y + 228), outline=ink, width=3)


def _draw_selling_scene(draw, kind: str, *, ink, cream, paper) -> None:
    origin_x, origin_y = 820, 150
    if kind == "call":
        _draw_phone(draw, origin_x + 40, origin_y, ink=ink, paper=paper, cream=cream)
        return
    if kind == "booking":
        _draw_calendar(draw, origin_x, origin_y + 20, ink=ink, cream=cream, paper=paper)
        return
    _draw_tools(draw, origin_x, origin_y + 10, ink=ink, cream=cream)


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


def _branded_fallback(headline: str, subject: str, theme: str | None = None) -> bytes:
    from PIL import Image, ImageDraw

    paper = _hex_rgb("#EFE9DC")
    cream = _hex_rgb("#FBF7EE")
    ink = _hex_rgb("#1A2332")
    rule = _hex_rgb("#D7CDB8")
    muted = _hex_rgb("#6B6458")
    kind = _scene_kind(subject, theme)
    title, subline, cta = _selling_copy(kind, headline, subject)

    img = Image.new("RGB", (WIDTH, HEIGHT), paper)
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, 96, HEIGHT), fill=ink)
    draw.rectangle((96, 0, WIDTH, HEIGHT), fill=cream)
    draw.rectangle((96, 0, 100, HEIGHT), fill=rule)
    draw.rectangle((0, HEIGHT - 12, WIDTH, HEIGHT), fill=ink)
    for y in range(48, HEIGHT - 48, 28):
        draw.line((120, y, 760, y), fill=_hex_rgb("#E8E0D0"), width=1)

    _draw_brand_icon(draw, 48, 72)
    draw.text((24, HEIGHT - 52), "PilotCore", font=_load_font(16, bold=True), fill=_hex_rgb("#F6F1E6"))

    _draw_selling_scene(draw, kind, ink=ink, cream=cream, paper=paper)

    kicker = _load_font(18, bold=True)
    draw.text((140, 72), "ANNUAIRE · STANDARD IA", font=kicker, fill=muted)

    title_font = _load_font(52, bold=True)
    lines = _wrap_text(draw, title[:70], title_font, 640)
    y = 140
    for line in lines:
        draw.text((140, y), line, font=title_font, fill=ink)
        y += 62

    sub_font = _load_font(22, bold=False)
    for line in _wrap_text(draw, subline, sub_font, 620):
        draw.text((140, y + 8), line, font=sub_font, fill=muted)
        y += 32

    cta_font = _load_font(22, bold=True)
    cta_w = int(_text_width(draw, cta, cta_font) + 48)
    cy0 = min(y + 36, HEIGHT - 90)
    draw.rounded_rectangle((140, cy0, 140 + cta_w, cy0 + 52), radius=4, fill=ink)
    draw.text((164, cy0 + 12), cta, font=cta_font, fill=_hex_rgb("#F6F1E6"))

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

    font = _load_font(42, bold=True)
    lines = _wrap_text(draw, headline, font, WIDTH - 80)
    y = HEIGHT - 86 - len(lines) * 46
    for line in lines:
        draw.text((40, y), line, font=font, fill=_hex_rgb("#F6F1E6"))
        y += 46
    draw.text((40, HEIGHT - 48), "PilotCore  ·  RDV en ligne 24h/24", font=_load_font(18, bold=True), fill=_hex_rgb("#E8E4D6"))

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
    use_dalle: bool | None = None,
    theme: str | None = None,
) -> dict:
    """Generate a branded PNG and return ``image_path`` + ``image_url`` + ``png``."""
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
    if use_dalle is None:
        use_dalle = bool((current_app.config.get("OPENAI_API_KEY") or "").strip()) and not current_app.config.get("TESTING")

    raw = _try_dalle(visual) if use_dalle else None
    try:
        if raw:
            png = _apply_brand_overlay(raw, headline_text)
        else:
            png = _branded_fallback(headline_text, subject, theme=theme)
    except Exception:  # noqa: BLE001
        logger.exception("Social image render failed — minimal fallback")
        png = _branded_fallback("PilotCore", subject, theme=theme)

    relative = _save_png(png)
    return {
        "image_path": relative,
        "image_url": image_public_url(relative),
        "image_headline": headline_text,
        "png": png,
    }


def materialize_post_image(post) -> Path | None:
    """Ensure the PNG exists on disk (from path or DB blob)."""
    existing = resolve_image_path(getattr(post, "image_path", None))
    if existing:
        return existing
    blob = getattr(post, "image_blob", None)
    if not blob:
        return None
    path = write_image_bytes(getattr(post, "image_path", None), bytes(blob))
    if path is None:
        return None
    if not getattr(post, "image_path", None):
        post.image_path = f"{MEDIA_PREFIX}/{path.name}"
    return path


def ensure_post_visual(post, *, subject: str | None = None, use_dalle: bool = False, theme: str | None = None):
    """Create a selling visual if the queued post has none (or the file vanished)."""
    from app.core.extensions import db

    if materialize_post_image(post):
        if getattr(post, "image_blob", None) is None:
            path = resolve_image_path(post.image_path)
            if path:
                post.image_blob = path.read_bytes()
                db.session.commit()
        return post
    first = ((subject or getattr(post, "message", None) or "PilotCore").split("\n")[0]).strip()[:60]
    visual = generate_for_post(
        subject or (post.message or "PilotCore"),
        headline=first or "PilotCore",
        visual_brief=subject or (post.message or "PilotCore"),
        use_dalle=use_dalle,
        theme=theme or getattr(post, "target_key", None),
    )
    post.image_path = visual["image_path"]
    post.image_blob = visual["png"]
    db.session.commit()
    return post


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
