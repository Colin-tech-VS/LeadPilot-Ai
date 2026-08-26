"""AI copywriting for mailing campaigns, grounded in the real site.

A generic "write me a marketing e-mail" prompt invents the product: prices that
do not exist, features nobody built, promises support has to walk back. So the
model is never asked to imagine PilotCore — it is handed the site's own facts
first:

* ``/llms-full.txt`` — the knowledge base the site already publishes for AI
  assistants: positioning, what each plan does and does not include, coverage,
  legal boundaries. It is maintained as part of the product, so the campaign
  copy stays in step with the site by construction.
* the live ``Offer`` rows — the prices actually displayed on /pro right now,
  read from the database rather than from a prompt written months ago;
* the published pages and recent articles, so a call to action can link to a
  page that exists;
* the trades and cities the campaign is aimed at.

The model then returns a **block document** in the editor's own schema, not raw
HTML. The design lands in the designer where it can be edited by hand, and the
renderer stays the only thing that produces HTML.
"""
from __future__ import annotations

import json
import logging

from flask import current_app

from app.constants.trades import TRADES, trade_label
from app.services import campaign_render, content_ai

logger = logging.getLogger(__name__)

MAX_KNOWLEDGE_CHARS = 6000


class CampaignAIError(Exception):
    pass


def is_available() -> bool:
    return content_ai.is_available()


def _base_url() -> str:
    return str(current_app.config.get("PUBLIC_BASE_URL") or "https://www.pilotcore.fr").rstrip("/")


# --------------------------------------------------------------------------- #
# Site knowledge
# --------------------------------------------------------------------------- #
def site_knowledge() -> dict:
    """Everything the copywriter must know before writing a word."""
    from app.models.blog_post import BlogPost
    from app.models.offer import Offer
    from app.models.site_page import SitePage

    def _safe(fn, default):
        try:
            return fn()
        except Exception:  # noqa: BLE001 — knowledge gathering must never 500
            logger.debug("campaign knowledge piece failed", exc_info=True)
            return default

    def _offers():
        rows = (
            Offer.query.filter(Offer.active.is_(True))
            .order_by(Offer.sort_order.asc())
            .all()
        )
        return [
            {
                "key": o.key,
                "name": o.name,
                "price": o.price,
                "period": o.period,
                "calls": o.calls,
                "badge": o.badge,
                "description": o.description,
                "features": o.feature_list()[:8],
                "featured": bool(o.featured),
            }
            for o in rows
        ]

    def _pages():
        rows = (
            SitePage.query.filter(SitePage.status == "published")
            .order_by(SitePage.updated_at.desc())
            .limit(15)
            .all()
        )
        return [{"title": p.title, "url": f"{_base_url()}/p/{p.slug}"} for p in rows]

    def _articles():
        rows = (
            BlogPost.query.filter(BlogPost.status == "published")
            .order_by(BlogPost.created_at.desc())
            .limit(6)
            .all()
        )
        return [{"title": p.title, "url": f"{_base_url()}/blog/{p.slug}"} for p in rows]

    def _factsheet():
        from app.utils.llm_discovery import render_llms_full_txt

        return render_llms_full_txt()[:MAX_KNOWLEDGE_CHARS]

    base = _base_url()
    return {
        "site": base,
        "brand": "PilotCore",
        "factsheet": _safe(_factsheet, ""),
        "offers": _safe(_offers, []),
        "pages": _safe(_pages, []),
        "articles": _safe(_articles, []),
        "trades": [trade_label(k, "fr") for k in TRADES if k != "autre"],
        "key_links": {
            "inscription": "{{lien_inscription}}",
            "offres_pro": f"{base}/pro",
            "programme_50": f"{base}/50-artisans",
            "annuaire": f"{base}/artisans",
            "contact": f"{base}/contact",
        },
    }


# --------------------------------------------------------------------------- #
# Prompting
# --------------------------------------------------------------------------- #
_BLOCK_SCHEMA = """Chaque bloc est un objet JSON. Types autorisés et champs :
- {"type":"header","title":"PilotCore","tagline":"Invitation","logo":true}
- {"type":"heading","text":"Titre","align":"left"}
- {"type":"text","html":"<p>Paragraphe</p><p>Autre paragraphe</p>"}
- {"type":"list","items":["Point 1","Point 2"],"icon":"✓"}
- {"type":"button","label":"Essayer","url":"{{lien_inscription}}","align":"left"}
- {"type":"offer","name":"Pro","price":"349 €","period":"HT / mois","description":"…","features":["…"],"badge":"","highlight":false,"cta_label":"Voir l'offre","cta_url":"…"}
- {"type":"stats","items":[{"value":"24h/24","label":"Appels pris"},{"value":"14 j","label":"Essai"}]}
- {"type":"quote","text":"…","author":"…"}
- {"type":"divider"}
- {"type":"spacer","height":24}
- {"type":"footer","html":"<p>Petit texte de fin</p>"}"""

_MERGE_TAGS = (
    "{{salutation}} {{prenom}} {{entreprise}} {{ville}} {{metier}} {{email}} {{site}} "
    "{{lien_inscription}} {{lien_desinscription}}"
)


def _system_prompt() -> str:
    return (
        "Tu es le rédacteur e-mail de PilotCore. Tu écris des campagnes B2B en français "
        "à destination d'artisans du bâtiment.\n\n"
        "RÈGLES ABSOLUES :\n"
        "1. Tu ne parles QUE de ce qui figure dans les FAITS fournis. Aucun prix, aucune "
        "fonctionnalité, aucun chiffre, aucun témoignage qui n'y figure pas. Si une info "
        "manque, tu n'en parles pas.\n"
        "2. Vouvoiement, ton direct et concret, phrases courtes. Pas de superlatifs creux "
        "(« révolutionnaire », « incroyable »), pas de majuscules criardes, pas de « !! ».\n"
        "3. Une seule idée par e-mail, un seul appel à l'action principal.\n"
        "4. Personnalise avec les variables disponibles : " + _MERGE_TAGS + ". "
        "N'invente jamais d'autre variable. Pour saluer, utilise {{salutation}} "
        "(qui vaut « Bonjour » quand le prénom est inconnu), jamais « Bonjour {{prenom}} ».\n"
        "5. N'ajoute jamais de bloc de désinscription : il est ajouté automatiquement.\n"
        "6. Longueur cible : 120 à 200 mots de corps de texte.\n\n"
        "Tu réponds UNIQUEMENT en JSON avec les clés :\n"
        '"name" (nom interne court de la campagne), '
        '"subject" (objet, 40-70 caractères, sans emoji sauf demande explicite), '
        '"preheader" (pré-en-tête, 60-100 caractères), '
        '"blocks" (liste de blocs).\n\n' + _BLOCK_SCHEMA
    )


def _facts_payload(knowledge: dict, *, audience: dict | None) -> str:
    payload = {
        "FAITS_SITE": knowledge.get("factsheet", ""),
        "OFFRES_EN_LIGNE": knowledge.get("offers", []),
        "PAGES_PUBLIEES": knowledge.get("pages", []),
        "ARTICLES_RECENTS": knowledge.get("articles", []),
        "LIENS": knowledge.get("key_links", {}),
        "METIERS": knowledge.get("trades", []),
        "AUDIENCE": audience or {},
    }
    return json.dumps(payload, ensure_ascii=False)


def _normalise_blocks(raw) -> list[dict]:
    """Keep only blocks the renderer understands, in the order given."""
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        btype = item.get("type")
        if btype not in campaign_render.BLOCK_TYPES:
            continue
        block = {k: v for k, v in item.items() if k != "id"}
        block["type"] = btype
        block["id"] = campaign_render.new_block_id()
        if btype == "text" and "html" not in block and "text" in block:
            block["html"] = block.pop("text")
        if btype in ("list",) and not isinstance(block.get("items"), list):
            continue
        out.append(block)
    return out


def generate_campaign(
    *,
    brief: str,
    audience: dict | None = None,
    tone: str = "direct",
    goal: str = "inscription",
) -> dict:
    """Write a full campaign (name, subject, preheader, blocks) from a brief."""
    if not is_available():
        raise CampaignAIError("IA indisponible — renseignez MISTRAL_API_KEY.")

    knowledge = site_knowledge()
    user = (
        f"BRIEF : {brief.strip() or 'Présenter PilotCore aux artisans et obtenir une inscription.'}\n"
        f"OBJECTIF : {goal}\n"
        f"TON : {tone}\n\n"
        f"DONNÉES DU SITE (source de vérité) :\n{_facts_payload(knowledge, audience=audience)}"
    )
    raw = content_ai._complete(
        _system_prompt(), user, json_mode=True, max_tokens=2200, temperature=0.55
    )
    try:
        data = content_ai._parse_json_response(raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Campaign AI JSON unparseable: %s", exc)
        raise CampaignAIError("La réponse de l'IA n'était pas exploitable, réessayez.") from exc
    if not isinstance(data, dict):
        raise CampaignAIError("La réponse de l'IA n'était pas exploitable, réessayez.")

    blocks = _normalise_blocks(data.get("blocks"))
    if not blocks:
        raise CampaignAIError("L'IA n'a produit aucun bloc exploitable, réessayez.")

    return {
        "name": (data.get("name") or "Campagne artisans").strip()[:160],
        "subject": (data.get("subject") or "").strip()[:255],
        "preheader": (data.get("preheader") or "").strip()[:255],
        "design": {"settings": dict(campaign_render.DEFAULT_SETTINGS), "blocks": blocks},
    }


def suggest_subjects(*, brief: str, count: int = 5) -> list[str]:
    """Alternative subject lines for the same campaign."""
    if not is_available():
        raise CampaignAIError("IA indisponible — renseignez MISTRAL_API_KEY.")
    count = max(2, min(int(count or 5), 8))
    knowledge = site_knowledge()
    system = (
        "Tu proposes des objets d'e-mail B2B en français pour des artisans du bâtiment. "
        "40 à 70 caractères, concrets, sans emoji, sans promesse absente des faits fournis. "
        'Réponds UNIQUEMENT en JSON : {"subjects": ["…"]}'
    )
    user = (
        f"CAMPAGNE : {brief.strip()}\n"
        f"NOMBRE : {count}\n"
        f"FAITS : {json.dumps({'offres': knowledge.get('offers', []), 'resume': knowledge.get('factsheet', '')[:1500]}, ensure_ascii=False)}"
    )
    raw = content_ai._complete(system, user, json_mode=True, max_tokens=500, temperature=0.8)
    try:
        data = content_ai._parse_json_response(raw)
        subjects = data.get("subjects") if isinstance(data, dict) else None
    except Exception as exc:  # noqa: BLE001
        raise CampaignAIError("Réponse IA inexploitable, réessayez.") from exc
    if not isinstance(subjects, list) or not subjects:
        raise CampaignAIError("Réponse IA inexploitable, réessayez.")
    return [str(s).strip()[:255] for s in subjects if str(s).strip()][:count]


def rewrite_block(*, block: dict, instruction: str) -> dict:
    """Rewrite one block's copy in place — the editor's « améliorer » action."""
    if not is_available():
        raise CampaignAIError("IA indisponible — renseignez MISTRAL_API_KEY.")
    btype = (block or {}).get("type")
    if btype not in campaign_render.BLOCK_TYPES:
        raise CampaignAIError("Bloc inconnu.")

    knowledge = site_knowledge()
    system = (
        "Tu réécris UN bloc d'e-mail marketing B2B en français, pour des artisans. "
        "Tu conserves strictement le type de bloc et la forme des champs reçus. "
        "Tu ne parles que de ce qui figure dans les faits fournis. "
        "Variables autorisées : " + _MERGE_TAGS + ". "
        'Réponds UNIQUEMENT en JSON : {"block": {…}}\n\n' + _BLOCK_SCHEMA
    )
    user = (
        f"CONSIGNE : {instruction.strip() or 'Rendre le texte plus clair et plus concret.'}\n"
        f"BLOC ACTUEL : {json.dumps(block, ensure_ascii=False)}\n"
        f"FAITS : {json.dumps({'offres': knowledge.get('offers', []), 'resume': knowledge.get('factsheet', '')[:2000], 'liens': knowledge.get('key_links', {})}, ensure_ascii=False)}"
    )
    raw = content_ai._complete(system, user, json_mode=True, max_tokens=900, temperature=0.5)
    try:
        data = content_ai._parse_json_response(raw)
        new_block = data.get("block") if isinstance(data, dict) else None
    except Exception as exc:  # noqa: BLE001
        raise CampaignAIError("Réponse IA inexploitable, réessayez.") from exc

    blocks = _normalise_blocks([new_block] if isinstance(new_block, dict) else [])
    if not blocks:
        raise CampaignAIError("Réponse IA inexploitable, réessayez.")
    result = blocks[0]
    result["id"] = block.get("id") or campaign_render.new_block_id()
    if result["type"] != btype:
        raise CampaignAIError("L'IA a changé le type de bloc, réessayez.")
    return result
