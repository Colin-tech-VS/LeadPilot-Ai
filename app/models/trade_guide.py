"""Cached SEO guide content per trade (Mistral-generated, TTL-refreshed).

Each row is a long-form, unique paragraph (~800 words) + a topical FAQ that
fills the trade pillar page (`/artisans/metier/<trade>`) with the substance
Google needs to rank the URL on head keywords. Regenerated on demand from the
admin, and auto-invalidated after ``fresh_days``.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import Uuid

from app.core.extensions import db


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TradeGuide(db.Model):
    """One row per (trade_key, lang). Never seeded — created on-demand."""

    __tablename__ = "trade_guides"

    id = db.Column(Uuid, primary_key=True, default=uuid.uuid4)
    trade_key = db.Column(db.String(40), nullable=False, index=True)
    lang = db.Column(db.String(5), nullable=False, default="fr")
    intro_html = db.Column(db.Text, nullable=True)
    body_html = db.Column(db.Text, nullable=True)
    faq_json = db.Column(db.Text, nullable=True)
    price_hints = db.Column(db.Text, nullable=True)
    generated_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    __table_args__ = (
        db.UniqueConstraint("trade_key", "lang", name="uq_trade_guides_trade_lang"),
    )

    def get_faq(self) -> list[dict]:
        if not self.faq_json:
            return []
        try:
            data = json.loads(self.faq_json)
        except (json.JSONDecodeError, TypeError):
            return []
        return [item for item in data if isinstance(item, dict) and item.get("question")]

    def set_faq(self, items: list[dict]) -> None:
        cleaned = [
            {"question": (i.get("question") or "").strip(), "answer": (i.get("answer") or "").strip()}
            for i in (items or [])
            if (i.get("question") or "").strip()
        ]
        self.faq_json = json.dumps(cleaned, ensure_ascii=False) if cleaned else None

    def is_fresh(self, ttl_days: int = 90) -> bool:
        if not self.generated_at:
            return False
        age = utcnow() - self.generated_at
        return age.days < ttl_days
