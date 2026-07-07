"""Canonical public URLs and SEO helpers."""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from flask import current_app, request


def site_base_url() -> str:
    """Prefer PUBLIC_BASE_URL in prod; fall back to the current request host."""
    base = (current_app.config.get("PUBLIC_BASE_URL") or "").strip()
    if base:
        return base.rstrip("/")
    return request.url_root.rstrip("/")


def canonical_url(path: str = "/") -> str:
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{site_base_url()}{path}"


def hreflang_alternates(path: str = "/") -> list[tuple[str, str]]:
    """Return (hreflang, absolute URL) pairs for FR / EN / x-default."""
    base = canonical_url(path)
    return [
        ("fr", base),
        ("en", f"{base}?lang=en"),
        ("x-default", base),
    ]


def profile_keywords(trade_label: str, city: str, postal_code: str | None, lang: str = "fr") -> str:
    city = (city or "").strip()
    postal = (postal_code or "").strip()
    if lang == "en":
        parts = [trade_label, city, postal, "tradesperson", "emergency repair", "book online"]
    else:
        parts = [trade_label, city, postal, "artisan", "dépannage", "RDV en ligne", f"{trade_label} {city}"]
    return ", ".join(p for p in parts if p)


def format_lastmod(value: date | datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    return value.isoformat()


def logo_url() -> str:
    return f"{site_base_url()}/static/images/logo.svg"


def og_locale(lang: str = "fr") -> str:
    return "fr_FR" if lang == "fr" else "en_GB"


def json_ld_script(data: dict[str, Any] | list[Any]) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def organization_json_ld(lang: str, description: str) -> dict[str, Any]:
    knows = (
        [
            "standard téléphonique IA",
            "annuaire artisans France",
            "prise de rendez-vous en ligne",
            "plombier électricien serrurier chauffagiste",
            "réceptionniste vocal artisan",
            "dépannage urgence domicile",
        ]
        if lang == "fr"
        else [
            "AI phone system",
            "tradesperson directory France",
            "online booking",
            "plumber electrician locksmith",
            "voice receptionist for trades",
            "home emergency repair",
        ]
    )
    return {
        "@context": "https://schema.org",
        "@type": "Organization",
        "@id": f"{site_base_url()}/#organization",
        "name": "PilotCore",
        "alternateName": ["PilotCore Pro", "PilotCore Annuaire"],
        "url": site_base_url(),
        "logo": logo_url(),
        "description": description,
        "email": "contact@pilotcore.fr",
        "slogan": (
            "Ne ratez plus aucun appel — trouvez le bon artisan."
            if lang == "fr"
            else "Never miss a call — find the right tradesperson."
        ),
        "knowsAbout": knows,
        "contactPoint": {
            "@type": "ContactPoint",
            "contactType": "customer support",
            "email": "contact@pilotcore.fr",
            "availableLanguage": ["French", "English"],
            "areaServed": "FR",
        },
        "areaServed": {"@type": "Country", "name": "France"},
        "sameAs": [],
    }


def global_site_json_ld(lang: str = "fr") -> dict[str, Any]:
    """WebSite + Organization graph for all public pages (AI/search rich results)."""
    from app.utils.i18n import translate

    desc = translate("client.meta_description", lang)
    org = organization_json_ld(lang, desc)
    website = {
        "@type": "WebSite",
        "@id": f"{site_base_url()}/#website",
        "name": "PilotCore",
        "alternateName": "PilotCore — annuaire artisans & standard IA",
        "url": site_base_url(),
        "description": desc,
        "publisher": {"@id": org["@id"]},
        "inLanguage": "fr-FR" if lang == "fr" else "en-GB",
        "potentialAction": {
            "@type": "SearchAction",
            "target": {
                "@type": "EntryPoint",
                "urlTemplate": f"{canonical_url('/artisans')}?q={{search_term_string}}",
            },
            "query-input": "required name=search_term_string",
        },
    }
    return {"@context": "https://schema.org", "@graph": [org, website]}


def global_site_json_ld_script(lang: str = "fr") -> str:
    return json_ld_script(global_site_json_ld(lang))


def breadcrumb_json_ld(items: list[tuple[str, str]]) -> dict[str, Any]:
    """``items`` = list of (label, path) ending with the current page."""
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": idx + 1,
                "name": label,
                "item": canonical_url(path),
            }
            for idx, (label, path) in enumerate(items)
        ],
    }


def blog_index_json_ld(posts: list, lang: str = "fr") -> dict[str, Any]:
    desc = (
        "Conseils artisans, dépannage maison et téléphonie IA — le blog PilotCore."
        if lang == "fr"
        else "Trades tips, home repairs and AI phone systems — the PilotCore blog."
    )
    graph: list[Any] = [
        {
            "@type": "Blog",
            "@id": f"{canonical_url('/blog')}#blog",
            "name": "Blog PilotCore",
            "description": desc,
            "url": canonical_url("/blog"),
            "publisher": {"@id": f"{site_base_url()}/#organization"},
        },
        {**organization_json_ld(lang, desc), "@id": f"{site_base_url()}/#organization"},
    ]
    for post in posts[:12]:
        graph.append(
            {
                "@type": "BlogPosting",
                "headline": post.title,
                "url": canonical_url(f"/blog/{post.slug}"),
                "datePublished": _iso_dt(post.published_at or post.created_at),
                "description": (post.excerpt or post.meta_description or "")[:300],
            }
        )
    return {"@context": "https://schema.org", "@graph": graph}


def blog_posting_json_ld(post, *, lang: str = "fr") -> dict[str, Any]:
    category_name = post.category.name if post.category else "Blog"
    description = (post.meta_description or post.excerpt or post.title or "")[:300]
    crumbs: list[tuple[str, str]] = [
        ("Accueil" if lang == "fr" else "Home", "/"),
        ("Blog", "/blog"),
    ]
    if post.category:
        crumbs.append((category_name, f"/blog/categorie/{post.category.slug}"))
    crumbs.append((post.title, f"/blog/{post.slug}"))
    graph: list[Any] = [
        breadcrumb_json_ld(crumbs),
        {
            "@type": "BlogPosting",
            "@id": f"{canonical_url(f'/blog/{post.slug}')}#article",
            "headline": post.title,
            "description": description,
            "image": logo_url(),
            "datePublished": _iso_dt(post.published_at or post.created_at),
            "dateModified": _iso_dt(post.updated_at),
            "author": {"@type": "Organization", "name": "PilotCore", "url": site_base_url()},
            "publisher": organization_json_ld(lang, description),
            "mainEntityOfPage": canonical_url(f"/blog/{post.slug}"),
            "articleSection": category_name,
            "inLanguage": "fr-FR" if lang == "fr" else "en-GB",
            "wordCount": _estimate_words(post.body_html or ""),
        },
    ]
    faq = post.get_faq() if hasattr(post, "get_faq") else []
    if faq:
        graph.append(
            {
                "@type": "FAQPage",
                "mainEntity": [
                    {
                        "@type": "Question",
                        "name": item["question"],
                        "acceptedAnswer": {
                            "@type": "Answer",
                            "text": (item.get("answer") or "").strip(),
                        },
                    }
                    for item in faq
                    if (item.get("question") or "").strip()
                ],
            }
        )
    return {"@context": "https://schema.org", "@graph": graph}


def _iso_dt(value: datetime | date | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(microsecond=0).isoformat()
    return value.isoformat()


def _estimate_words(html: str) -> int:
    import re

    text = re.sub(r"<[^>]+>", " ", html or "")
    return len([w for w in text.split() if w.strip()])
