"""Block document -> e-mail HTML.

The campaign editor manipulates a small JSON document (``settings`` + an ordered
list of ``blocks``); this module is the only thing that turns it into HTML. The
preview iframe, the test send and the real send all call the same function, so
what the admin sees in the editor is byte-for-byte what lands in the mailbox.

Why render server-side rather than let the editor emit HTML:

* Mail clients need table layout and inline styles. Building that in the browser
  and posting it back means trusting a DOM we cannot audit; here the HTML is a
  pure function of a document we validate.
* Personalisation happens per recipient. The design is rendered once per person
  with their own merge values and their own unsubscribe link — the editor never
  needs to know about that.
* The unsubscribe footer is appended by the renderer, not by a block. A block
  can be deleted; a legal obligation cannot.
"""
from __future__ import annotations

import html as html_lib
import re
import uuid

from app.services.transactional_email import (
    BORDER,
    BORDER_STRONG,
    BRAND,
    CREAM,
    FONT_BODY,
    FONT_DISPLAY,
    INK,
    INK_DEEP,
    INK_MUTED,
    PAPER,
    SURFACE,
    _base_url,
)

BLOCK_TYPES = (
    "header",
    "heading",
    "text",
    "button",
    "image",
    "divider",
    "spacer",
    "list",
    "offer",
    "stats",
    "quote",
    "footer",
)

DEFAULT_SETTINGS = {
    "bg": PAPER,
    "surface": SURFACE,
    "ink": INK,
    "muted": INK_MUTED,
    "accent": INK_DEEP,
    "border": BORDER_STRONG,
    "width": 600,
}

MERGE_TAGS = [
    {"tag": "{{salutation}}", "label": "Bonjour + prénom si connu", "sample": "Bonjour Julien"},
    {"tag": "{{prenom}}", "label": "Prénom du contact (vide si inconnu)", "sample": "Julien"},
    {"tag": "{{entreprise}}", "label": "Nom de l'entreprise", "sample": "Dupont Plomberie"},
    {"tag": "{{ville}}", "label": "Ville", "sample": "Lyon"},
    {"tag": "{{metier}}", "label": "Métier", "sample": "plombier"},
    {"tag": "{{email}}", "label": "E-mail du contact", "sample": "contact@exemple.fr"},
    {"tag": "{{site}}", "label": "Adresse du site", "sample": "www.pilotcore.fr"},
    {"tag": "{{lien_inscription}}", "label": "Lien d'inscription", "sample": "/register"},
    {"tag": "{{lien_desinscription}}", "label": "Lien de désinscription", "sample": "/desinscription/…"},
]

# Anything that can execute, load remote code or break the layout is removed —
# the editor is trusted, pasted content is not.
_TAG_BLOCKLIST = re.compile(
    r"<\s*/?\s*(script|style|iframe|object|embed|form|input|link|meta)[^>]*>", re.IGNORECASE
)
_EVENT_ATTR = re.compile(r"\son[a-z]+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.IGNORECASE)
_JS_URL = re.compile(r"(href|src)\s*=\s*([\"'])\s*javascript:[^\"']*\2", re.IGNORECASE)


def new_block_id() -> str:
    return uuid.uuid4().hex[:10]


# --------------------------------------------------------------------------- #
# Sanitising / escaping
# --------------------------------------------------------------------------- #
def sanitize_html(value: str) -> str:
    """Keep the light formatting the editor produces, drop everything active."""
    text = value or ""
    text = _TAG_BLOCKLIST.sub("", text)
    text = _EVENT_ATTR.sub("", text)
    text = _JS_URL.sub(r'\1="#"', text)
    return text.strip()


def _esc(value) -> str:
    return html_lib.escape(str(value or ""), quote=True)


def _url(value: str, fallback: str = "#") -> str:
    raw = (value or "").strip()
    if not raw:
        return fallback
    if raw.startswith(("http://", "https://", "mailto:", "tel:", "{{")):
        return _esc(raw)
    if raw.startswith("/"):
        return _esc(_base_url() + raw)
    return _esc("https://" + raw)


def _px(value, default: int, *, lo: int = 0, hi: int = 400) -> int:
    try:
        return max(lo, min(int(value), hi))
    except (TypeError, ValueError):
        return default


def _align(value: str) -> str:
    return value if value in ("left", "center", "right") else "left"


def _color(value, default: str) -> str:
    raw = str(value or "").strip()
    return raw if re.fullmatch(r"#[0-9A-Fa-f]{3,8}|[a-zA-Z]{3,20}", raw) else default


def settings_of(design: dict) -> dict:
    merged = dict(DEFAULT_SETTINGS)
    raw = (design or {}).get("settings") or {}
    for key, default in DEFAULT_SETTINGS.items():
        if key == "width":
            merged[key] = _px(raw.get(key), int(default), lo=320, hi=760)
        else:
            merged[key] = _color(raw.get(key), str(default))
    return merged


def blocks_of(design: dict) -> list[dict]:
    raw = (design or {}).get("blocks")
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        btype = item.get("type")
        if btype not in BLOCK_TYPES:
            continue
        block = dict(item)
        block.setdefault("id", new_block_id())
        out.append(block)
    return out


# --------------------------------------------------------------------------- #
# Merge tags
# --------------------------------------------------------------------------- #
def merge_context(
    *,
    first_name: str | None = None,
    company_name: str | None = None,
    city: str | None = None,
    trade_type: str | None = None,
    email: str | None = None,
    unsubscribe_url: str | None = None,
) -> dict:
    from app.constants.trades import trade_label

    base = _base_url()
    given = (first_name or "").strip()
    return {
        # Sourced registers rarely carry a first name, so ``prenom`` must be
        # safe to be empty: ``salutation`` is what a greeting line should use.
        "salutation": f"Bonjour {given}" if given else "Bonjour",
        "prenom": given,
        "entreprise": (company_name or "").strip() or "votre entreprise",
        "ville": (city or "").strip() or "votre secteur",
        "metier": trade_label(trade_type, "fr").lower() if trade_type else "artisan",
        "email": (email or "").strip(),
        "site": base.replace("https://", "").replace("http://", ""),
        "lien_inscription": f"{base}/register",
        "lien_desinscription": unsubscribe_url or f"{base}/contact",
    }


def sample_context() -> dict:
    """Merge values for the editor preview, where there is no recipient yet."""
    base = _base_url()
    return {
        "salutation": "Bonjour Julien",
        "prenom": "Julien",
        "entreprise": "Dupont Plomberie",
        "ville": "Lyon",
        "metier": "plombier",
        "email": "contact@dupont-plomberie.fr",
        "site": base.replace("https://", "").replace("http://", ""),
        "lien_inscription": f"{base}/register",
        "lien_desinscription": f"{base}/desinscription/apercu",
    }


_TAG_RE = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")


def apply_merge(value: str, ctx: dict) -> str:
    """Replace ``{{tag}}`` with its value; an unknown tag is removed, never shown."""
    if not value:
        return ""

    def sub(match):
        return str(ctx.get(match.group(1), ""))

    text = _TAG_RE.sub(sub, value)
    # A tag that resolved to nothing leaves a hole: "Bonjour ," or a double
    # space mid-sentence. Nobody proofreads 200 personalised copies, so the
    # tidying happens here rather than in the copy.
    text = re.sub(r"[ \t]{2,}", " ", text)
    # Only the comma and the full stop: French typography *wants* a space
    # before ; : ! ?, so stripping those would introduce a different error.
    text = re.sub(r"[ \t]+([,.])", r"\1", text)
    return text


# --------------------------------------------------------------------------- #
# Block renderers — each returns one <tr> of the body table
# --------------------------------------------------------------------------- #
def _row(inner: str, *, padding: str, bg: str | None = None) -> str:
    background = f"background:{bg};" if bg else ""
    return f'<tr><td style="padding:{padding};{background}">{inner}</td></tr>'


def _render_header(b, s, ctx) -> str:
    base = _base_url()
    title = apply_merge(b.get("title") or BRAND, ctx)
    tagline = apply_merge(b.get("tagline") or "", ctx)
    align = _align(b.get("align") or "left")
    logo = ""
    if b.get("logo", True):
        logo = (
            f'<img src="{base}/static/images/logo-512.png" width="36" height="36" alt="{_esc(title)}"'
            f' style="vertical-align:middle;border:0;display:inline-block;">'
        )
    tagline_html = (
        f'<p style="margin:10px 0 0;font-size:12px;letter-spacing:.14em;text-transform:uppercase;'
        f'color:{s["muted"]};font-family:{FONT_BODY};">{_esc(tagline)}</p>'
        if tagline
        else ""
    )
    return _row(
        f'<div style="text-align:{align};">{logo}'
        f'<span style="font-size:22px;font-weight:650;color:{s["ink"]};letter-spacing:-0.03em;'
        f'font-family:{FONT_DISPLAY};vertical-align:middle;margin-left:{"10px" if logo else "0"};">'
        f"{_esc(title)}</span>{tagline_html}</div>",
        padding="24px 32px 10px",
    )


def _render_heading(b, s, ctx) -> str:
    text = apply_merge(b.get("text") or "", ctx)
    size = _px(b.get("size"), 26, lo=14, hi=48)
    return _row(
        f'<h1 style="margin:0;font-size:{size}px;line-height:1.22;font-weight:650;'
        f'letter-spacing:-0.03em;color:{_color(b.get("color"), s["ink"])};'
        f'font-family:{FONT_DISPLAY};text-align:{_align(b.get("align"))};">{_esc(text)}</h1>',
        padding="14px 32px 6px",
    )


def _render_text(b, s, ctx) -> str:
    body = apply_merge(sanitize_html(b.get("html") or b.get("text") or ""), ctx)
    if not body:
        return ""
    if not body.lstrip().startswith("<"):
        body = "".join(f"<p>{line}</p>" for line in body.split("\n") if line.strip())
    size = _px(b.get("size"), 16, lo=11, hi=28)
    styled = body.replace(
        "<p>",
        f'<p style="margin:0 0 14px;font-size:{size}px;line-height:1.65;'
        f'color:{_color(b.get("color"), s["ink"])};font-family:{FONT_BODY};'
        f'text-align:{_align(b.get("align"))};">',
    )
    return _row(styled, padding="6px 32px 6px")


def _render_button(b, s, ctx) -> str:
    label = apply_merge(b.get("label") or "En savoir plus", ctx)
    url = _url(apply_merge(b.get("url") or "", ctx), f"{_base_url()}/register")
    bg = _color(b.get("bg"), s["accent"])
    fg = _color(b.get("color"), CREAM)
    align = _align(b.get("align") or "left")
    return _row(
        f'<table role="presentation" cellpadding="0" cellspacing="0" align="{align}" '
        f'style="margin:{"0 auto" if align == "center" else "0"};">'
        f'<tr><td style="background:{bg};border-radius:4px;">'
        f'<a href="{url}" style="display:inline-block;padding:13px 26px;font-size:15px;font-weight:700;'
        f'letter-spacing:.02em;color:{fg};text-decoration:none;border-radius:4px;'
        f'font-family:{FONT_BODY};">{_esc(label)}</a></td></tr></table>',
        padding="14px 32px 14px",
    )


def _render_image(b, s, ctx) -> str:
    src = (b.get("src") or "").strip()
    if not src:
        return ""
    width = _px(b.get("width"), 520, lo=40, hi=760)
    align = _align(b.get("align") or "center")
    img = (
        f'<img src="{_url(src)}" alt="{_esc(b.get("alt") or "")}" width="{width}" '
        f'style="display:inline-block;max-width:100%;height:auto;border:0;border-radius:4px;">'
    )
    href = (b.get("href") or "").strip()
    if href:
        img = f'<a href="{_url(apply_merge(href, ctx))}">{img}</a>'
    return _row(f'<div style="text-align:{align};">{img}</div>', padding="10px 32px")


def _render_divider(b, s, ctx) -> str:
    return _row(
        f'<div style="height:1px;background:{_color(b.get("color"), BORDER)};font-size:0;line-height:0;">&nbsp;</div>',
        padding="14px 32px",
    )


def _render_spacer(b, s, ctx) -> str:
    height = _px(b.get("height"), 24, lo=4, hi=120)
    return f'<tr><td style="height:{height}px;font-size:0;line-height:0;">&nbsp;</td></tr>'


def _render_list(b, s, ctx) -> str:
    items = [i for i in (b.get("items") or []) if str(i).strip()]
    if not items:
        return ""
    icon = _esc(b.get("icon") or "✓")
    rows = "".join(
        f'<tr><td width="22" valign="top" style="padding:0 8px 10px 0;font-size:15px;'
        f'color:{_color(b.get("icon_color"), s["accent"])};font-family:{FONT_BODY};">{icon}</td>'
        f'<td valign="top" style="padding:0 0 10px;font-size:15px;line-height:1.55;'
        f'color:{s["ink"]};font-family:{FONT_BODY};">{_esc(apply_merge(str(item), ctx))}</td></tr>'
        for item in items
    )
    return _row(
        f'<table role="presentation" cellpadding="0" cellspacing="0" width="100%">{rows}</table>',
        padding="6px 32px 10px",
    )


def _render_offer(b, s, ctx) -> str:
    name = _esc(apply_merge(b.get("name") or "Offre", ctx))
    price = _esc(apply_merge(b.get("price") or "", ctx))
    period = _esc(apply_merge(b.get("period") or "", ctx))
    desc = _esc(apply_merge(b.get("description") or "", ctx))
    features = [str(f) for f in (b.get("features") or []) if str(f).strip()]
    highlight = bool(b.get("highlight"))
    border = s["accent"] if highlight else s["border"]

    feature_rows = "".join(
        f'<tr><td style="padding:0 0 7px;font-size:14px;line-height:1.5;color:{s["ink"]};'
        f'font-family:{FONT_BODY};">✓&nbsp; {_esc(apply_merge(f, ctx))}</td></tr>'
        for f in features
    )
    cta = ""
    if b.get("cta_label"):
        cta = (
            f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin:14px 0 0;">'
            f'<tr><td style="background:{s["accent"]};border-radius:4px;">'
            f'<a href="{_url(apply_merge(b.get("cta_url") or "", ctx), _base_url() + "/register")}" '
            f'style="display:inline-block;padding:10px 20px;font-size:14px;font-weight:700;color:{CREAM};'
            f'text-decoration:none;border-radius:4px;font-family:{FONT_BODY};">'
            f'{_esc(apply_merge(b.get("cta_label"), ctx))}</a></td></tr></table>'
        )
    badge = ""
    if b.get("badge"):
        badge = (
            f'<span style="display:inline-block;margin-left:8px;padding:3px 8px;font-size:10px;'
            f'font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:{CREAM};'
            f'background:{s["accent"]};border-radius:3px;font-family:{FONT_BODY};">'
            f'{_esc(b.get("badge"))}</span>'
        )
    return _row(
        f'<table role="presentation" cellpadding="0" cellspacing="0" width="100%" '
        f'style="border:1px solid {border};border-radius:4px;background:{PAPER};">'
        f'<tr><td style="padding:18px 20px;">'
        f'<p style="margin:0 0 4px;font-size:17px;font-weight:700;color:{s["ink"]};'
        f'font-family:{FONT_DISPLAY};">{name}{badge}</p>'
        + (
            f'<p style="margin:0 0 10px;font-size:26px;font-weight:650;color:{s["ink"]};'
            f'font-family:{FONT_DISPLAY};letter-spacing:-0.02em;">{price}'
            f'<span style="font-size:13px;font-weight:400;color:{s["muted"]};"> {period}</span></p>'
            if price
            else ""
        )
        + (
            f'<p style="margin:0 0 12px;font-size:14px;line-height:1.6;color:{s["muted"]};'
            f'font-family:{FONT_BODY};">{desc}</p>'
            if desc
            else ""
        )
        + (
            f'<table role="presentation" cellpadding="0" cellspacing="0" width="100%">{feature_rows}</table>'
            if feature_rows
            else ""
        )
        + cta
        + "</td></tr></table>",
        padding="10px 32px",
    )


def _render_stats(b, s, ctx) -> str:
    items = [i for i in (b.get("items") or []) if isinstance(i, dict)][:3]
    if not items:
        return ""
    width = int(100 / len(items))
    cells = "".join(
        f'<td width="{width}%" valign="top" style="padding:0 6px;text-align:center;">'
        f'<p style="margin:0;font-size:26px;font-weight:650;color:{s["ink"]};'
        f'font-family:{FONT_DISPLAY};letter-spacing:-0.02em;">'
        f'{_esc(apply_merge(str(i.get("value") or ""), ctx))}</p>'
        f'<p style="margin:4px 0 0;font-size:12px;line-height:1.4;color:{s["muted"]};'
        f'font-family:{FONT_BODY};">{_esc(apply_merge(str(i.get("label") or ""), ctx))}</p></td>'
        for i in items
    )
    return _row(
        f'<table role="presentation" cellpadding="0" cellspacing="0" width="100%">'
        f"<tr>{cells}</tr></table>",
        padding="14px 32px",
    )


def _render_quote(b, s, ctx) -> str:
    text = _esc(apply_merge(b.get("text") or "", ctx))
    if not text:
        return ""
    author = _esc(apply_merge(b.get("author") or "", ctx))
    author_html = (
        f'<p style="margin:10px 0 0;font-size:13px;color:{s["muted"]};'
        f'font-family:{FONT_BODY};">— {author}</p>'
        if author
        else ""
    )
    return _row(
        f'<table role="presentation" cellpadding="0" cellspacing="0" width="100%">'
        f'<tr><td style="padding:2px 0 2px 16px;border-left:3px solid {s["accent"]};">'
        f'<p style="margin:0;font-size:16px;line-height:1.6;font-style:italic;color:{s["ink"]};'
        f'font-family:{FONT_DISPLAY};">« {text} »</p>{author_html}'
        f"</td></tr></table>",
        padding="12px 32px",
    )


def _render_footer(b, s, ctx) -> str:
    text = apply_merge(sanitize_html(b.get("html") or b.get("text") or ""), ctx)
    if not text:
        return ""
    if not text.lstrip().startswith("<"):
        text = f"<p>{text}</p>"
    styled = text.replace(
        "<p>",
        f'<p style="margin:0 0 6px;font-size:12px;line-height:1.6;color:{s["muted"]};'
        f'font-family:{FONT_BODY};text-align:{_align(b.get("align") or "left")};">',
    )
    return _row(styled, padding="10px 32px 4px")


_RENDERERS = {
    "header": _render_header,
    "heading": _render_heading,
    "text": _render_text,
    "button": _render_button,
    "image": _render_image,
    "divider": _render_divider,
    "spacer": _render_spacer,
    "list": _render_list,
    "offer": _render_offer,
    "stats": _render_stats,
    "quote": _render_quote,
    "footer": _render_footer,
}


# --------------------------------------------------------------------------- #
# Document rendering
# --------------------------------------------------------------------------- #
def _legal_footer(s: dict, ctx: dict) -> str:
    """Sender identity + unsubscribe. Appended to every campaign, always."""
    base = _base_url()
    unsub = ctx.get("lien_desinscription") or f"{base}/contact"
    return (
        f'<tr><td style="padding:16px 32px 20px;background:{s["bg"]};border-top:1px solid {BORDER};">'
        f'<p style="margin:0 0 6px;font-size:11px;font-weight:600;letter-spacing:.14em;'
        f'text-transform:uppercase;color:{s["muted"]};font-family:{FONT_BODY};">'
        f"{BRAND} · Réceptionniste IA pour artisans</p>"
        f'<p style="margin:0;font-size:12px;line-height:1.6;color:{s["muted"]};font-family:{FONT_BODY};">'
        f'Vous recevez cet e-mail en tant que professionnel du bâtiment. '
        f'<a href="{_esc(unsub)}" style="color:{s["muted"]};text-decoration:underline;">Se désinscrire</a>'
        f'&nbsp;·&nbsp;<a href="{base}" style="color:{s["accent"]};text-decoration:none;">'
        f'{base.replace("https://", "")}</a>'
        f'&nbsp;·&nbsp;<a href="{base}/mentions-legales" style="color:{s["muted"]};text-decoration:none;">'
        f"Mentions légales</a></p></td></tr>"
    )


def render_html(design: dict, *, ctx: dict | None = None, preheader: str | None = None) -> str:
    """Render the block document into a complete, mail-client-safe HTML document."""
    ctx = ctx or sample_context()
    s = settings_of(design)
    rows = []
    for block in blocks_of(design):
        renderer = _RENDERERS.get(block["type"])
        if not renderer:
            continue
        try:
            rows.append(renderer(block, s, ctx))
        except Exception:  # noqa: BLE001 — one bad block must not break the mail
            continue
    rows.append(_legal_footer(s, ctx))
    body = "\n".join(r for r in rows if r)

    pre = re.sub(r"<[^>]+>", " ", apply_merge(preheader or "", ctx)).strip()
    return f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light">
<title>{_esc(BRAND)}</title></head>
<body style="margin:0;padding:0;background:{s["bg"]};">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{_esc(pre)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{s["bg"]};padding:32px 12px;">
  <tr><td align="center">
    <table role="presentation" width="{s["width"]}" cellpadding="0" cellspacing="0"
           style="width:100%;max-width:{s["width"]}px;background:{s["surface"]};
                  border:1px solid {s["border"]};border-radius:4px;">
      <tr><td style="height:4px;background:{s["accent"]};font-size:0;line-height:0;">&nbsp;</td></tr>
      {body}
    </table>
  </td></tr>
</table>
</body></html>"""


def render_plain(design: dict, *, ctx: dict | None = None) -> str:
    """Plain-text alternative — every multipart mail needs one to avoid the spam box."""
    ctx = ctx or sample_context()
    lines: list[str] = []
    for block in blocks_of(design):
        btype = block["type"]
        if btype in ("header", "divider", "spacer"):
            continue
        if btype == "heading":
            lines += [apply_merge(block.get("text") or "", ctx), ""]
        elif btype in ("text", "footer"):
            raw = apply_merge(sanitize_html(block.get("html") or block.get("text") or ""), ctx)
            raw = re.sub(r"<br\s*/?>", "\n", raw)
            raw = re.sub(r"</p\s*>", "\n\n", raw)
            text = re.sub(r"<[^>]+>", "", raw)
            lines += [html_lib.unescape(text).strip(), ""]
        elif btype == "button":
            label = apply_merge(block.get("label") or "", ctx)
            url = apply_merge(block.get("url") or "", ctx)
            lines += [f"{label} : {url}", ""]
        elif btype == "list":
            lines += [f"- {apply_merge(str(i), ctx)}" for i in block.get("items") or []]
            lines.append("")
        elif btype == "offer":
            price = apply_merge(block.get("price") or "", ctx)
            period = apply_merge(block.get("period") or "", ctx)
            lines.append(f"{apply_merge(block.get('name') or '', ctx)} — {price} {period}".strip())
            lines += [f"  - {apply_merge(str(f), ctx)}" for f in block.get("features") or []]
            lines.append("")
        elif btype == "stats":
            for item in block.get("items") or []:
                if isinstance(item, dict):
                    lines.append(f"{item.get('value', '')} — {item.get('label', '')}")
            lines.append("")
        elif btype == "quote":
            lines += [f"« {apply_merge(block.get('text') or '', ctx)} »", ""]

    base = _base_url()
    lines += [
        "—",
        f"{BRAND} · {base}",
        f"Se désinscrire : {ctx.get('lien_desinscription') or base + '/contact'}",
    ]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# --------------------------------------------------------------------------- #
# Starter designs
# --------------------------------------------------------------------------- #
def blank_design() -> dict:
    return {
        "settings": dict(DEFAULT_SETTINGS),
        "blocks": [
            {"id": new_block_id(), "type": "header", "title": BRAND, "tagline": "", "logo": True},
            {"id": new_block_id(), "type": "heading", "text": "Votre titre ici", "align": "left"},
            {
                "id": new_block_id(),
                "type": "text",
                "html": "<p>{{salutation}}, écrivez votre message ici.</p>",
            },
            {
                "id": new_block_id(),
                "type": "button",
                "label": "Découvrir",
                "url": "{{lien_inscription}}",
                "align": "left",
            },
        ],
    }


def default_template(kind: str = "offre") -> dict:
    """Ready-made starting points, mirroring what the site actually sells."""
    base = _base_url()
    if kind == "relance":
        blocks = [
            {"id": new_block_id(), "type": "header", "title": BRAND, "tagline": "Relance", "logo": True},
            {"id": new_block_id(), "type": "heading", "text": "Une question rapide"},
            {
                "id": new_block_id(),
                "type": "text",
                "html": (
                    "<p>Je vous ai écrit la semaine dernière au sujet des appels manqués "
                    "chez les {{metier}}s de {{ville}}.</p>"
                    "<p>Si le sujet n'est pas d'actualité, dites-le moi et je n'insiste pas. "
                    "Sinon, une démonstration prend dix minutes.</p>"
                ),
            },
            {
                "id": new_block_id(),
                "type": "button",
                "label": "Voir comment ça marche",
                "url": f"{base}/pro",
            },
        ]
    elif kind == "annonce":
        blocks = [
            {"id": new_block_id(), "type": "header", "title": BRAND, "tagline": "Nouveauté", "logo": True},
            {"id": new_block_id(), "type": "heading", "text": "Du nouveau chez {{site}}"},
            {
                "id": new_block_id(),
                "type": "text",
                "html": "<p>{{salutation}}, voici ce qui change ce mois-ci.</p>",
            },
            {
                "id": new_block_id(),
                "type": "list",
                "items": ["Première nouveauté", "Deuxième nouveauté", "Troisième nouveauté"],
            },
            {"id": new_block_id(), "type": "divider"},
            {
                "id": new_block_id(),
                "type": "button",
                "label": "Voir le détail",
                "url": f"{base}/pro",
                "align": "left",
            },
        ]
    else:  # "offre" — the acquisition mail
        blocks = [
            {"id": new_block_id(), "type": "header", "title": BRAND, "tagline": "Invitation", "logo": True},
            {
                "id": new_block_id(),
                "type": "heading",
                "text": "Les appels manqués coûtent cher à un {{metier}}",
            },
            {
                "id": new_block_id(),
                "type": "text",
                "html": (
                    "<p>{{salutation}},</p>"
                    "<p>Quand vous êtes sur un chantier, le téléphone sonne dans le vide. "
                    "Le client appelle le suivant. {{site}} répond à votre place, qualifie la "
                    "demande et vous transmet le rendez-vous.</p>"
                ),
            },
            {
                "id": new_block_id(),
                "type": "list",
                "items": [
                    "Un standard qui décroche 24h/24, même en intervention",
                    "La demande qualifiée par écrit, avec l'adresse et le besoin",
                    "Le rendez-vous posé dans votre agenda",
                ],
            },
            {"id": new_block_id(), "type": "divider"},
            {
                "id": new_block_id(),
                "type": "button",
                "label": "Essayer gratuitement",
                "url": "{{lien_inscription}}",
                "align": "left",
            },
            {
                "id": new_block_id(),
                "type": "footer",
                "html": "<p>Une question ? Répondez simplement à cet e-mail.</p>",
            },
        ]
    return {"settings": dict(DEFAULT_SETTINGS), "blocks": blocks}
