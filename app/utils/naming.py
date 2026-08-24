"""Presentation helpers for registry data.

The official register stores names in uppercase — "SOCIETE ANNECIENNE DE
DEPANNAGE", "ANNECY". Rendering that as-is in a title tag, an H1 or a meta
description reads as shouting and measurably costs click-through, so it is
cased for display. The stored value is never modified: this is a view concern.
"""
from __future__ import annotations

import re

# Kept uppercase: legal forms and common trade abbreviations that would look
# wrong title-cased ("Sarl", "Ets", "Sas").
_KEEP_UPPER = {
    "SARL", "SAS", "SASU", "SA", "SCI", "SCOP", "SNC", "EURL", "SELARL",
    "ETS", "EI", "EIRL", "GIE", "SEM", "SCM", "SCP", "TP", "BTP", "CVC",
    "PVC", "VMC", "RGE", "ADI", "JLG", "MJC",
}
# Kept lowercase when inside a name, never at the start.
_KEEP_LOWER = {"de", "du", "des", "la", "le", "les", "et", "en", "sur", "sous", "aux", "au", "d", "l"}

_ACRONYM_DOTS = re.compile(r"^(?:[A-Z]\.){2,}[A-Z]?\.?$")


def _word(token: str, first: bool) -> str:
    bare = token.strip("()[]«».,")
    if not bare:
        return token
    upper = bare.upper()
    # A.D.I. and friends stay as they are.
    if _ACRONYM_DOTS.match(bare):
        return token
    if upper in _KEEP_UPPER:
        return token.replace(bare, upper)
    if not first and bare.lower() in _KEEP_LOWER:
        return token.replace(bare, bare.lower())
    # Case each alphabetic run, then lower the particles that sit *inside* the
    # name: "SAINT-BONNET-EN-CHAMPSAUR" -> "Saint-Bonnet-en-Champsaur",
    # "D'INSTALLATION" -> "d'Installation".
    parts = re.split(r"([^\w]|_)", bare, flags=re.UNICODE)
    out: list[str] = []
    seen_word = False
    for part in parts:
        if not part or not part[0].isalpha():
            out.append(part)
            continue
        low = part.lower()
        if seen_word and low in _KEEP_LOWER:
            out.append(low)
        else:
            out.append(part.capitalize())
        seen_word = True
    cased = "".join(out)
    if first and cased[:1].islower():
        cased = cased[0].upper() + cased[1:]
    return token.replace(bare, cased)


def display_name(value: str | None) -> str:
    """Human-friendly casing for an all-caps registry name.

    Names that are not fully uppercase are left alone — the registrant chose
    that casing and it is more likely to be right than our guess.
    """
    value = (value or "").strip()
    if not value or value != value.upper():
        return value
    tokens = value.split()
    return " ".join(_word(t, i == 0) for i, t in enumerate(tokens))


def display_city(value: str | None) -> str:
    """Same treatment for commune names, which the register also shouts."""
    return display_name(value)
