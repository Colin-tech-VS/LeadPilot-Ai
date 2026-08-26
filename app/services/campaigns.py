"""Mailing campaigns: audience, sending, reporting.

The send is deliberately *batched* rather than one long request. Each message
opens an SMTP connection that takes seconds, and the host caps hourly volume;
a 200-recipient loop inside one request would hit the gunicorn timeout long
before it hit the mail server. So ``send_batch`` sends a bounded slice and
reports what is left, and the caller — the admin UI or the cron worker — comes
back for the next slice. That also makes an interrupted send resumable: the
recipient rows carry the state, not the request.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from flask import current_app
from sqlalchemy import func

from app.core.extensions import db
from app.models.email_campaign import (
    CAMPAIGN_STATUSES,
    R_FAILED,
    R_PENDING,
    R_SENT,
    R_SKIPPED,
    R_UNSUBSCRIBED,
    STATUS_DRAFT,
    STATUS_PAUSED,
    STATUS_SCHEDULED,
    STATUS_SENDING,
    STATUS_SENT,
    CampaignRecipient,
    EmailCampaign,
    utcnow,
)
from app.models.email_message import (
    STATUS_SENT as MSG_SENT,
    STATUS_SIMULATED as MSG_SIMULATED,
    EmailMessage,
)
from app.models.outreach_prospect import OutreachProspect
from app.services import admin_email, campaign_render
from app.services.email_tracking import format_rate
from app.services.email_validation import check_recipient

logger = logging.getLogger(__name__)

DEFAULT_BATCH = 25
MAX_BATCH = 100


class CampaignError(Exception):
    """Raised for anything the admin should read as a sentence, not a 500."""


def _base_url() -> str:
    return str(current_app.config.get("PUBLIC_BASE_URL") or "https://www.pilotcore.fr").rstrip("/")


def _coerce_id(value):
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def get_campaign(campaign_id) -> EmailCampaign:
    try:
        campaign = db.session.get(EmailCampaign, _coerce_id(campaign_id))
    except (ValueError, AttributeError, TypeError) as exc:
        raise CampaignError("Campagne introuvable.") from exc
    if not campaign:
        raise CampaignError("Campagne introuvable.")
    return campaign


def list_campaigns(*, status: str | None = None, limit: int = 100) -> list[EmailCampaign]:
    q = EmailCampaign.query.order_by(EmailCampaign.created_at.desc())
    if status:
        q = q.filter(EmailCampaign.status == status)
    return q.limit(limit).all()


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #
def create_campaign(*, name: str = "", template: str = "offre", subject: str = "") -> EmailCampaign:
    design = campaign_render.default_template(template) if template != "blank" else campaign_render.blank_design()
    campaign = EmailCampaign(
        name=(name or "").strip()[:160] or "Nouvelle campagne",
        subject=(subject or "").strip()[:255] or "Vos appels manqués vous coûtent des chantiers",
        preheader="Un standard qui décroche quand vous êtes sur le chantier.",
        from_name="PilotCore",
        reply_to=admin_email.default_from_addr(),
        status=STATUS_DRAFT,
    )
    campaign.set_design(design)
    campaign.set_segment(default_segment())
    _rerender(campaign)
    db.session.add(campaign)
    db.session.commit()
    return campaign


def _rerender(campaign: EmailCampaign) -> None:
    """Keep the stored HTML in step with the design on every save."""
    design = campaign.design()
    ctx = campaign_render.sample_context()
    campaign.html_body = campaign_render.render_html(design, ctx=ctx, preheader=campaign.preheader)
    campaign.plain_body = campaign_render.render_plain(design, ctx=ctx)


def save_campaign(campaign_id, **fields) -> EmailCampaign:
    campaign = get_campaign(campaign_id)
    if not campaign.is_editable:
        raise CampaignError("Une campagne déjà envoyée ne peut plus être modifiée. Dupliquez-la.")

    if "name" in fields:
        campaign.name = (fields["name"] or "").strip()[:160] or campaign.name
    if "subject" in fields:
        campaign.subject = (fields["subject"] or "").strip()[:255]
    if "preheader" in fields:
        campaign.preheader = (fields["preheader"] or "").strip()[:255] or None
    if "from_name" in fields:
        campaign.from_name = (fields["from_name"] or "").strip()[:120] or None
    if "reply_to" in fields:
        campaign.reply_to = (fields["reply_to"] or "").strip()[:255] or None
    if "design" in fields and isinstance(fields["design"], dict):
        campaign.set_design(
            {
                "settings": campaign_render.settings_of(fields["design"]),
                "blocks": campaign_render.blocks_of(fields["design"]),
            }
        )
    if "segment" in fields and isinstance(fields["segment"], dict):
        campaign.set_segment(_clean_segment(fields["segment"]))
    if "ai_prompt" in fields:
        campaign.ai_prompt = (fields["ai_prompt"] or "").strip() or None

    _rerender(campaign)
    campaign.updated_at = utcnow()
    db.session.commit()
    return campaign


def duplicate_campaign(campaign_id) -> EmailCampaign:
    source = get_campaign(campaign_id)
    copy = EmailCampaign(
        name=f"{source.name} (copie)"[:160],
        subject=source.subject,
        preheader=source.preheader,
        from_name=source.from_name,
        reply_to=source.reply_to,
        design_json=source.design_json,
        segment_json=source.segment_json,
        ai_prompt=source.ai_prompt,
        status=STATUS_DRAFT,
    )
    _rerender(copy)
    db.session.add(copy)
    db.session.commit()
    return copy


def delete_campaign(campaign_id) -> None:
    campaign = get_campaign(campaign_id)
    if campaign.status == STATUS_SENDING:
        raise CampaignError("Envoi en cours — mettez la campagne en pause avant de la supprimer.")
    db.session.delete(campaign)
    db.session.commit()


def set_status(campaign_id, status: str) -> EmailCampaign:
    if status not in CAMPAIGN_STATUSES:
        raise CampaignError("Statut invalide.")
    campaign = get_campaign(campaign_id)
    campaign.status = status
    campaign.updated_at = utcnow()
    db.session.commit()
    return campaign


def schedule_campaign(campaign_id, when: str | None) -> EmailCampaign:
    campaign = get_campaign(campaign_id)
    if not when:
        campaign.scheduled_at = None
        campaign.status = STATUS_DRAFT
    else:
        try:
            moment = datetime.fromisoformat(when)
        except ValueError as exc:
            raise CampaignError("Date de programmation invalide.") from exc
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        if moment <= datetime.now(timezone.utc):
            raise CampaignError("Choisissez une date dans le futur.")
        campaign.scheduled_at = moment
        campaign.status = STATUS_SCHEDULED
    campaign.updated_at = utcnow()
    db.session.commit()
    return campaign


# --------------------------------------------------------------------------- #
# Audience
# --------------------------------------------------------------------------- #
def default_segment() -> dict:
    return {
        "trades": [],
        "cities": [],
        "statuses": ["new", "ready"],
        "sources": [],
        "exclude_contacted": True,
        "limit": 200,
    }


def _clean_segment(raw: dict) -> dict:
    def _strings(key, cap=40):
        values = raw.get(key)
        if not isinstance(values, list):
            return []
        return [str(v).strip()[:100] for v in values if str(v).strip()][:cap]

    limit = raw.get("limit")
    try:
        limit = max(1, min(int(limit), 5000))
    except (TypeError, ValueError):
        limit = 200
    return {
        "trades": _strings("trades"),
        "cities": _strings("cities"),
        "statuses": _strings("statuses"),
        "sources": _strings("sources"),
        "exclude_contacted": bool(raw.get("exclude_contacted", True)),
        "limit": limit,
    }


def audience_query(segment: dict):
    """Prospects matching a segment. E-mail and opt-in are never negotiable."""
    segment = _clean_segment(segment or {})
    q = OutreachProspect.query.filter(
        OutreachProspect.email.isnot(None),
        OutreachProspect.email != "",
        OutreachProspect.opted_out_at.is_(None),
        # Unconditional, never a segment choice: someone who opted out, and an
        # address a mail server has already refused for good ("skipped"), are
        # both off-limits. Re-sending to a known-dead address is what turns a
        # sending domain into a spam domain.
        OutreachProspect.status.notin_(("unsubscribed", "skipped")),
    )
    if segment["trades"]:
        q = q.filter(OutreachProspect.trade_type.in_(segment["trades"]))
    if segment["sources"]:
        q = q.filter(OutreachProspect.source.in_(segment["sources"]))
    if segment["statuses"]:
        q = q.filter(OutreachProspect.status.in_(segment["statuses"]))
    if segment["cities"]:
        clauses = [OutreachProspect.city.ilike(f"%{c}%") for c in segment["cities"]]
        q = q.filter(db.or_(*clauses))
    if segment["exclude_contacted"]:
        q = q.filter(OutreachProspect.last_contacted_at.is_(None))
    return q.order_by(OutreachProspect.created_at.desc())


def preview_audience(segment: dict, *, sample: int = 8) -> dict:
    segment = _clean_segment(segment or {})
    q = audience_query(segment)
    total = q.count()
    rows = q.limit(sample).all()
    return {
        "total": total,
        "will_receive": min(total, segment["limit"]),
        "limit": segment["limit"],
        "sample": [
            {
                "email": p.email,
                "name": p.display_name(),
                "city": p.city,
                "trade_type": p.trade_type,
            }
            for p in rows
        ],
    }


def prepare_campaign(campaign_id) -> dict:
    """Freeze the audience into recipient rows. Idempotent: re-running only adds
    people who are not in the list yet."""
    campaign = get_campaign(campaign_id)
    if campaign.status == STATUS_SENT:
        raise CampaignError("Cette campagne est terminée. Dupliquez-la pour un nouvel envoi.")

    segment = _clean_segment(campaign.segment())
    existing = {
        email.lower()
        for (email,) in db.session.query(CampaignRecipient.email).filter(
            CampaignRecipient.campaign_id == campaign.id
        )
    }
    added = 0
    for prospect in audience_query(segment).limit(segment["limit"] * 2).all():
        if added >= segment["limit"]:
            break
        email = (prospect.email or "").strip().lower()
        if not email or email in existing:
            continue
        if not check_recipient(email)[0]:
            continue
        db.session.add(
            CampaignRecipient(
                campaign_id=campaign.id,
                prospect_id=prospect.id,
                email=email,
                first_name=prospect.first_name,
                company_name=prospect.company_name,
                city=prospect.city,
                trade_type=prospect.trade_type,
            )
        )
        existing.add(email)
        added += 1

    db.session.commit()
    total = campaign.recipients.count()
    return {"added": added, "total": total}


# --------------------------------------------------------------------------- #
# Sending
# --------------------------------------------------------------------------- #
def unsubscribe_url(recipient: CampaignRecipient) -> str:
    return f"{_base_url()}/desinscription/{recipient.unsub_token}"


def _render_for(campaign: EmailCampaign, recipient: CampaignRecipient) -> tuple[str, str, str]:
    ctx = campaign_render.merge_context(
        first_name=recipient.first_name,
        company_name=recipient.company_name,
        city=recipient.city,
        trade_type=recipient.trade_type,
        email=recipient.email,
        unsubscribe_url=unsubscribe_url(recipient),
    )
    design = campaign.design()
    subject = campaign_render.apply_merge(campaign.subject or "", ctx)[:255]
    html = campaign_render.render_html(design, ctx=ctx, preheader=campaign.preheader)
    plain = campaign_render.render_plain(design, ctx=ctx)
    return subject, html, plain


def send_test(campaign_id, to_addr: str) -> dict:
    """Send the campaign to one address, with sample merge values."""
    campaign = get_campaign(campaign_id)
    to_addr = (to_addr or "").strip()
    ok, reason = check_recipient(to_addr)
    if not ok:
        raise CampaignError(f"Adresse de test invalide ({reason}).")
    if not (campaign.subject or "").strip():
        raise CampaignError("Renseignez l'objet avant d'envoyer un test.")

    probe = CampaignRecipient(
        campaign_id=campaign.id,
        email=to_addr,
        first_name="Julien",
        company_name="Dupont Plomberie",
        city="Lyon",
        trade_type="plombier",
    )
    subject, html, plain = _render_for(campaign, probe)
    row = admin_email.send_email(
        to_addr,
        f"[TEST] {subject}"[:255],
        plain,
        is_html=True,
        html_body=html,
        reply_to=campaign.reply_to or admin_email.default_from_addr(),
    )
    return {"status": row.status, "error": row.error}


def send_batch(campaign_id, *, batch_size: int = DEFAULT_BATCH) -> dict:
    """Send the next slice of pending recipients. Returns progress for the caller."""
    campaign = get_campaign(campaign_id)
    if campaign.status == STATUS_PAUSED:
        raise CampaignError("Campagne en pause — reprenez-la pour continuer l'envoi.")
    if not (campaign.subject or "").strip():
        raise CampaignError("Renseignez l'objet de l'e-mail avant l'envoi.")
    if not campaign_render.blocks_of(campaign.design()):
        raise CampaignError("La campagne est vide — ajoutez au moins un bloc.")

    batch_size = max(1, min(int(batch_size or DEFAULT_BATCH), MAX_BATCH))
    pending = (
        CampaignRecipient.query.filter(
            CampaignRecipient.campaign_id == campaign.id,
            CampaignRecipient.status == R_PENDING,
        )
        .order_by(CampaignRecipient.created_at.asc())
        .limit(batch_size)
        .all()
    )
    if not pending and campaign.recipients.count() == 0:
        raise CampaignError("Aucun destinataire — préparez d'abord l'audience.")

    if campaign.status != STATUS_SENDING:
        campaign.status = STATUS_SENDING
        campaign.started_at = campaign.started_at or utcnow()
        db.session.commit()

    sent = failed = skipped = 0
    unsubscribe_mailto = admin_email.default_from_addr()

    for recipient in pending:
        prospect = (
            db.session.get(OutreachProspect, recipient.prospect_id)
            if recipient.prospect_id
            else None
        )
        # Somebody may have unsubscribed between the snapshot and this batch.
        if prospect is not None and prospect.opted_out_at is not None:
            recipient.status = R_UNSUBSCRIBED
            recipient.unsubscribed_at = prospect.opted_out_at
            skipped += 1
            continue
        deliverable, reason = check_recipient(recipient.email)
        if not deliverable:
            recipient.status = R_SKIPPED
            recipient.error = f"Adresse non délivrable ({reason})."[:500]
            skipped += 1
            continue

        try:
            subject, html, plain = _render_for(campaign, recipient)
            row = admin_email.send_email(
                recipient.email,
                subject,
                plain,
                is_html=True,
                html_body=html,
                reply_to=campaign.reply_to or unsubscribe_mailto,
                list_unsubscribe=(
                    f"<mailto:{unsubscribe_mailto}?subject=desinscription>, "
                    f"<{unsubscribe_url(recipient)}>"
                ),
            )
        except Exception as exc:  # noqa: BLE001 — one bad address never kills a batch
            db.session.rollback()
            logger.exception("Campaign send failed for %s", recipient.email)
            recipient = db.session.get(CampaignRecipient, recipient.id)
            if recipient:
                recipient.status = R_FAILED
                recipient.error = f"{type(exc).__name__}: {exc}"[:500]
            failed += 1
            db.session.commit()
            continue

        recipient.email_message_id = row.id
        recipient.sent_at = utcnow()
        if row.status == "failed":
            recipient.status = R_FAILED
            recipient.error = (row.error or "erreur SMTP")[:500]
            failed += 1
        else:
            recipient.status = R_SENT
            recipient.error = None
            sent += 1
            if prospect is not None:
                prospect.status = "contacted"
                prospect.last_contacted_at = recipient.sent_at
                prospect.updated_at = utcnow()
        db.session.commit()

    remaining = CampaignRecipient.query.filter(
        CampaignRecipient.campaign_id == campaign.id,
        CampaignRecipient.status == R_PENDING,
    ).count()
    if remaining == 0:
        campaign.status = STATUS_SENT
        campaign.finished_at = utcnow()
    campaign.updated_at = utcnow()
    db.session.commit()

    return {
        "campaign_id": str(campaign.id),
        "sent": sent,
        "failed": failed,
        "skipped": skipped,
        "remaining": remaining,
        "status": campaign.status,
        "done": remaining == 0,
    }


def run_due_campaigns(*, batch_size: int = DEFAULT_BATCH) -> list[dict]:
    """Cron entry point: start scheduled campaigns and advance in-flight ones."""
    now = datetime.now(timezone.utc)
    due = EmailCampaign.query.filter(
        EmailCampaign.status == STATUS_SCHEDULED,
        EmailCampaign.scheduled_at.isnot(None),
        EmailCampaign.scheduled_at <= now,
    ).all()
    for campaign in due:
        try:
            prepare_campaign(campaign.id)
        except CampaignError as exc:
            logger.warning("Scheduled campaign %s not prepared: %s", campaign.id, exc)

    in_flight = EmailCampaign.query.filter(
        EmailCampaign.status.in_((STATUS_SCHEDULED, STATUS_SENDING)),
        db.or_(
            EmailCampaign.scheduled_at.is_(None),
            EmailCampaign.scheduled_at <= now,
        ),
    ).all()
    reports = []
    for campaign in in_flight:
        try:
            reports.append(send_batch(campaign.id, batch_size=batch_size))
        except CampaignError as exc:
            logger.warning("Campaign %s batch skipped: %s", campaign.id, exc)
    return reports


# --------------------------------------------------------------------------- #
# Unsubscribe
# --------------------------------------------------------------------------- #
def unsubscribe(token: str) -> CampaignRecipient | None:
    """Honour an unsubscribe link. Terminal, and applied to the prospect too."""
    recipient = CampaignRecipient.query.filter_by(unsub_token=(token or "").strip()).first()
    if not recipient:
        return None
    if recipient.status != R_UNSUBSCRIBED:
        recipient.status = R_UNSUBSCRIBED
        recipient.unsubscribed_at = utcnow()
    if recipient.prospect_id:
        prospect = db.session.get(OutreachProspect, recipient.prospect_id)
        if prospect and prospect.opted_out_at is None:
            prospect.status = "unsubscribed"
            prospect.opted_out_at = utcnow()
            prospect.updated_at = utcnow()
    # The same person may sit in several campaigns; one click covers them all.
    CampaignRecipient.query.filter(
        CampaignRecipient.email == recipient.email,
        CampaignRecipient.status == R_PENDING,
    ).update(
        {"status": R_UNSUBSCRIBED, "unsubscribed_at": utcnow()}, synchronize_session=False
    )
    db.session.commit()
    return recipient


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def _recipient_counts(campaign_id) -> dict:
    rows = (
        db.session.query(CampaignRecipient.status, func.count())
        .filter(CampaignRecipient.campaign_id == campaign_id)
        .group_by(CampaignRecipient.status)
        .all()
    )
    return {status: count for status, count in rows}


def campaign_stats(campaign_id) -> dict:
    campaign = get_campaign(campaign_id)
    counts = _recipient_counts(campaign.id)
    recipients = sum(counts.values())
    sent = counts.get(R_SENT, 0)

    joined = db.session.query(EmailMessage).join(
        CampaignRecipient, CampaignRecipient.email_message_id == EmailMessage.id
    ).filter(CampaignRecipient.campaign_id == campaign.id)

    delivered = joined.filter(EmailMessage.status.in_((MSG_SENT, MSG_SIMULATED))).count()
    opened = joined.filter(EmailMessage.first_opened_at.isnot(None)).count()
    clicked = joined.filter(EmailMessage.first_clicked_at.isnot(None)).count()
    total_opens = int(
        db.session.query(func.coalesce(func.sum(EmailMessage.open_count), 0))
        .join(CampaignRecipient, CampaignRecipient.email_message_id == EmailMessage.id)
        .filter(CampaignRecipient.campaign_id == campaign.id)
        .scalar()
        or 0
    )
    total_clicks = int(
        db.session.query(func.coalesce(func.sum(EmailMessage.click_count), 0))
        .join(CampaignRecipient, CampaignRecipient.email_message_id == EmailMessage.id)
        .filter(CampaignRecipient.campaign_id == campaign.id)
        .scalar()
        or 0
    )

    links: dict[str, int] = {}
    for message in joined.filter(EmailMessage.click_urls_json.isnot(None)).all():
        for link in message.clicked_links():
            links[link["url"]] = links.get(link["url"], 0) + link["count"]
    top_links = sorted(
        ({"url": u, "count": c} for u, c in links.items()),
        key=lambda r: -r["count"],
    )[:8]

    return {
        "recipients": recipients,
        "pending": counts.get(R_PENDING, 0),
        "sent": sent,
        "failed": counts.get(R_FAILED, 0),
        "skipped": counts.get(R_SKIPPED, 0),
        "unsubscribed": counts.get(R_UNSUBSCRIBED, 0),
        "delivered": delivered,
        "unique_opens": opened,
        "unique_clicks": clicked,
        "total_opens": total_opens,
        "total_clicks": total_clicks,
        "delivery_rate": format_rate(delivered, sent),
        "open_rate": format_rate(opened, delivered),
        "click_rate": format_rate(clicked, delivered),
        "ctor": format_rate(clicked, opened),
        "unsub_rate": format_rate(counts.get(R_UNSUBSCRIBED, 0), sent),
        "progress": format_rate(sent + counts.get(R_FAILED, 0) + counts.get(R_SKIPPED, 0), recipients),
        "top_links": top_links,
    }


def overview_stats() -> dict:
    """Numbers for the campaign list header."""
    campaigns = EmailCampaign.query.count()
    sending = EmailCampaign.query.filter(
        EmailCampaign.status.in_((STATUS_SENDING, STATUS_SCHEDULED))
    ).count()

    joined = db.session.query(EmailMessage).join(
        CampaignRecipient, CampaignRecipient.email_message_id == EmailMessage.id
    )
    sent = CampaignRecipient.query.filter(CampaignRecipient.status == R_SENT).count()
    delivered = joined.filter(EmailMessage.status.in_((MSG_SENT, MSG_SIMULATED))).count()
    opened = joined.filter(EmailMessage.first_opened_at.isnot(None)).count()
    clicked = joined.filter(EmailMessage.first_clicked_at.isnot(None)).count()
    unsubscribed = CampaignRecipient.query.filter(
        CampaignRecipient.status == R_UNSUBSCRIBED
    ).count()

    contactable = OutreachProspect.query.filter(
        OutreachProspect.email.isnot(None),
        OutreachProspect.email != "",
        OutreachProspect.opted_out_at.is_(None),
    ).count()

    return {
        "campaigns": campaigns,
        "active": sending,
        "contactable": contactable,
        "sent": sent,
        "delivered": delivered,
        "unique_opens": opened,
        "unique_clicks": clicked,
        "unsubscribed": unsubscribed,
        "open_rate": format_rate(opened, delivered),
        "click_rate": format_rate(clicked, delivered),
        "delivery_rate": format_rate(delivered, sent),
    }


def recipient_rows(campaign_id, *, status: str | None = None, limit: int = 300) -> list[dict]:
    q = CampaignRecipient.query.filter(CampaignRecipient.campaign_id == _coerce_id(campaign_id))
    if status:
        q = q.filter(CampaignRecipient.status == status)
    rows = q.order_by(CampaignRecipient.created_at.asc()).limit(limit).all()
    return [r.to_dict() for r in rows]
