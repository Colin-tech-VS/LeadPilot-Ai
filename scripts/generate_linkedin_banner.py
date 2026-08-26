"""LinkedIn Company Page cover — 4200×700 (6:1), charte PilotCore.

Upload this PNG as the page cover. LinkedIn renders ~1128×191; the company
logo is overlaid bottom-left, so copy sits in the central safe band.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 4200, 700
PAPER = (239, 233, 220)  # #EFE9DC
CREAM = (246, 241, 230)  # #F6F1E6
INK = (26, 35, 50)  # #1A2332
MUTED = (90, 98, 110)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "static" / "images" / "linkedin-cover.png"


def _font(size: int, *, bold: bool = False, serif: bool = False):
    if serif:
        names = ["Georgia.ttf", "georgia.ttf", "georgiab.ttf" if bold else "georgia.ttf"]
        if bold:
            names = ["georgiab.ttf", "Georgia Bold.ttf"] + names
    elif bold:
        names = ["segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"]
    else:
        names = ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"]
    roots = [Path("C:/Windows/Fonts"), Path("/usr/share/fonts/truetype/dejavu")]
    for root in roots:
        for name in names:
            path = root / name
            if path.is_file():
                return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _compass(draw: ImageDraw.ImageDraw, cx: int, cy: int, r: int) -> None:
    draw.rounded_rectangle((cx - r - 28, cy - r - 28, cx + r + 28, cy + r + 28), radius=18, fill=INK)
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=CREAM, width=4)
    inner = int(r * 0.72)
    draw.ellipse((cx - inner, cy - inner, cx + inner, cy + inner), outline=(90, 98, 110), width=2)
    needle = [
        (cx, cy - int(r * 0.72)),
        (cx + int(r * 0.22), cy + int(r * 0.38)),
        (cx, cy + int(r * 0.12)),
        (cx - int(r * 0.22), cy + int(r * 0.38)),
    ]
    draw.polygon(needle, fill=CREAM)
    draw.ellipse((cx - 14, cy - 14, cx + 14, cy + 14), fill=CREAM)
    y_line = cy + int(r * 0.92)
    draw.line((cx - int(r * 0.85), y_line, cx + int(r * 0.85), y_line), fill=CREAM, width=4)


def main() -> None:
    img = Image.new("RGB", (WIDTH, HEIGHT), PAPER)
    draw = ImageDraw.Draw(img)

    draw.rectangle((0, 0, 36, HEIGHT), fill=INK)
    draw.rectangle((0, HEIGHT - 12, WIDTH, HEIGHT), fill=INK)

    # ledger hairlines, very light
    for x in range(120, WIDTH - 80, 96):
        draw.line((x, 48, x, HEIGHT - 48), fill=(215, 205, 186), width=1)

    # LinkedIn overlays the Page logo on the left — keep copy right of ~780px.
    title_font = _font(210, bold=True, serif=True)
    tag_font = _font(52, bold=True)
    sub_font = _font(48, bold=False)
    url_font = _font(40, bold=True)

    x = 820
    y = 168
    draw.text((x, y - 56), "STANDARD TÉLÉPHONIQUE  ·  ARTISANS", font=tag_font, fill=MUTED)
    draw.text((x, y + 24), "PilotCore", font=title_font, fill=INK)
    draw.text(
        (x, y + 280),
        "Le standard qui décroche. Vous restez sur le chantier.",
        font=sub_font,
        fill=MUTED,
    )

    _compass(draw, WIDTH - 420, HEIGHT // 2 - 8, 118)
    url = "pilotcore.fr"
    tw = draw.textlength(url, font=url_font)
    draw.text((WIDTH - 420 - tw / 2, HEIGHT - 118), url, font=url_font, fill=INK)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, "PNG", optimize=True)
    print(f"wrote {OUT} {img.size} {OUT.stat().st_size} bytes")


if __name__ == "__main__":
    main()
