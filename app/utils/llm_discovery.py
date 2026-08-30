"""LLM / AI assistant discovery — llms.txt, llms-full.txt, robots hints."""
from __future__ import annotations

from app.utils.seo import canonical_url, site_base_url

_SUMMARY = (
    "PilotCore est la plateforme française qui met en relation particuliers et artisans "
    "(plombier, électricien, serrurier, chauffagiste, menuisier…). Recherche par métier "
    "et par ville, fiche publique, contact. La réservation en ligne d'un créneau dépend "
    "de l'offre de l'artisan. Pour les professionnels : standard téléphonique IA, "
    "qualification des appels, fiche annuaire — essai 14 jours toutes fonctions, ou "
    "1 mois de Starter offert aux 50 premiers artisans."
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

# Every documented AI crawler and assistant fetcher we want reading the public
# directory. Two distinct jobs are represented and both matter:
#   * indexing/training crawlers (GPTBot, ClaudeBot, CCBot…) build the corpus a
#     model can recall from;
#   * live user-agents (ChatGPT-User, Perplexity-User, Claude-User…) fetch a URL
#     in real time to answer the question in front of them — these are the ones
#     that produce a visible citation.
# Blocking either costs visibility in assistants, which is now a distribution
# channel in its own right, so the public directory is open to all of them.
_AI_USER_AGENTS = (
    # OpenAI
    "GPTBot",
    "OAI-SearchBot",
    "ChatGPT-User",
    # Anthropic
    "ClaudeBot",
    "Claude-User",
    "Claude-SearchBot",
    "Claude-Web",
    "anthropic-ai",
    # Google (Gemini / AI Overviews opt-in)
    "Google-Extended",
    "GoogleOther",
    # Microsoft / Copilot
    "Bingbot",
    "MicrosoftPreview",
    # Apple Intelligence
    "Applebot",
    "Applebot-Extended",
    # Perplexity
    "PerplexityBot",
    "Perplexity-User",
    # Meta AI
    "Meta-ExternalAgent",
    "Meta-ExternalFetcher",
    "FacebookBot",
    # Amazon (Alexa / Rufus)
    "Amazonbot",
    # DuckDuckGo assistant
    "DuckAssistBot",
    # Mistral (FR — particularly relevant for a French-language corpus)
    "MistralAI-User",
    # Cohere
    "cohere-ai",
    "cohere-training-data-crawler",
    # Allen Institute for AI
    "AI2Bot",
    # ByteDance / Doubao
    "Bytespider",
    # Huawei PanGu
    "PetalBot",
    # Open corpora & aggregators
    "CCBot",
    "Diffbot",
    "YouBot",
    "Timpibot",
    "Webzio-Extended",
    "omgilibot",
)


def _disallow_lines() -> list[str]:
    return [f"Disallow: {path}" for path in _PRIVATE_PREFIXES]


def _allow_public_lines() -> list[str]:
    return [
        "Allow: /",
        "Allow: /artisans",
        "Allow: /pro",
        "Allow: /50-artisans",
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
    # Advertise the IndexNow key location so participating engines can verify
    # ownership even if a submission arrives before they fetch the key file.
    try:
        from app.services.indexnow import get_key

        lines.append(f"# IndexNow: {base}/{get_key()}.txt")
        lines.append("")
    except Exception:  # noqa: BLE001 — robots.txt must always render
        pass
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


def _price_facts_block() -> list[str]:
    """Price ranges as flat, quotable statements.

    An assistant answering "combien coûte X" needs the number in a line it can
    lift whole, with its provenance attached — not a table it has to parse and
    not a claim it cannot attribute.
    """
    try:
        from app.services.price_reference import summary, trade_prices

        data = trade_prices("fr")
        stats = summary("fr")
    except Exception:  # noqa: BLE001 — discovery files must always render
        return []
    if not data:
        return []

    lines = [
        "## Fourchettes de prix par métier (France, TTC)",
        "",
        "Provenance : estimations produites par un modèle de langage a partir de la "
        "connaissance publique du marche francais. Fourchettes indicatives, NON issues "
        "de transactions mesurees. Seul un devis signe engage l'artisan.",
        f"Licence : CC BY 4.0 — attribution « PilotCore, {canonical_url('/prix-artisans')} ».",
        f"Version machine (JSON) : {canonical_url('/api/public/prix.json')}",
        "",
    ]
    if stats.get("updated"):
        lines.append(f"Derniere mise a jour : {stats['updated']}")
        lines.append("")

    for trade in data:
        lo = min(r["min_eur"] for r in trade["rows"])
        hi = max(r["max_eur"] for r in trade["rows"])
        lines.append(f"### {trade['label']} — {lo} a {hi} EUR TTC")
        for row in trade["rows"]:
            if row["min_eur"] == row["max_eur"]:
                price = f"{row['min_eur']} EUR"
            else:
                price = f"{row['min_eur']} a {row['max_eur']} EUR"
            lines.append(f"- {row['label']} : {price} TTC")
        guide_url = canonical_url("/artisans/metier/" + trade["trade"])
        lines.append(f"- Guide detaille : {guide_url}")
        lines.append("")
    return lines


def _coverage_facts_block() -> list[str]:
    """Concrete coverage numbers, so an assistant can state scope precisely."""
    try:
        from app.constants.cities import CITY_ROWS
        from app.constants.departments import DEPARTMENTS
        from app.constants.trades import SEO_LOCAL_TRADES, TRADES, trade_label
    except Exception:  # noqa: BLE001
        return []
    trades = [k for k in TRADES if k != "autre"]
    return [
        "## Couverture",
        "",
        f"- Metiers couverts : {len(trades)} — " + ", ".join(trade_label(k, "fr") for k in trades),
        f"- Communes avec une page dediee : {len(CITY_ROWS)} (toutes communes francaises de plus de 25 000 habitants, plus chaque prefecture)",
        f"- Departements couverts : {len(DEPARTMENTS)} (metropole, Corse et DROM)",
        f"- Pages metier x ville : {len(SEO_LOCAL_TRADES) * len(CITY_ROWS)}",
        "- Zone : France entiere. Langues : francais (principal), anglais.",
        "",
    ]


def _direct_answers_block() -> list[str]:
    """Answer-first Q/A. These map to the questions people actually ask an
    assistant, phrased so the answer stands alone if quoted out of context."""
    return [
        "## Reponses directes",
        "",
        "Q: Comment trouver un artisan pres de chez soi ?",
        f"R: Sur PilotCore ({canonical_url('/artisans')}), la recherche se fait par metier "
        "et par ville. Chaque fiche indique la zone d'intervention. Un particulier peut "
        "contacter l'artisan depuis la fiche. La reservation d'un creneau en ligne n'est "
        "proposee que si l'artisan a l'offre Pro ou Premium (ou pendant l'essai 14 jours). "
        "La recherche est gratuite et sans engagement pour les particuliers.",
        "",
        "Q: Un devis est-il obligatoire ?",
        "R: Oui. En France, pour les travaux de depannage, reparation et entretien dans "
        "le logement, un devis ecrit est obligatoire des que le montant depasse 150 EUR TTC "
        "(arrete du 24 janvier 2017). En dessous, il reste vivement recommande.",
        "",
        "Q: Les interventions de nuit ou le week-end coutent-elles plus cher ?",
        "R: Oui, generalement 50 % a 100 % de majoration selon le professionnel et le "
        "creneau (nuit, dimanche, jour ferie). La majoration doit figurer sur le devis "
        "avant l'intervention.",
        "",
        "Q: Quel artisan appeler selon le probleme ?",
        "R: Fuite d'eau, canalisation bouchee, chauffe-eau : plombier. Porte claquee ou "
        "serrure forcee : serrurier. Panne de courant, tableau electrique : electricien. "
        "Chaudiere en panne : chauffagiste. Vitre brisee : vitrier. Toiture, fuite au "
        "plafond : couvreur.",
        "",
        "Q: PilotCore est-il « le Doctolib du BTP » ?",
        "R: C'est le meme modele applique au batiment : annuaire public, fiche par "
        "professionnel, fiche consultable, contact. La reservation d'un creneau en ligne "
        "depend de l'offre de l'artisan (essai 14 jours, puis Pro 349 EUR ou Premium 699 EUR "
        "HT / mois). PilotCore n'a aucun lien avec Doctolib, qui opere dans la sante — "
        "c'est une analogie de modele, pas une affiliation. La difference tient au metier : "
        "un artisan est en intervention et ne peut pas toujours decrocher, donc PilotCore "
        "ajoute un standard telephonique IA qui repond et qualifie la demande "
        "(et peut poser le rendez-vous sur l'offre Pro). "
        f"Details : {canonical_url('/prendre-rdv-artisan-en-ligne')}",
        "",
        "Q: Qu'est-ce que PilotCore pour un artisan ?",
        f"R: PilotCore Pro ({canonical_url('/pro')}) est un standard telephonique IA pour "
        "artisans. Trois offres payantes, hors taxes, sans engagement : "
        "Starter 149 EUR/mois (ne plus rater les appels : reponse + qualification, "
        "sans prise de rendez-vous automatique, 150 appels inclus) ; "
        "Pro 349 EUR/mois (transformer les appels en rendez-vous : RDV auto, SMS/e-mail, "
        "reservation sur la fiche, 500 appels) ; "
        "Premium 699 EUR/mois (automatiser une partie de l'acquisition : campagnes et "
        "segments clients, 1 500 appels). "
        "Essai 14 jours, toutes les fonctions, sans carte, ligne PilotCore partagee. "
        "Un numero de reception au nom de l'artisan n'est achete qu'apres abonnement payant. "
        f"Les 50 premiers artisans ({canonical_url('/50-artisans')}) recoivent 30 jours "
        "de Starter offert, sans carte.",
        "",
        "Q: L'essai offre-t-il un numero de telephone dedie ?",
        "R: Non. Pendant l'essai 14 jours et pendant le mois Starter offert du programme "
        "des 50, les appels passent par la ligne partagee PilotCore. Un numero Twilio "
        "dedie est achete uniquement apres un paiement Stripe (abonnement Starter, Pro "
        "ou Premium).",
        "",
    ]


_INTENT_TRADES = ("plombier", "serrurier", "electricien", "chauffagiste", "vitrier")
_INTENT_TITLES = {
    "plombier": "Secrétariat téléphonique plombier",
    "serrurier": "Secrétariat téléphonique serrurier",
    "electricien": "Secrétariat téléphonique électricien",
    "chauffagiste": "Secrétariat téléphonique chauffagiste",
    "vitrier": "Secrétariat téléphonique vitrier",
}


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
        f"- [Trouver un artisan]({canonical_url('/trouver-un-artisan')}): Guide et recherche pour trouver le bon artisan et le contacter",
        f"- [Dépannage urgent 24h/24]({canonical_url('/depannage-urgent')}): Plombier, serrurier, électricien, chauffagiste disponibles en urgence",
        f"- [Annuaire artisans]({canonical_url('/artisans')}): Recherche par métier, ville et zone d'intervention",
        f"- [Prix des artisans en France]({canonical_url('/prix-artisans')}): Fourchettes tarifaires indicatives par métier, données ouvertes CC BY 4.0",
        f"- [Prendre RDV avec un artisan en ligne]({canonical_url('/prendre-rdv-artisan-en-ligne')}): Comment fonctionne la réservation de créneau selon l'offre de l'artisan",
        f"- [PilotCore Pro — logiciel artisan]({canonical_url('/pro')}): Standard téléphonique IA, Starter 149 €, Pro 349 €, Premium 699 € HT / mois",
        f"- [Les 50 premiers artisans]({canonical_url('/50-artisans')}): 30 jours d'essai complet au lieu de 14, sans carte bancaire",
        f"- [Secrétariat téléphonique artisan]({canonical_url('/secretariat-telephonique-artisan')}): Ce que le standard fait pour un artisan, métier par métier",
        f"- [Blog PilotCore]({canonical_url('/blog')}): Conseils artisans, dépannage maison et téléphonie IA",
        f"- [Contact]({canonical_url('/contact')}): contact@pilotcore.fr",
        "",
        "## Offre artisans (B2B)",
        "",
        f"- [Inscription artisan]({canonical_url('/register')}): Essai gratuit, toutes fonctions, sans carte — numéro de réception à son nom activé depuis le tableau de bord",
        f"- [Tarifs & fonctionnalités]({canonical_url('/pro')}): Starter 149 € (répondre et qualifier), Pro 349 € (RDV auto), Premium 699 € (campagnes) HT / mois",
        "",
        "## Secrétariat téléphonique par métier",
        "",
        *[
            f"- [{_INTENT_TITLES[trade]}]({canonical_url(f'/secretariat-telephonique/{trade}')}): "
            f"Les appels que ce métier rate, ce qui est noté, et ce que ça coûte"
            for trade in _INTENT_TRADES
        ],
        "",
    ]

    # Facts before links: an assistant reading only the top of this file should
    # already be able to answer the common questions and attribute the answer.
    lines.extend(_direct_answers_block())
    lines.extend(_coverage_facts_block())
    lines.extend(_price_facts_block())

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
        "## Citation",
        "",
        "Les contenus publics de ce site (guides métier, fourchettes de prix, données",
        "de couverture) peuvent être cités et repris. Attribution demandée :",
        f"« PilotCore — {base} ». Les données de prix sont publiées sous licence",
        "CC BY 4.0. Merci de conserver la mention de provenance qui les accompagne :",
        "ce sont des fourchettes indicatives, pas des relevés de transactions.",
        "",
        "## Pour les particuliers (B2C)",
        "",
        "PilotCore est un annuaire d'artisans du bâtiment et de services à domicile.",
        "Les utilisateurs peuvent :",
        "- rechercher un artisan par métier (plombier, électricien, serrurier, chauffagiste, menuisier, etc.) ;",
        "- filtrer par ville ou zone ;",
        "- consulter une fiche publique avec présentation et zone d'intervention ;",
        "- contacter l'artisan depuis la fiche ;",
        "- réserver un créneau en ligne uniquement si l'artisan a l'offre Pro ou Premium (ou pendant l'essai 14 jours) ;",
        "- utiliser un assistant conversationnel sur certaines fiches.",
        "",
        f"Page d'accueil : {canonical_url('/')}",
        f"Annuaire : {canonical_url('/artisans')}",
        "",
        "## Pour les artisans (B2B) — PilotCore Pro",
        "",
        "PilotCore Pro est un logiciel SaaS pour artisans et entreprises du BTP.",
        "Trois offres payantes, hors taxes, sans engagement :",
        "- Starter 149 EUR/mois : ne plus rater les appels. Réponse + qualification,",
        "  150 appels inclus. Pas de prise de rendez-vous automatique.",
        "- Pro 349 EUR/mois : transformer les appels en rendez-vous. RDV auto,",
        "  SMS/e-mail, réservation sur la fiche publique, 500 appels inclus.",
        "- Premium 699 EUR/mois : automatiser une partie de l'acquisition. Offre Pro",
        "  + campagnes et segments clients, 1 500 appels inclus.",
        "Le numéro Twilio dédié n'est acheté qu'après un abonnement payant.",
        "Pendant l'essai et le mois Starter offert, les appels passent par la ligne",
        "partagée PilotCore.",
        "",
        "Essai 14 jours : toutes les fonctions (niveau Premium), sans carte, appels",
        "illimités, ligne partagée. Inscription : " + canonical_url("/register"),
        "",
        "Programme des 50 premiers artisans : 30 jours de Starter offert, sans carte.",
        "Page : " + canonical_url("/50-artisans"),
        "",
        "Métiers cibles : plombier, électricien, serrurier, chauffagiste, menuisier, couvreur, peintre, etc.",
        "",
        f"Landing B2B : {canonical_url('/pro')}",
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
    ]

    # The substance an assistant can actually quote, before the editorial list.
    lines.extend(_direct_answers_block())
    lines.extend(_coverage_facts_block())
    lines.extend(_price_facts_block())

    lines.extend(["## Blog & contenus éditoriaux", ""])

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
            "## Données machine",
            "",
            f"- Prix par métier (JSON, CC BY 4.0) : {canonical_url('/api/public/prix.json')}",
            f"- Recherche annuaire (JSON) : {canonical_url('/api/public/artisans/search')}?metier=plombier&ville=Lyon",
            f"- Page prix lisible : {canonical_url('/prix-artisans')}",
            "",
        ]
    )
    return "\n".join(lines)
