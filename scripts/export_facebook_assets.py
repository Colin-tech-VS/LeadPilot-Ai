"""Export PilotCore logo + Facebook profile / cover assets (paper/ink)."""
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "static" / "images"
DESKTOP = Path.home() / "Desktop" / "PilotCore-Facebook"

INK = "#1A2332"
PAPER = "#EFE9DC"
CREAM = "#FBF7EE"
LINE = "#C4B79A"
MUTED = "#6B6458"
NEEDLE = "#F6F1E6"

LOGO_MARK = f"""
<svg viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
  <rect x="4" y="4" width="56" height="56" rx="6" fill="{INK}"/>
  <circle cx="32" cy="32" r="16" stroke="{NEEDLE}" stroke-opacity="0.22" stroke-width="1.5"/>
  <path d="M32 18 L36.5 38 L32 33.5 L27.5 38 Z" fill="{NEEDLE}"/>
  <circle cx="32" cy="32" r="3.5" fill="{NEEDLE}"/>
  <path d="M18 46h28" stroke="{NEEDLE}" stroke-opacity="0.35" stroke-width="1.5" stroke-linecap="square"/>
</svg>
"""

FONTS = (
    "https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,650"
    "&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap"
)


def _page_html(body: str, extra_css: str = "") -> str:
    return f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="{FONTS}">
<style>
  html, body {{ margin: 0; padding: 0; background: transparent; }}
  * {{ box-sizing: border-box; }}
  {extra_css}
</style></head><body>{body}</body></html>"""


PROFILE_CSS = """
.sheet {
  width: 100vw; height: 100vh;
  background: #EFE9DC;
  display: flex; align-items: center; justify-content: center;
}
.sheet svg { width: 72%; height: 72%; display: block; }
"""

LOGO_CSS = """
.sheet {
  width: 100vw; height: 100vh;
  background: transparent;
  display: flex; align-items: center; justify-content: center;
}
.sheet svg { width: 100%; height: 100%; display: block; }
"""

BANNER_CSS = """
.banner {
  width: 100vw; height: 100vh;
  background: #EFE9DC;
  position: relative;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
  color: #1A2332;
}
.banner::before {
  content: "";
  position: absolute;
  inset: 7.5%;
  border: 1px solid #C4B79A;
  border-radius: 6px;
  background: #FBF7EE;
  pointer-events: none;
}
.ring {
  position: absolute;
  right: 14%;
  top: 50%;
  transform: translateY(-50%);
  width: 34vh;
  height: 34vh;
  border: 1.5px solid rgba(26, 35, 50, 0.08);
  border-radius: 50%;
  pointer-events: none;
}
.ring--inner {
  width: 22vh;
  height: 22vh;
  right: calc(14% + 6vh);
}
.cluster {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  gap: 4.2vh;
  transform: translateY(-2%);
}
.mark { width: 22vh; height: 22vh; flex-shrink: 0; }
.mark svg { width: 100%; height: 100%; display: block; }
.copy { display: flex; flex-direction: column; gap: 1.4vh; }
.wordmark {
  font-family: Fraunces, Georgia, serif;
  font-optical-sizing: auto;
  font-weight: 650;
  font-size: 11.4vh;
  line-height: 0.95;
  letter-spacing: -0.03em;
  margin: 0;
}
.tag {
  margin: 0;
  font-size: 2.55vh;
  font-weight: 600;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: #6B6458;
}
.url {
  margin: 0.4vh 0 0;
  font-size: 2.2vh;
  font-weight: 500;
  letter-spacing: 0.04em;
  color: #1A2332;
}
"""

BANNER_BODY = f"""
<div class="banner">
  <div class="ring"></div>
  <div class="ring ring--inner"></div>
  <div class="cluster">
    <div class="mark">{LOGO_MARK}</div>
    <div class="copy">
      <p class="wordmark">PilotCore</p>
      <p class="tag">Le copilote des artisans</p>
      <p class="url">pilotcore.fr</p>
    </div>
  </div>
</div>
"""


def _shot(page, html: str, width: int, height: int, path: Path, omit_bg: bool = False) -> None:
    page.set_viewport_size({"width": width, "height": height})
    page.set_content(html, wait_until="networkidle")
    page.wait_for_timeout(400)
    page.screenshot(path=str(path), omit_background=omit_bg)
    print(f"Wrote {path.name} ({width}x{height})")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    DESKTOP.mkdir(parents=True, exist_ok=True)

    logo_html = _page_html(f'<div class="sheet">{LOGO_MARK}</div>', LOGO_CSS)
    profile_html = _page_html(f'<div class="sheet">{LOGO_MARK}</div>', PROFILE_CSS)
    banner_html = _page_html(BANNER_BODY, BANNER_CSS)

    jobs = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(device_scale_factor=1)

        for size, name in ((1024, "logo-1024.png"), (512, "logo-512.png")):
            dest = OUT / name
            _shot(page, logo_html, size, size, dest, omit_bg=True)
            jobs.append(dest)

        for size, name in (
            (1024, "logo-facebook-1024.png"),
            (512, "logo-facebook-512.png"),
            (320, "logo-facebook-320.png"),
            (512, "icon-512.png"),
            (192, "icon-192.png"),
            (180, "apple-touch-icon.png"),
        ):
            dest = OUT / name
            _shot(page, profile_html, size, size, dest, omit_bg=False)
            jobs.append(dest)

        maskable_css = PROFILE_CSS.replace("width: 72%; height: 72%;", "width: 58%; height: 58%;")
        maskable_html = _page_html(f'<div class="sheet">{LOGO_MARK}</div>', maskable_css)
        dest = OUT / "icon-maskable-512.png"
        _shot(page, maskable_html, 512, 512, dest, omit_bg=False)
        jobs.append(dest)

        for size, name in ((48, "favicon-48.png"), (32, "favicon-32.png")):
            dest = OUT / name
            _shot(page, logo_html, size, size, dest, omit_bg=True)
            jobs.append(dest)

        for w, h, name in (
            (1640, 624, "banner-facebook-1640x624.png"),
            (851, 315, "banner-facebook-851x315.png"),
            (820, 312, "banner-facebook-820x312.png"),
            (1200, 630, "og-image.png"),
        ):
            dest = OUT / name
            _shot(page, banner_html, w, h, dest, omit_bg=False)
            jobs.append(dest)

        browser.close()

    from PIL import Image

    ico_src = Image.open(OUT / "favicon-48.png").convert("RGBA")
    ico_path = OUT / "favicon.ico"
    ico_src.save(
        ico_path,
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48)],
    )
    print(f"Wrote {ico_path.name}")
    jobs.append(ico_path)

    aliases = {
        "admin-icon-192.png": "icon-192.png",
        "admin-icon-512.png": "icon-512.png",
        "admin-icon-maskable-512.png": "icon-maskable-512.png",
        "admin-touch-icon-180.png": "apple-touch-icon.png",
    }
    for dest_name, src_name in aliases.items():
        src = OUT / src_name
        dest = OUT / dest_name
        dest.write_bytes(src.read_bytes())
        print(f"Aliased {dest_name} <- {src_name}")
        jobs.append(dest)

    for src in jobs:
        target = DESKTOP / src.name
        target.write_bytes(src.read_bytes())
        print(f"Copied {src.name} -> {target}")

    svg_logo = OUT / "logo.svg"
    if svg_logo.exists():
        (DESKTOP / "logo.svg").write_bytes(svg_logo.read_bytes())
        print("Copied logo.svg")


if __name__ == "__main__":
    main()
