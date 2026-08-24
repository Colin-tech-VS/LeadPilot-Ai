"""Structured price ranges per trade, parsed out of the generated guides.

Generative engines cite sources that hand them a *fact*, not a brochure. The
single most-asked question in this market is "combien ça coûte" — so the price
grids sitting inside each trade guide are the most citable asset the site has,
provided they are exposed as data rather than buried in one page of thousands.

Provenance matters and is stated everywhere these numbers surface: they are
**indicative ranges produced by a language model** from public market knowledge,
not measured transaction data. Presenting them as a proprietary observatory
would be inventing a provenance. Labelled honestly they are still useful — and
an assistant that repeats them can attribute them correctly.
"""
from __future__ import annotations

import re

# Two shapes show up in generated grids and both are legitimate:
#   "80 € – 150 €"  — the currency repeated in every cell
#   "80 – 150"      — bare numbers, with "Prix TTC (€)" in the column header
# Parsing bare numbers unconditionally would happily read a surface area or a
# year as a price, so that form is only accepted when the table's own caption
# or header row announces that the column holds euros.
_NUM = r"(\d[\d\s\u00a0\u202f]{0,9}?(?:[.,]\d{1,2})?)"
_CUR = r"\s*(?:\u20ac|EUR)"
_SEP = r"\s*(?:\u2013|\u2014|-|\u00e0|to)\s*"
_RANGE_CUR_RE = re.compile(_NUM + _CUR + _SEP + _NUM + _CUR, re.IGNORECASE)
_SINGLE_CUR_RE = re.compile(_NUM + _CUR, re.IGNORECASE)
_RANGE_BARE_RE = re.compile(r"^\s*" + _NUM + _SEP + _NUM + r"\s*(?:\u20ac|EUR)?\s*$", re.IGNORECASE)
_SINGLE_BARE_RE = re.compile(
    r"^\s*(?:\u00e0 partir de\s*|d\u00e8s\s*|env\.?\s*)?" + _NUM + r"\s*(?:\u20ac|EUR)?\s*$",
    re.IGNORECASE,
)
_CURRENCY_HINT_RE = re.compile(r"\u20ac|EUR|\bTTC\b|\bHT\b|\bprix\b|\btarif\b", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.IGNORECASE | re.DOTALL)
_CAPTION_RE = re.compile(r"<caption[^>]*>(.*?)</caption>", re.IGNORECASE | re.DOTALL)


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", html or "")).strip()


def _to_int(raw: str) -> int | None:
    cleaned = raw.replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        return int(round(float(cleaned)))
    except ValueError:
        return None


def parse_price_rows(price_html: str | None) -> list[dict]:
    """Rows of ``{label, min_eur, max_eur}`` from a guide's price table.

    Rows without a readable amount are skipped rather than guessed at, so a
    model that drifts on table shape yields fewer rows instead of wrong ones.
    """
    if not price_html:
        return []

    # Does the table itself say the numeric column is money? Caption plus the
    # header row is enough, and keeps bare-number parsing from running loose.
    caption = " ".join(_text(c) for c in _CAPTION_RE.findall(price_html))
    header = ""
    for row_html in _ROW_RE.findall(price_html):
        if "<th" in row_html.lower():
            header = " ".join(_text(c) for c in _CELL_RE.findall(row_html))
            break
    allow_bare = bool(_CURRENCY_HINT_RE.search(caption + " " + header))

    out: list[dict] = []
    for row_html in _ROW_RE.findall(price_html):
        cells = [_text(c) for c in _CELL_RE.findall(row_html)]
        if len(cells) < 2:
            continue
        label, value = cells[0], " ".join(cells[1:]).strip()
        if not label or not value:
            continue

        lo = hi = None
        m = _RANGE_CUR_RE.search(value)
        if m:
            lo, hi = _to_int(m.group(1)), _to_int(m.group(2))
        else:
            m = _SINGLE_CUR_RE.search(value)
            if m:
                lo = hi = _to_int(m.group(1))
            elif allow_bare:
                m = _RANGE_BARE_RE.match(value)
                if m:
                    lo, hi = _to_int(m.group(1)), _to_int(m.group(2))
                else:
                    m = _SINGLE_BARE_RE.match(value)
                    if m:
                        lo = hi = _to_int(m.group(1))

        if lo is None or hi is None:
            continue
        if lo > hi:
            lo, hi = hi, lo
        out.append({"label": label, "min_eur": lo, "max_eur": hi})
    return out


def trade_prices(lang: str = "fr") -> list[dict]:
    """Every trade that currently has a parsable price grid.

    Shape: ``{trade, label, rows: [{label, min_eur, max_eur}], updated}``.
    """
    from app.constants.trades import TRADES, trade_label
    from app.models.trade_guide import TradeGuide

    guides = {
        g.trade_key: g
        for g in TradeGuide.query.filter_by(lang=lang).all()
    }
    out: list[dict] = []
    for key in (k for k in TRADES if k != "autre"):
        guide = guides.get(key)
        if guide is None:
            continue
        rows = parse_price_rows(guide.price_hints)
        if not rows:
            continue
        out.append(
            {
                "trade": key,
                "label": trade_label(key, lang),
                "rows": rows,
                "updated": guide.generated_at.date().isoformat() if guide.generated_at else None,
            }
        )
    return out


def summary(lang: str = "fr") -> dict:
    """Headline figures an assistant can quote in one sentence."""
    data = trade_prices(lang)
    points = sum(len(t["rows"]) for t in data)
    lows = [r["min_eur"] for t in data for r in t["rows"]]
    highs = [r["max_eur"] for t in data for r in t["rows"]]
    return {
        "trades_covered": len(data),
        "price_points": points,
        "min_eur": min(lows) if lows else None,
        "max_eur": max(highs) if highs else None,
        "updated": max((t["updated"] for t in data if t["updated"]), default=None),
    }


def facts_for(trade_key: str, lang: str = "fr") -> dict | None:
    """Compact answer-first payload for one trade's landing pages.

    ``{min_eur, max_eur, first: {label, min_eur, max_eur}}`` — the numbers a
    reader (or a generative engine) needs to answer "combien ça coûte" in a
    single sentence, or ``None`` when the trade has no parsable grid yet.
    """
    from app.models.trade_guide import TradeGuide

    guide = TradeGuide.query.filter_by(
        trade_key=(trade_key or "").strip().lower(), lang=lang
    ).one_or_none()
    if guide is None:
        return None
    rows = parse_price_rows(guide.price_hints)
    if not rows:
        return None
    return {
        "min_eur": min(r["min_eur"] for r in rows),
        "max_eur": max(r["max_eur"] for r in rows),
        "first": rows[0],
        "rows": rows,
    }
