"""Export PilotCore logo PNGs sized for Facebook (profile picture)."""
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SVG = ROOT / "static" / "images" / "logo-facebook.svg"
OUT_DIR = ROOT / "static" / "images"
SIZES = {
    "logo-facebook-1024.png": 1024,
    "logo-facebook-512.png": 512,
    "logo-facebook-320.png": 320,
}


def main():
    svg = SVG.read_text(encoding="utf-8")
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
html,body{{margin:0;padding:0;background:transparent;}}
</style></head><body>{svg}</body></html>"""

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(device_scale_factor=1)
        for name, size in SIZES.items():
            page.set_viewport_size({"width": size, "height": size})
            page.set_content(html, wait_until="load")
            page.locator("svg").screenshot(path=str(OUT_DIR / name), omit_background=True)
            print(f"Wrote {name} ({size}x{size})")
        browser.close()


if __name__ == "__main__":
    main()
