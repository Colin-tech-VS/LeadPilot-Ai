"""Recipient address validation — reject harvested false positives before send.

Web-harvested prospect e-mails are polluted with strings that *look* like an
address but never receive mail. The worst offenders are retina asset
references pulled straight out of the page HTML/CSS — ``logo@2x.png``,
``sprite@3x.jpg``, ``icon@2x.svg`` — which the harvesting regex captures as
``local@domain.tld`` because ``png``/``jpg``/``svg`` are valid-looking TLDs.

Sending to one of those yields an immediate *"Bounced by relay"* from the
recipient MX. In volume those bounces poison the sending domain's reputation,
at which point the relay starts bouncing *legitimate* prospecting mail too —
turning a handful of junk addresses into « tous les e-mails de prospection
rebondissent ». This guard stops those sends before they ever reach SMTP.

Pure syntax/asset filtering only — no DNS or network calls, so it is safe to
run inline in the send path and deterministic in tests.
"""
from __future__ import annotations

import re

# TLDs that are really file extensions harvested from CSS/HTML asset refs, not
# real mail domains. ``logo@2x.png`` -> domain ``2x.png`` -> TLD ``png``.
NON_MAIL_TLDS = frozenset(
    {
        # images
        "png", "jpg", "jpeg", "gif", "svg", "webp", "bmp", "ico", "tif", "tiff", "avif",
        # web assets
        "css", "js", "mjs", "json", "xml", "map", "scss", "less",
        # fonts
        "woff", "woff2", "ttf", "otf", "eot",
        # media
        "mp4", "mp3", "wav", "ogg", "webm", "avi", "mov", "m4a", "flac",
        # documents / archives
        "pdf", "zip", "gz", "tar", "rar", "7z",
        "doc", "docx", "xls", "xlsx", "ppt", "pptx", "csv",
    }
)

# A pragmatic RFC-ish address shape. Deliberately close to the harvesting regex
# so the two agree on what a "candidate" address is.
_ADDR_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")

# Retina / density asset references: ``@2x.``, ``@3x.``, ``@1.5x.`` …
_RETINA_RE = re.compile(r"@\d+(?:\.\d+)?x\.", re.IGNORECASE)


def _tld(addr: str) -> str:
    return addr.rsplit(".", 1)[-1].lower() if "." in addr else ""


def looks_like_asset(addr: str) -> bool:
    """True when the "address" is really a file reference (retina image, asset)."""
    if not addr:
        return False
    if _RETINA_RE.search(addr):
        return True
    return _tld(addr) in NON_MAIL_TLDS


def check_recipient(addr: str | None) -> tuple[bool, str]:
    """Return ``(deliverable, reason)`` for a candidate recipient address.

    ``reason`` is an empty string when deliverable, otherwise a short,
    user-facing French explanation of why the address was rejected.
    """
    email = (addr or "").strip().lower()
    if not email:
        return False, "adresse vide"
    if email.count("@") != 1:
        return False, "adresse mal formée"
    if not _ADDR_RE.match(email):
        return False, "adresse mal formée"
    if looks_like_asset(email):
        return False, "référence de fichier/image, pas une vraie adresse"
    return True, ""


def is_valid_recipient(addr: str | None) -> bool:
    return check_recipient(addr)[0]
