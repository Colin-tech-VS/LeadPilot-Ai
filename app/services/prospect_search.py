"""Web search helpers for B2B artisan prospecting."""
from __future__ import annotations

import logging
import re
from html import unescape
from urllib.parse import parse_qs, unquote, urlparse

import requests
from flask import current_app

logger = logging.getLogger(__name__)

_USER_AGENT = "PilotCore-Prospecting/1.0 (+https://www.pilotcore.fr)"
_SKIP_DOMAINS = (
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "twitter.com",
    "x.com",
    "youtube.com",
    "tiktok.com",
    "wikipedia.org",
    "leboncoin.fr",
    "indeed.fr",
    "pole-emploi.fr",
)


class ProspectSearchError(Exception):
    """Raised when no search backend is available or the query fails."""


def search_provider() -> str:
    if current_app.config.get("SERPAPI_KEY"):
        return "serpapi"
    if current_app.config.get("GOOGLE_CSE_API_KEY") and current_app.config.get("GOOGLE_CSE_CX"):
        return "google_cse"
    return "duckduckgo"


def is_configured() -> bool:
    return True  # DuckDuckGo fallback always available


def web_search(query: str, *, max_results: int = 10) -> list[dict]:
    """Return organic results: title, url, snippet."""
    provider = search_provider()
    if provider == "serpapi":
        return _serpapi_search(query, max_results=max_results)
    if provider == "google_cse":
        return _google_cse_search(query, max_results=max_results)
    return _duckduckgo_search(query, max_results=max_results)


def _serpapi_search(query: str, *, max_results: int) -> list[dict]:
    api_key = current_app.config.get("SERPAPI_KEY")
    if not api_key:
        raise ProspectSearchError("SERPAPI_KEY manquante.")
    try:
        resp = requests.get(
            "https://serpapi.com/search.json",
            params={
                "engine": "google",
                "q": query,
                "hl": "fr",
                "gl": "fr",
                "num": max_results,
                "api_key": api_key,
            },
            timeout=25,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.exception("SerpAPI search failed")
        raise ProspectSearchError(f"Recherche SerpAPI impossible : {exc}") from exc

    out = []
    for row in data.get("organic_results") or []:
        url = (row.get("link") or "").strip()
        if not url or _should_skip_url(url):
            continue
        out.append(
            {
                "title": (row.get("title") or "").strip(),
                "url": url,
                "snippet": (row.get("snippet") or "").strip(),
            }
        )
    return out[:max_results]


def _google_cse_search(query: str, *, max_results: int) -> list[dict]:
    api_key = current_app.config.get("GOOGLE_CSE_API_KEY")
    cx = current_app.config.get("GOOGLE_CSE_CX")
    if not api_key or not cx:
        raise ProspectSearchError("GOOGLE_CSE_API_KEY ou GOOGLE_CSE_CX manquant.")
    try:
        resp = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params={"key": api_key, "cx": cx, "q": query, "hl": "fr", "num": max_results},
            timeout=25,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Google CSE search failed")
        raise ProspectSearchError(f"Recherche Google CSE impossible : {exc}") from exc

    out = []
    for row in data.get("items") or []:
        url = (row.get("link") or "").strip()
        if not url or _should_skip_url(url):
            continue
        out.append(
            {
                "title": (row.get("title") or "").strip(),
                "url": url,
                "snippet": (row.get("snippet") or "").strip(),
            }
        )
    return out[:max_results]


def _duckduckgo_search(query: str, *, max_results: int) -> list[dict]:
    try:
        resp = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query, "kl": "fr-fr"},
            headers={"User-Agent": _USER_AGENT},
            timeout=20,
        )
        resp.raise_for_status()
        html = resp.text
    except Exception as exc:  # noqa: BLE001
        logger.exception("DuckDuckGo search failed")
        raise ProspectSearchError(f"Recherche web impossible : {exc}") from exc

    links = re.findall(
        r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        html,
        flags=re.I | re.S,
    )
    snippets = re.findall(
        r'class="result__snippet"[^>]*>(.*?)</(?:a|td|div)>',
        html,
        flags=re.I | re.S,
    )
    out = []
    seen = set()
    for idx, (href, title_html) in enumerate(links):
        url = _normalize_ddg_url(href)
        if not url or url in seen or _should_skip_url(url):
            continue
        seen.add(url)
        title = _strip_html(title_html)
        snippet = _strip_html(snippets[idx]) if idx < len(snippets) else ""
        out.append({"title": title, "url": url, "snippet": snippet})
        if len(out) >= max_results:
            break
    return out


def _normalize_ddg_url(href: str) -> str:
    href = unescape((href or "").strip())
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc:
        qs = parse_qs(parsed.query)
        # Organic redirect: the real destination lives in ?uddg=
        if parsed.path.startswith("/l/"):
            return unquote(qs.get("uddg", [""])[0])
        # Sponsored ad redirect (/y.js): recover the advertiser domain instead
        # of storing the 700+ char tracking URL (which overflows the DB column).
        if parsed.path.startswith("/y.js"):
            ad_domain = qs.get("ad_domain", [""])[0].strip().strip("/")
            return f"https://{ad_domain}" if ad_domain else ""
    return href


def _should_skip_url(url: str) -> bool:
    host = (urlparse(url).netloc or "").lower().removeprefix("www.")
    return any(host == d or host.endswith("." + d) for d in _SKIP_DOMAINS)


def _strip_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", unescape(text)).strip()


_EMAIL_RE = re.compile(
    r"(?<![\w.+-])([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})(?![\w.\-])"
)
_JUNK_EMAIL_SUFFIXES = (
    "@sentry.io",
    "@example.com",
    "@wixpress.com",
    "@domain.com",
    "@email.com",
    "@yourdomain",
)
_CONTACT_PATHS = ("/contact", "/contactez-nous", "/nous-contacter", "/mentions-legales")


def extract_emails_from_html(html: str) -> list[str]:
    found = []
    seen = set()
    for match in _EMAIL_RE.findall(html or ""):
        email = match.lower().strip().rstrip(".")
        if any(email.endswith(s) for s in _JUNK_EMAIL_SUFFIXES):
            continue
        if email.startswith(("noreply@", "no-reply@", "donotreply@")):
            continue
        if email not in seen:
            seen.add(email)
            found.append(email)
    return found


def fetch_page_text(url: str, *, max_bytes: int = 120_000, timeout: int = 12) -> str:
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": _USER_AGENT, "Accept-Language": "fr-FR,fr;q=0.9"},
            timeout=timeout,
            allow_redirects=True,
        )
        if resp.status_code >= 400:
            return ""
        content = resp.content[:max_bytes]
        charset = resp.encoding or "utf-8"
        return content.decode(charset, errors="ignore")
    except Exception:  # noqa: BLE001
        logger.debug("fetch_page_text failed for %s", url, exc_info=True)
        return ""


def harvest_emails_from_site(url: str) -> list[str]:
    """Try the landing page and common contact paths."""
    emails: list[str] = []
    seen = set()
    base = url.rstrip("/")
    candidates = [base]
    for path in _CONTACT_PATHS:
        candidates.append(base + path)

    for candidate in candidates[:4]:
        html = fetch_page_text(candidate)
        for email in extract_emails_from_html(html):
            if email not in seen:
                seen.add(email)
                emails.append(email)
        if emails:
            break
    return emails
