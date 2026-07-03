import uuid
from datetime import datetime, timedelta, timezone

from flask import (
    Blueprint,
    abort,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from app.core.extensions import db
from app.core.web_auth import web_tenant_required
from app.models.lead import Lead
from app.models.quote import (
    DOC_DEVIS,
    DOC_FACTURE,
    STATUS_ACCEPTED,
    STATUS_CANCELLED,
    STATUS_DRAFT,
    STATUS_PAID,
    STATUS_REFUSED,
    STATUS_SENT,
    Quote,
)
from app.models.tenant import Tenant
from app.services import notifications, quote_engine

quotes_bp = Blueprint("quotes", __name__, url_prefix="/quotes")


def utcnow():
    return datetime.now(timezone.utc)


def _get_quote(quote_id):
    try:
        qid = uuid.UUID(str(quote_id))
    except ValueError:
        abort(404)
    quote = Quote.query.filter_by(id=qid, tenant_id=g.tenant_id).first()
    if not quote:
        abort(404)
    return quote


def _parse_items(form):
    """Read the repeated line-item fields posted by the form."""
    labels = form.getlist("item_label")
    quantities = form.getlist("item_quantity")
    prices = form.getlist("item_unit_price")
    rates = form.getlist("item_tva_rate")
    items = []
    for idx, label in enumerate(labels):
        label = (label or "").strip()
        if not label:
            continue

        def _num(values, i, default=0.0):
            try:
                return float((values[i] or "").replace(",", ".")) if i < len(values) else default
            except (ValueError, AttributeError):
                return default

        items.append(
            {
                "label": label,
                "quantity": _num(quantities, idx, 1) or 1,
                "unit_price": _num(prices, idx, 0),
                "tva_rate": _num(rates, idx, quote_engine.DEFAULT_TVA),
            }
        )
    return items


def _apply_form(quote, form):
    quote.client_name = (form.get("client_name") or "").strip() or None
    quote.client_phone = (form.get("client_phone") or "").strip() or None
    quote.client_address = (form.get("client_address") or "").strip() or None
    quote.title = (form.get("title") or "").strip() or None
    quote.notes = (form.get("notes") or "").strip() or None

    deposit_raw = (form.get("deposit_percent") or "").strip()
    if deposit_raw:
        try:
            quote.deposit_percent = max(0, min(100, int(float(deposit_raw))))
        except ValueError:
            quote.deposit_percent = None
    else:
        quote.deposit_percent = None

    quote.set_items(_parse_items(form))


# --------------------------------------------------------------------------
# List
# --------------------------------------------------------------------------
@quotes_bp.route("", methods=["GET"])
@web_tenant_required
def quotes_page():
    view = request.args.get("view", "devis")
    doc_type = DOC_FACTURE if view == "factures" else DOC_DEVIS
    quotes = (
        Quote.query.filter_by(tenant_id=g.tenant_id, doc_type=doc_type)
        .order_by(Quote.created_at.desc())
        .all()
    )
    followups = quote_engine.quotes_needing_followup(g.tenant_id)
    return render_template(
        "quotes.html",
        quotes=quotes,
        view=view,
        doc_type=doc_type,
        followup_ids={str(q.id) for q in followups},
        followup_count=len(followups),
    )


# --------------------------------------------------------------------------
# Create
# --------------------------------------------------------------------------
@quotes_bp.route("/create", methods=["POST"])
@web_tenant_required
def create_quote():
    """One-click devis creation: persist a ready-to-edit draft immediately.

    The plumber lands straight on the editable devis (already saved and listed)
    instead of an empty form that creates nothing until submitted.
    """
    tenant = db.session.get(Tenant, g.tenant_id)

    lead = None
    lead_id = request.form.get("lead_id")
    if lead_id:
        try:
            lead = Lead.query.filter_by(
                id=uuid.UUID(lead_id), tenant_id=g.tenant_id
            ).first()
        except ValueError:
            lead = None

    quote = quote_engine.build_draft_from_lead(lead, tenant)
    quote.number = quote_engine.generate_number(g.tenant_id, DOC_DEVIS)
    db.session.add(quote)
    db.session.commit()
    return redirect(url_for("quotes.edit_quote", quote_id=quote.id))


@quotes_bp.route("/new", methods=["GET", "POST"])
@web_tenant_required
def new_quote():
    tenant = db.session.get(Tenant, g.tenant_id)

    if request.method == "POST":
        quote = Quote(tenant_id=g.tenant_id, doc_type=DOC_DEVIS, status=STATUS_DRAFT)
        lead_id = request.form.get("lead_id")
        if lead_id:
            try:
                lead = Lead.query.filter_by(
                    id=uuid.UUID(lead_id), tenant_id=g.tenant_id
                ).first()
                if lead:
                    quote.lead_id = lead.id
            except ValueError:
                pass
        _apply_form(quote, request.form)
        quote.number = quote_engine.generate_number(g.tenant_id, DOC_DEVIS)
        quote.valid_until = utcnow() + timedelta(days=quote_engine.DEVIS_VALIDITY_DAYS)
        quote.ensure_token()
        db.session.add(quote)
        db.session.commit()
        return redirect(url_for("quotes.quote_detail", quote_id=quote.id))

    # GET: pre-fill a draft (optionally from a lead) without persisting.
    lead = None
    lead_id = request.args.get("lead_id")
    if lead_id:
        try:
            lead = Lead.query.filter_by(
                id=uuid.UUID(lead_id), tenant_id=g.tenant_id
            ).first()
        except ValueError:
            lead = None

    draft = quote_engine.build_draft_from_lead(lead, tenant)
    return render_template(
        "quote_form.html",
        quote=draft,
        lead=lead,
        is_new=True,
        tva_rates=[5.5, 10, 20],
    )


# --------------------------------------------------------------------------
# Detail (printable) + edit
# --------------------------------------------------------------------------
@quotes_bp.route("/<quote_id>", methods=["GET"])
@web_tenant_required
def quote_detail(quote_id):
    quote = _get_quote(quote_id)
    tenant = db.session.get(Tenant, g.tenant_id)
    return render_template("quote_view.html", quote=quote, tenant=tenant, owner_view=True)


@quotes_bp.route("/<quote_id>/edit", methods=["GET", "POST"])
@web_tenant_required
def edit_quote(quote_id):
    quote = _get_quote(quote_id)
    if request.method == "POST":
        _apply_form(quote, request.form)
        db.session.commit()
        return redirect(url_for("quotes.quote_detail", quote_id=quote.id))
    return render_template(
        "quote_form.html",
        quote=quote,
        lead=quote.lead,
        is_new=False,
        tva_rates=[5.5, 10, 20],
    )


# --------------------------------------------------------------------------
# Status transitions
# --------------------------------------------------------------------------
@quotes_bp.route("/<quote_id>/status", methods=["POST"])
@web_tenant_required
def change_status(quote_id):
    quote = _get_quote(quote_id)
    action = request.form.get("action")

    if action == "send":
        quote_engine.mark_sent(quote)
    elif action == "accept":
        result = quote_engine.accept_quote(quote)
        db.session.commit()
        if not result["already"]:
            notifications.notify_quote_accepted(
                quote, appointment=result["appointment"], invoice=result["invoice"]
            )
        return redirect(url_for("quotes.quote_detail", quote_id=quote.id))
    elif action == "refuse":
        quote.status = STATUS_REFUSED
        quote.refused_at = utcnow()
        db.session.commit()
        notifications.notify_quote_refused(quote)
        return redirect(url_for("quotes.quote_detail", quote_id=quote.id))
    elif action == "pay":
        quote.status = STATUS_PAID
        quote.paid_at = utcnow()
    elif action == "cancel":
        quote.status = STATUS_CANCELLED
    elif action == "reopen":
        quote.status = STATUS_DRAFT
        quote.accepted_at = None
        quote.refused_at = None

    db.session.commit()
    return redirect(url_for("quotes.quote_detail", quote_id=quote.id))


@quotes_bp.route("/<quote_id>/remind", methods=["POST"])
@web_tenant_required
def remind(quote_id):
    quote = _get_quote(quote_id)
    quote_engine.mark_reminded(quote)
    db.session.commit()
    return redirect(request.referrer or url_for("quotes.quotes_page"))


@quotes_bp.route("/<quote_id>/convert", methods=["POST"])
@web_tenant_required
def convert(quote_id):
    quote = _get_quote(quote_id)
    if quote.doc_type != DOC_DEVIS:
        return redirect(url_for("quotes.quote_detail", quote_id=quote.id))
    invoice = quote_engine.convert_to_invoice(quote, g.tenant_id)
    db.session.add(invoice)
    db.session.commit()
    return redirect(url_for("quotes.quote_detail", quote_id=invoice.id))


@quotes_bp.route("/<quote_id>/delete", methods=["POST"])
@web_tenant_required
def delete_quote(quote_id):
    quote = _get_quote(quote_id)
    view = "factures" if quote.doc_type == DOC_FACTURE else "devis"
    db.session.delete(quote)
    db.session.commit()
    return redirect(url_for("quotes.quotes_page", view=view))


# --------------------------------------------------------------------------
# Public client-facing accept / refuse (token, no auth)
# --------------------------------------------------------------------------
@quotes_bp.route("/public/<quote_id>/<token>", methods=["GET"])
def public_quote(quote_id, token):
    try:
        qid = uuid.UUID(str(quote_id))
    except ValueError:
        abort(404)
    quote = Quote.query.filter_by(id=qid).first()
    if not quote or not quote.public_token or quote.public_token != token:
        abort(404)
    tenant = db.session.get(Tenant, quote.tenant_id)
    return render_template(
        "quote_public.html", quote=quote, tenant=tenant, token=token
    )


@quotes_bp.route("/public/<quote_id>/<token>/decision", methods=["POST"])
def public_decision(quote_id, token):
    try:
        qid = uuid.UUID(str(quote_id))
    except ValueError:
        abort(404)
    quote = Quote.query.filter_by(id=qid).first()
    if not quote or not quote.public_token or quote.public_token != token:
        abort(404)

    # Only an outstanding devis can be decided by the client.
    if quote.doc_type == DOC_DEVIS and quote.status in (STATUS_SENT, STATUS_DRAFT):
        decision = request.form.get("decision")
        if decision == "accept":
            # Client acceptance auto-generates the facture and schedules the RDV.
            result = quote_engine.accept_quote(quote)
            db.session.commit()
            if not result["already"]:
                notifications.notify_quote_accepted(
                    quote, appointment=result["appointment"], invoice=result["invoice"]
                )
        elif decision == "refuse":
            quote.status = STATUS_REFUSED
            quote.refused_at = utcnow()
            db.session.commit()
            notifications.notify_quote_refused(quote)

    tenant = db.session.get(Tenant, quote.tenant_id)
    return render_template(
        "quote_public.html", quote=quote, tenant=tenant, token=token, submitted=True
    )
