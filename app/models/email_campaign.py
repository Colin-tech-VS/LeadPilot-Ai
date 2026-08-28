"""Mailing campaigns — a designed e-mail sent to a segment of prospects.

Two tables, and the split matters:

``EmailCampaign``
    The *design*: subject, sender, the block document the editor manipulates,
    and the segment rule that decides who receives it. Editing a campaign never
    touches anybody's mailbox.

``CampaignRecipient``
    The *audience snapshot*, frozen when the campaign is prepared. Recomputing
    the segment at send time would silently change who is being mailed between
    two batches — someone imported mid-send would receive a campaign they were
    never reviewed into. One row per person, carrying its own unsubscribe token
    and a link to the ``EmailMessage`` actually sent, which is where opens and
    clicks are already recorded.
"""
from __future__ import annotations

import json
import secrets
import uuid
from datetime import datetime, timezone

from sqlalchemy import ForeignKey, Uuid

from app.core.extensions import db


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


STATUS_DRAFT = "draft"
STATUS_SCHEDULED = "scheduled"
STATUS_SENDING = "sending"
STATUS_SENT = "sent"
STATUS_PAUSED = "paused"

CAMPAIGN_STATUSES = (STATUS_DRAFT, STATUS_SCHEDULED, STATUS_SENDING, STATUS_SENT, STATUS_PAUSED)

CAMPAIGN_STATUS_LABELS = {
    STATUS_DRAFT: "Brouillon",
    STATUS_SCHEDULED: "Programmée",
    STATUS_SENDING: "En cours d'envoi",
    STATUS_SENT: "Envoyée",
    STATUS_PAUSED: "En pause",
}

R_PENDING = "pending"
R_SENT = "sent"
R_FAILED = "failed"
R_SKIPPED = "skipped"
R_UNSUBSCRIBED = "unsubscribed"

RECIPIENT_STATUS_LABELS = {
    R_PENDING: "En attente",
    R_SENT: "Envoyé",
    R_FAILED: "Échec",
    R_SKIPPED: "Ignoré",
    R_UNSUBSCRIBED: "Désinscrit",
}


def new_unsub_token() -> str:
    return secrets.token_urlsafe(24)


class EmailCampaign(db.Model):
    __tablename__ = "email_campaigns"

    id = db.Column(Uuid, primary_key=True, default=uuid.uuid4)
    name = db.Column(db.String(160), nullable=False, default="Nouvelle campagne")
    subject = db.Column(db.String(255), nullable=False, default="")
    preheader = db.Column(db.String(255), nullable=True)
    from_name = db.Column(db.String(120), nullable=True)
    reply_to = db.Column(db.String(255), nullable=True)

    # The block document the designer edits — the single source of truth for
    # the HTML, which is re-rendered on every save so the two can never drift.
    design_json = db.Column(db.Text, nullable=True)
    html_body = db.Column(db.Text, nullable=True)
    plain_body = db.Column(db.Text, nullable=True)

    # Audience rule, kept for display and for re-preparing the campaign.
    segment_json = db.Column(db.Text, nullable=True)

    status = db.Column(db.String(20), nullable=False, default=STATUS_DRAFT, index=True)
    scheduled_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)
    started_at = db.Column(db.DateTime(timezone=True), nullable=True)
    finished_at = db.Column(db.DateTime(timezone=True), nullable=True)

    ai_prompt = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    updated_at = db.Column(
        db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    recipients = db.relationship(
        "CampaignRecipient",
        back_populates="campaign",
        cascade="all, delete-orphan",
        lazy="dynamic",
    )

    # ---------------------------------------------------------------- helpers
    def design(self) -> dict:
        if not self.design_json:
            return {}
        try:
            data = json.loads(self.design_json)
        except (json.JSONDecodeError, TypeError):
            return {}
        return data if isinstance(data, dict) else {}

    def set_design(self, design: dict) -> None:
        self.design_json = json.dumps(design, ensure_ascii=False)

    def segment(self) -> dict:
        if not self.segment_json:
            return {}
        try:
            data = json.loads(self.segment_json)
        except (json.JSONDecodeError, TypeError):
            return {}
        return data if isinstance(data, dict) else {}

    def set_segment(self, segment: dict) -> None:
        self.segment_json = json.dumps(segment, ensure_ascii=False)

    @property
    def status_label(self) -> str:
        return CAMPAIGN_STATUS_LABELS.get(self.status, self.status)

    @property
    def is_editable(self) -> bool:
        """A campaign stops being editable the moment mail has gone out."""
        return self.status in (STATUS_DRAFT, STATUS_SCHEDULED, STATUS_PAUSED)

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "name": self.name,
            "subject": self.subject,
            "preheader": self.preheader,
            "from_name": self.from_name,
            "reply_to": self.reply_to,
            "design": self.design(),
            "segment": self.segment(),
            "status": self.status,
            "status_label": self.status_label,
            "editable": self.is_editable,
            "scheduled_at": self.scheduled_at.isoformat() if self.scheduled_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class CampaignRecipient(db.Model):
    __tablename__ = "campaign_recipients"

    id = db.Column(Uuid, primary_key=True, default=uuid.uuid4)
    campaign_id = db.Column(
        Uuid, ForeignKey("email_campaigns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    prospect_id = db.Column(Uuid, ForeignKey("outreach_prospects.id"), nullable=True, index=True)
    email = db.Column(db.String(255), nullable=False, index=True)

    # Denormalised at snapshot time: a prospect edited (or deleted) after the
    # send must not rewrite what a delivered e-mail actually said.
    first_name = db.Column(db.String(100), nullable=True)
    company_name = db.Column(db.String(255), nullable=True)
    city = db.Column(db.String(100), nullable=True)
    trade_type = db.Column(db.String(30), nullable=True)
    # The registry listing this recipient was matched to when the audience was
    # frozen. Kept as the identifier rather than a foreign key: the link is
    # re-checked at send time, because a fiche claimed or withdrawn between
    # « préparer » and « envoyer » must not be linked to.
    listing_siren = db.Column(db.String(9), nullable=True)

    status = db.Column(db.String(20), nullable=False, default=R_PENDING, index=True)
    email_message_id = db.Column(Uuid, ForeignKey("email_messages.id"), nullable=True, index=True)
    unsub_token = db.Column(db.String(64), nullable=False, default=new_unsub_token, unique=True, index=True)
    sent_at = db.Column(db.DateTime(timezone=True), nullable=True)
    unsubscribed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    error = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    campaign = db.relationship("EmailCampaign", back_populates="recipients")
    message = db.relationship("EmailMessage", foreign_keys=[email_message_id])

    __table_args__ = (
        db.UniqueConstraint("campaign_id", "email", name="uq_campaign_recipient_email"),
        db.Index("ix_campaign_recipients_campaign_status", "campaign_id", "status"),
    )

    def display_name(self) -> str:
        return self.first_name or self.company_name or "Artisan"

    def to_dict(self) -> dict:
        msg = self.message
        return {
            "id": str(self.id),
            "email": self.email,
            "name": self.display_name(),
            "company_name": self.company_name,
            "city": self.city,
            "trade_type": self.trade_type,
            "status": self.status,
            "status_label": RECIPIENT_STATUS_LABELS.get(self.status, self.status),
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
            "error": self.error,
            "opened": bool(msg and msg.first_opened_at),
            "clicked": bool(msg and msg.first_clicked_at),
            "opens": int(msg.open_count or 0) if msg else 0,
            "clicks": int(msg.click_count or 0) if msg else 0,
        }
