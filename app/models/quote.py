import json
import secrets
import uuid
from datetime import datetime, timezone

from sqlalchemy import Uuid

from app.core.extensions import db


def utcnow():
    return datetime.now(timezone.utc)


# doc_type: a "devis" (quote) can be converted into a "facture" (invoice).
DOC_DEVIS = "devis"
DOC_FACTURE = "facture"
DOC_TYPES = (DOC_DEVIS, DOC_FACTURE)

# Status lifecycle. Devis: draft -> sent -> accepted/refused. Once accepted a
# devis can be converted to a facture, which then goes sent -> paid.
STATUS_DRAFT = "draft"
STATUS_SENT = "sent"
STATUS_ACCEPTED = "accepted"
STATUS_REFUSED = "refused"
STATUS_PAID = "paid"
STATUS_CANCELLED = "cancelled"
QUOTE_STATUSES = (
    STATUS_DRAFT,
    STATUS_SENT,
    STATUS_ACCEPTED,
    STATUS_REFUSED,
    STATUS_PAID,
    STATUS_CANCELLED,
)


def _round2(value):
    return round(float(value or 0), 2)


class Quote(db.Model):
    """A devis (quote) or facture (invoice) for a plumbing job.

    Line items are stored as JSON so the plumber can freely add/remove rows
    without a migration. Each item is
    ``{"label", "quantity", "unit_price", "tva_rate"}`` where prices are HT
    (hors taxes) in euros and ``tva_rate`` is a percentage (e.g. 10 or 20).
    """

    __tablename__ = "quotes"

    id = db.Column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id = db.Column(Uuid, db.ForeignKey("tenants.id"), nullable=False, index=True)
    lead_id = db.Column(Uuid, db.ForeignKey("leads.id"), nullable=True, index=True)

    doc_type = db.Column(db.String(10), nullable=False, default=DOC_DEVIS)
    number = db.Column(db.String(40), nullable=True, index=True)

    # Client snapshot (kept independent of the lead so edits/deletes are safe).
    client_name = db.Column(db.String(255), nullable=True)
    client_phone = db.Column(db.String(50), nullable=True)
    client_address = db.Column(db.String(500), nullable=True)

    title = db.Column(db.String(255), nullable=True)
    items_json = db.Column(db.Text, nullable=True)
    deposit_percent = db.Column(db.Integer, nullable=True)
    notes = db.Column(db.Text, nullable=True)

    status = db.Column(db.String(20), nullable=False, default=STATUS_DRAFT)
    valid_until = db.Column(db.DateTime(timezone=True), nullable=True)

    # Random token backing the client-facing accept/refuse link.
    public_token = db.Column(db.String(64), nullable=True, index=True)

    sent_at = db.Column(db.DateTime(timezone=True), nullable=True)
    accepted_at = db.Column(db.DateTime(timezone=True), nullable=True)
    refused_at = db.Column(db.DateTime(timezone=True), nullable=True)
    paid_at = db.Column(db.DateTime(timezone=True), nullable=True)
    last_reminded_at = db.Column(db.DateTime(timezone=True), nullable=True)
    reminder_count = db.Column(db.Integer, nullable=False, default=0)

    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    tenant = db.relationship("Tenant")
    lead = db.relationship("Lead")

    # ---- line items -------------------------------------------------------
    def get_items(self):
        if not self.items_json:
            return []
        try:
            items = json.loads(self.items_json)
        except (json.JSONDecodeError, TypeError):
            return []
        return items if isinstance(items, list) else []

    def set_items(self, items):
        cleaned = []
        for item in items or []:
            label = (item.get("label") or "").strip()
            if not label:
                continue
            cleaned.append(
                {
                    "label": label,
                    "quantity": _round2(item.get("quantity", 1)) or 1,
                    "unit_price": _round2(item.get("unit_price")),
                    "tva_rate": _round2(item.get("tva_rate", 10)),
                }
            )
        self.items_json = json.dumps(cleaned, ensure_ascii=False)

    def ensure_token(self):
        if not self.public_token:
            self.public_token = secrets.token_urlsafe(24)
        return self.public_token

    # ---- totals -----------------------------------------------------------
    @property
    def total_ht(self):
        return _round2(sum(i["quantity"] * i["unit_price"] for i in self.get_items()))

    @property
    def total_tva(self):
        return _round2(
            sum(i["quantity"] * i["unit_price"] * (i["tva_rate"] / 100.0) for i in self.get_items())
        )

    @property
    def total_ttc(self):
        return _round2(self.total_ht + self.total_tva)

    @property
    def deposit_amount(self):
        if not self.deposit_percent:
            return None
        return _round2(self.total_ttc * (self.deposit_percent / 100.0))

    @property
    def tva_breakdown(self):
        """Totals grouped by TVA rate — required on French invoices."""
        buckets = {}
        for item in self.get_items():
            rate = item["tva_rate"]
            base = item["quantity"] * item["unit_price"]
            bucket = buckets.setdefault(rate, {"rate": rate, "base_ht": 0.0, "tva": 0.0})
            bucket["base_ht"] += base
            bucket["tva"] += base * (rate / 100.0)
        result = []
        for rate in sorted(buckets):
            b = buckets[rate]
            result.append(
                {"rate": rate, "base_ht": _round2(b["base_ht"]), "tva": _round2(b["tva"])}
            )
        return result

    @property
    def is_invoice(self):
        return self.doc_type == DOC_FACTURE

    @property
    def is_overdue_reminder(self):
        """A sent devis with no client decision for 3+ days should be relaunched."""
        if self.doc_type != DOC_DEVIS or self.status != STATUS_SENT:
            return False
        anchor = self.last_reminded_at or self.sent_at
        if not anchor:
            return False
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=timezone.utc)
        return (utcnow() - anchor).days >= 3

    def to_dict(self):
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "lead_id": str(self.lead_id) if self.lead_id else None,
            "doc_type": self.doc_type,
            "number": self.number,
            "client_name": self.client_name,
            "client_phone": self.client_phone,
            "client_address": self.client_address,
            "title": self.title,
            "items": self.get_items(),
            "deposit_percent": self.deposit_percent,
            "deposit_amount": self.deposit_amount,
            "notes": self.notes,
            "status": self.status,
            "total_ht": self.total_ht,
            "total_tva": self.total_tva,
            "total_ttc": self.total_ttc,
            "valid_until": self.valid_until.isoformat() if self.valid_until else None,
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
            "accepted_at": self.accepted_at.isoformat() if self.accepted_at else None,
            "paid_at": self.paid_at.isoformat() if self.paid_at else None,
            "reminder_count": self.reminder_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
