"""LLM / AI assistant discovery — llms.txt, llms-full.txt, robots hints."""
from __future__ import annotations

from app.utils.seo import canonical_url, site_base_url

_SUMMARY = (
    "PilotCore est la plateforme française qui met en relation particuliers et artisans "
    "(plombier, électricien, serrurier, chauffagiste, menuisier…) avec prise de rendez-vous "
    "en ligne 24h/24. Pour les professionnels : standard téléphonique IA, qualification des "
    "appels, fiche publique annuaire et gestion des demandes — essai gratuit 14 jours."
)

_PRIVATE_PREFIXES = (
    "/admin",
    "/dashboard",
    "/leads",
    "/appointments",
    "/settings",
    "/test-call",
    "/chatbot",
    "/chat/",
    "/client/",
    "/billing",
    "/login",
    "/register",
    "/reset-password",
    "/forgot-password",
)

_AI_USER_AGENTS = (
    "GPTBot",
    "OAI-SearchBot",
    "ChatGPT-User",
    "ClaudeBot",
    "Claude-Web",
    "anthropic-ai",
    "Claude-SearchBot",
    "PerplexityBot",
    "Perplexity-User",
    "Google-Extended",
    "Applebot-Extended",
    "cohere-ai",
    "Meta-ExternalAgent",
    "FacebookBot",
    "Bytespider",
    "CCBot",
    "Diffbot",
    "YouBot",
    "MistralAI-User",
)


def _disallow_lines() -> list[str]:
    return [f"Disallow: {path}" for path in _PRIVATE_PREFIXES]


def _allow_public_lines() -> list[str]:
    return [
        "Allow: /",
        "Allow: /artisans",
        "Allow: /pro",
        "Allow: /blog",
        "Allow: /contact",
        "Allow: /p/",
        "Allow: /media/social/",
        "Allow: /llms.txt",
        "Allow: /llms-full.txt",
    ]


def render_robots_txt() -> str:
    """robots.txt — allow AI search/training crawlers on public pages."""
    base = site_base_url()
    lines: list[str] = [
        "# PilotCore — public content welcome for search engines and AI assistants",
        f"# LLM curated index: {base}/llms.txt",
        f"# Full knowledge base: {base}/llms-full.txt",
        "",
    ]

    for agent in _AI_USER_AGENTS:
        lines.append(f"User-agent: {agent}")
        lines.extend(_allow_public_lines())
        lines.extend(_disallow_lines())
        lines.append("")

    lines.append("User-agent: *")
    lines.extend(_allow_public_lines())
    lines.extend(_disallow_lines())
    lines.append(f"Sitemap: {base}/sitemap.xml")
    lines.append("")
    return "\n".join(lines)


def _published_blog_posts(limit: int = 15):
    try:
        from app.models.blog_post import BlogPost

        return (
            BlogPost.query.filter_by(status="published")
            .order_by(BlogPost.published_at.desc().nullslast(), BlogPost.updated_at.desc())
            .limit(limit)
            .all()
        )
    except Exception:
        return []


def render_llms_txt() -> str:
    """Curated Markdown index at /llms.txt (llmstxt.org spec)."""
    base = site_base_url()
    lines = [
        "# PilotCore",
        "",
        f"> {_SUMMARY}",
        "",
        "## Pages principales",
        "",
        f"- [Accueil particuliers]({canonical_url('/')}): Trouver un artisan de confiance près de chez vous",
        f"- [Annuaire artisans]({canonical_url('/artisans')}): Recherche par métier, ville et disponibilités",
        f"- [PilotCore Pro — logiciel artisan]({canonical_url('/pro')}): Standard téléphonique IA et réception d'appels 24h/24",
        f"- [Blog PilotCore]({canonical_url('/blog')}): Conseils artisans, dépannage maison et téléphonie IA",
        f"- [Contact]({canonical_url('/contact')}): contact@pilotcore.fr",
        "",
        "## Offre artisans (B2B)",
        "",
        f"- [Inscription artisan]({canonical_url('/register')}): Essai gratuit 14 jours, numéro IA dédié",
        f"- [Tarifs & fonctionnalités]({canonical_url('/pro')}): CRM léger, RDV en ligne, fiche publique annuaire",
        "",
    ]

    posts = _published_blog_posts(12)
    if posts:
        lines.append("## Articles de blog (SEO)")
        lines.append("")
        for post in posts:
            desc = (post.excerpt or post.meta_description or post.title or "")[:140]
            lines.append(f"- [{post.title}]({canonical_url(f'/blog/{post.slug}')}): {desc}")
        lines.append("")

    lines.extend(
        [
            "## Optional",
            "",
            f"- [Mentions légales]({canonical_url('/mentions-legales')})",
            f"- [Politique de confidentialité]({canonical_url('/confidentialite')})",
            f"- [CGU]({canonical_url('/cgu')})",
            f"- [Suppression des données Meta]({canonical_url('/suppression-donnees')})",
            f"- [Base de connaissances complète]({canonical_url('/llms-full.txt')})",
            "",
        ]
    )
    return "\n".join(lines)


def render_llms_full_txt() -> str:
    """Extended plain-text factsheet for RAG / AI assistants."""
    base = site_base_url()
    lines = [
        "# PilotCore — Base de connaissances (AI / LLM)",
        "",
        _SUMMARY,
        "",
        "## Identité",
        "",
        "- Nom : PilotCore (PilotCore Pro pour l'offre artisans)",
        f"- Site : {base}",
        "- Email : contact@pilotcore.fr",
        "- Pays : France",
        "- Langues : français (principal), anglais",
        "",
        "## Pour les particuliers (B2C)",
        "",
        "PilotCore est un annuaire d'artisans du bâtiment et de services à domicile.",
        "Les utilisateurs peuvent :",
        "- rechercher un artisan par métier (plombier, électricien, serrurier, chauffagiste, menuisier, etc.) ;",
        "- filtrer par ville ou zone ;",
        "- consulter une fiche publique avec présentation et zone d'intervention ;",
        "- prendre rendez-vous en ligne 24h/24 ;",
        "- utiliser un assistant conversationnel sur certaines fiches.",
        "",
        f"Page d'accueil : {canonical_url('/')}",
        f"Annuaire : {canonical_url('/artisans')}",
        "",
        "## Pour les artisans (B2B) — PilotCore Pro",
        "",
        "PilotCore Pro est un logiciel SaaS pour artisans et entreprises du BTP :",
        "- standard téléphonique IA (réceptionniste vocal) disponible 24h/24 ;",
        "- qualification automatique des appels et des demandes ;",
        "- prise de rendez-vous et gestion des leads ;",
        "- fiche publique dans l'annuaire PilotCore ;",
        "- devis et suivi client ;",
        "- essai gratuit 14 jours.",
        "",
        "Métiers cibles : plombier, électricien, serrurier, chauffagiste, menuisier, couvreur, peintre, etc.",
        "",
        f"Landing B2B : {canonical_url('/pro')}",
        f"Inscription : {canonical_url('/register')}",
        "",
        "## Mots-clés & intentions de recherche",
        "",
        "- trouver un plombier / électricien / serrurier près de chez moi",
        "- dépannage urgent plomberie électricité",
        "- RDV artisan en ligne",
        "- standard téléphonique IA artisan",
        "- ne plus rater d'appels en intervention",
        "- réceptionniste automatique BTP",
        "- logiciel gestion artisan",
        "",
        "## Blog & contenus éditoriaux",
        "",
    ]

    posts = _published_blog_posts(25)
    if posts:
        for post in posts:
            lines.append(f"### {post.title}")
            lines.append(f"URL : {canonical_url(f'/blog/{post.slug}')}")
            if post.excerpt or post.meta_description:
                lines.append(post.excerpt or post.meta_description or "")
            if post.meta_keywords:
                lines.append(f"Mots-clés : {post.meta_keywords}")
            lines.append("")
    else:
        lines.append(f"Index blog : {canonical_url('/blog')}")
        lines.append("")

    lines.extend(
        [
            "## Pages légales",
            "",
            f"- Mentions légales : {canonical_url('/mentions-legales')}",
            f"- Confidentialité : {canonical_url('/confidentialite')}",
            f"- CGU : {canonical_url('/cgu')}",
            "",
            "## Index court",
            "",
            f"Fichier llms.txt : {canonical_url('/llms.txt')}",
            f"Sitemap XML : {canonical_url('/sitemap.xml')}",
            "",
        ]
    )
    return "\n".join(lines)
