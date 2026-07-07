"""B2B artisan prospecting — search, enrich, AI outreach emails."""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone

from flask import current_app

from app.constants.trades import TRADES, trade_label
from app.core.extensions import db
from app.models.outreach_prospect import OutreachProspect, utcnow
from app.services import admin_email, content_ai, prospect_search
from app.services.transactional_email import render_email

logger = logging.getLogger(__name__)

_PHONE_RE = re.compile(r"(?:\+33|0)[1-9](?:[\s.\-]?\d{2}){4}")


class ProspectingError(Exception):
    pass


def _column_limits() -> dict[str, int]:
    """Max length of every bounded ``String`` column on the prospect table."""
    limits = {}
    for col in OutreachProspect.__table__.columns:
        length = getattr(col.type, "length", None)
        if length:
            limits[col.name] = length
    return limits


def _fit_to_columns(**values):
    """Clamp string values to their column length so an over-long field (a long
    URL, a chatty AI ``company_name``) never raises StringDataRightTruncation."""
    limits = _column_limits()
    out = {}
    for key, value in values.items():
        limit = limits.get(key)
        if limit and isinstance(value, str) and len(value) > limit:
            value = value[:limit]
        out[key] = value
    return out


def _coerce_id(prospect_id):
    if isinstance(prospect_id, uuid.UUID):
        return prospect_id
    return uuid.UUID(str(prospect_id))


def _get_prospect(prospect_id) -> OutreachProspect | None:
    return db.session.get(OutreachProspect, _coerce_id(prospect_id))


def list_prospects(*, status: str | None = None, trade_type: str | None = None, limit: int = 200):
    q = OutreachProspect.query.order_by(OutreachProspect.created_at.desc())
    if status:
        q = q.filter(OutreachProspect.status == status)
    if trade_type:
        q = q.filter(OutreachProspect.trade_type == trade_type)
    return q.limit(limit).all()


def prospect_stats() -> dict:
    """Aggregate counts for the admin prospection dashboard."""
    from sqlalchemy import func

    rows = (
        db.session.query(OutreachProspect.status, func.count())
        .group_by(OutreachProspect.status)
        .all()
    )
    by_status = {status: count for status, count in rows}
    total = sum(by_status.values())
    with_email = OutreachProspect.query.filter(
        OutreachProspect.email.isnot(None),
        OutreachProspect.email != "",
    ).count()
    return {
        "total": total,
        "with_email": with_email,
        "ready": by_status.get("ready", 0),
        "contacted": by_status.get("contacted", 0) + by_status.get("replied", 0),
        "converted": by_status.get("converted", 0),
        "by_status": by_status,
    }


OUTREACH_STATUS_LABELS = {
    "new": "Nouveau",
    "ready": "Prêt",
    "contacted": "Contacté",
    "replied": "Répondu",
    "converted": "Converti",
    "unsubscribed": "Désinscrit",
    "skipped": "Ignoré",
}


def _existing_emails() -> set[str]:
    rows = OutreachProspect.query.filter(OutreachProspect.email.isnot(None)).all()
    return {r.email.lower() for r in rows if r.email}


def _build_queries(trade_type: str, city: str) -> list[str]:
    label = trade_label(trade_type, "fr")
    city = city.strip()
    return [
        f'{label} {city} artisan contact email',
        f'{label} {city} entreprise devis',
        f'site:pagesjaunes.fr {label} {city}',
    ]


def _parse_contact_from_text(title: str, snippet: str, page_text: str) -> dict:
    blob = f"{title}\n{snippet}\n{page_text[:4000]}"
    phone = None
    phone_match = _PHONE_RE.search(blob)
    if phone_match:
        phone = re.sub(r"[\s.\-]", "", phone_match.group(0))

    company_name = title.split("|")[0].split("—")[0].split("-")[0].strip()
    company_name = re.sub(r"\s+", " ", company_name)[:255]

    return {"company_name": company_name or None, "phone": phone}


def _enrich_with_ai(
    *,
    title: str,
    snippet: str,
    emails: list[str],
    trade_type: str,
    city: str,
    url: str,
) -> dict:
    if not content_ai.is_available():
        return {
            "first_name": None,
            "last_name": None,
            "company_name": _parse_contact_from_text(title, snippet, "")["company_name"],
            "email": emails[0] if emails else None,
            "phone": _parse_contact_from_text(title, snippet, "")["phone"],
            "email_confidence": "medium" if emails else None,
            "notes": "Extraction sans IA (Mistral indisponible).",
        }

    system = (
        "Tu analyses des résultats web pour identifier un artisan du bâtiment (B2B). "
        "Réponds UNIQUEMENT en JSON avec les clés: "
        '"first_name", "last_name", "company_name", "email", "phone", '
        '"email_confidence" ("high"|"medium"|"low"|null), "notes". '
        "RÈGLES STRICTES: ne devine JAMAIS un e-mail — utilise uniquement un e-mail "
        "présent dans les données fournies. Si aucun e-mail fiable, mets email à null. "
        "Ne devine pas un prénom/nom sans indice clair (signature, nom de dirigeant). "
        "company_name = nom de l'entreprise artisanale."
    )
    user = json.dumps(
        {
            "trade": trade_label(trade_type, "fr"),
            "city": city,
            "title": title,
            "snippet": snippet,
            "url": url,
            "emails_found_on_site": emails,
        },
        ensure_ascii=False,
    )
    try:
        raw = content_ai._complete(system, user, json_mode=True, max_tokens=500, temperature=0.2)
        data = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Prospect AI enrich failed: %s", exc)
        base = _parse_contact_from_text(title, snippet, "")
        return {
            **base,
            "first_name": None,
            "last_name": None,
            "email": emails[0] if emails else None,
            "email_confidence": "medium" if emails else None,
            "notes": "Enrichissement IA partiel.",
        }

    email = data.get("email")
    if email and emails and email.lower() not in {e.lower() for e in emails}:
        email = emails[0]
    if not email and emails:
        email = emails[0]

    return {
        "first_name": (data.get("first_name") or None),
        "last_name": (data.get("last_name") or None),
        "company_name": (data.get("company_name") or _parse_contact_from_text(title, snippet, "")["company_name"]),
        "email": email,
        "phone": data.get("phone") or _parse_contact_from_text(title, snippet, "")["phone"],
        "email_confidence": data.get("email_confidence"),
        "notes": data.get("notes"),
    }


def run_search(
    *,
    trade_type: str,
    city: str,
    max_results: int = 12,
) -> dict:
    """Search the web for artisans and persist new prospects."""
    if trade_type not in TRADES:
        raise ProspectingError("Métier invalide.")
    city = (city or "").strip()
    if not city:
        raise ProspectingError("Indiquez une ville ou un code postal.")

    max_results = max(3, min(int(max_results or 12), 25))
    known_emails = _existing_emails()
    queries = _build_queries(trade_type, city)
    collected: list[dict] = []
    seen_urls: set[str] = set()

    for query in queries:
        if len(collected) >= max_results:
            break
        try:
            hits = prospect_search.web_search(query, max_results=max_results)
        except prospect_search.ProspectSearchError as exc:
            if collected:
                break
            raise ProspectingError(str(exc)) from exc

        for hit in hits:
            url = hit["url"]
            if url in seen_urls:
                continue
            seen_urls.add(url)

            emails = prospect_search.harvest_emails_from_site(url)
            page_text = prospect_search.fetch_page_text(url) if not emails else ""
            enriched = _enrich_with_ai(
                title=hit["title"],
                snippet=hit["snippet"],
                emails=emails,
                trade_type=trade_type,
                city=city,
                url=url,
            )

            email = (enriched.get("email") or "").lower().strip() or None
            if email and email in known_emails:
                continue

            prospect = OutreachProspect(
                **_fit_to_columns(
                    first_name=enriched.get("first_name"),
                    last_name=enriched.get("last_name"),
                    company_name=enriched.get("company_name"),
                    email=email,
                    phone=enriched.get("phone"),
                    trade_type=trade_type,
                    city=city,
                    website_url=url,
                    source_url=url,
                    source="web_search",
                    status="ready" if email else "new",
                    email_confidence=enriched.get("email_confidence"),
                    search_query=query,
                    notes=enriched.get("notes"),
                )
            )
            db.session.add(prospect)
            db.session.flush()
            if email:
                known_emails.add(email)
            collected.append(prospect.to_dict())
            if len(collected) >= max_results:
                break

    db.session.commit()
    with_email = sum(1 for p in collected if p.get("email"))
    return {
        "provider": prospect_search.search_provider(),
        "queries": queries,
        "found": len(collected),
        "with_email": with_email,
        "prospects": collected,
    }


def generate_outreach_email(prospect_id, *, tone: str = "professionnel", angle: str = "") -> OutreachProspect:
    prospect = _get_prospect(prospect_id)
    if not prospect:
        raise ProspectingError("Prospect introuvable.")
    if prospect.opted_out_at:
        raise ProspectingError("Ce prospect s'est désinscrit.")

    if not content_ai.is_available():
        raise ProspectingError("Mistral indisponible — renseignez MISTRAL_API_KEY.")

    base_url = str(current_app.config.get("PUBLIC_BASE_URL") or "https://www.pilotcore.fr").rstrip("/")
    trade = trade_label(prospect.trade_type, "fr")
    name = prospect.display_name()
    city = prospect.city or "votre secteur"

    system = (
        "Tu rédiges un e-mail de prospection B2B court et personnalisé pour PilotCore, "
        "un standardiste téléphonique IA pour artisans (plombiers, électriciens, etc.). "
        "Réponds UNIQUEMENT en JSON: "
        '"subject" (objet, max 70 car.), '
        '"body_plain" (corps en texte, 120-180 mots, tutoiement interdit, vouvoiement), '
        '"body_html" (même contenu en HTML simple: p, strong, ul/li). '
        "Mets en avant: prise d'appels 24/7, qualification des demandes, essai 14 jours. "
        "Ton professionnel, concret, sans promesses mensongères. "
        "Inclus un CTA vers l'inscription. Pas de spam agressif."
    )
    user = (
        f"Prospect: {name}\n"
        f"Entreprise: {prospect.company_name or '—'}\n"
        f"Métier: {trade}\n"
        f"Ville: {city}\n"
        f"Angle / brief: {angle or 'Présenter PilotCore Pro et proposer un essai gratuit.'}\n"
        f"Ton: {tone}\n"
        f"Lien inscription: {base_url}/register\n"
    )
    raw = content_ai._complete(system, user, json_mode=True, max_tokens=900, temperature=0.55)
    try:
        data = content_ai._parse_json_response(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        # The model occasionally wraps JSON in ``` fences or truncates it — never
        # let that surface as a 500. Mirror the other content_ai generators.
        logger.warning("Prospect outreach email JSON unparseable: %s", exc)
        raise ProspectingError("La réponse de l'IA n'était pas exploitable, réessayez.") from exc
    if not isinstance(data, dict):
        raise ProspectingError("La réponse de l'IA n'était pas exploitable, réessayez.")
    prospect.outreach_subject = (data.get("subject") or f"PilotCore — standard IA pour {trade}s").strip()[:255]
    prospect.outreach_body = (data.get("body_plain") or "").strip()
    if prospect.status == "new" and prospect.email:
        prospect.status = "ready"
    prospect.updated_at = utcnow()
    db.session.commit()
    return prospect


def send_outreach_email(prospect_id) -> dict:
    prospect = _get_prospect(prospect_id)
    if not prospect:
        raise ProspectingError("Prospect introuvable.")
    if prospect.opted_out_at:
        raise ProspectingError("Ce prospect s'est désinscrit.")
    if not prospect.email:
        raise ProspectingError("Aucun e-mail pour ce prospect.")
    if not prospect.outreach_subject or not prospect.outreach_body:
        raise ProspectingError("Générez d'abord l'e-mail de prospection.")

    base_url = str(current_app.config.get("PUBLIC_BASE_URL") or "https://www.pilotcore.fr").rstrip("/")
    unsubscribe = f"{base_url}/contact?subject=desinscription-prospection&email={prospect.email}"
    plain = (
        prospect.outreach_body
        + "\n\n—\n"
        + "PilotCore · contact@pilotcore.fr\n"
        + f"Pour ne plus recevoir de messages : {unsubscribe}"
    )
    html = render_email(
        prospect.outreach_subject,
        prospect.outreach_body.replace("\n\n", "</p><p>").replace("\n", "<br>"),
        cta_label="Essayer PilotCore gratuitement",
        cta_url=f"{base_url}/register",
        outro=(
            "PilotCore · contact@pilotcore.fr<br>"
            f'<a href="{unsubscribe}">Se désinscrire de nos messages</a>'
        ),
    )
    row = admin_email.send_email(
        prospect.email,
        prospect.outreach_subject,
        plain,
        is_html=True,
        html_body=html,
    )
    prospect.status = "contacted"
    prospect.last_contacted_at = datetime.now(timezone.utc)
    prospect.updated_at = utcnow()
    db.session.commit()
    return {"prospect": prospect.to_dict(), "email_status": row.status}


def update_status(prospect_id, status: str) -> OutreachProspect:
    if status not in ("new", "ready", "contacted", "replied", "converted", "unsubscribed", "skipped"):
        raise ProspectingError("Statut invalide.")
    prospect = _get_prospect(prospect_id)
    if not prospect:
        raise ProspectingError("Prospect introuvable.")
    prospect.status = status
    if status == "unsubscribed":
        prospect.opted_out_at = utcnow()
    prospect.updated_at = utcnow()
    db.session.commit()
    return prospect


def delete_prospect(prospect_id) -> None:
    prospect = _get_prospect(prospect_id)
    if not prospect:
        raise ProspectingError("Prospect introuvable.")
    db.session.delete(prospect)
    db.session.commit()
