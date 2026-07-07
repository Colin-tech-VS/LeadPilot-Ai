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
from app.services import quote_payment
from app.utils.validation import EMAIL_REGEX

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
    quote.client_email = (form.get("client_email") or "").strip().lower() or None
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
        "artisan/quotes.html",
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
        "artisan/quote_form.html",
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
    return render_template(
        "artisan/quote_view.html",
        quote=quote,
        tenant=tenant,
        owner_view=True,
        sent_result=request.args.get("sent"),
    )


@quotes_bp.route("/<quote_id>/edit", methods=["GET", "POST"])
@web_tenant_required
def edit_quote(quote_id):
    quote = _get_quote(quote_id)
    if request.method == "POST":
        _apply_form(quote, request.form)
        db.session.commit()
        return redirect(url_for("quotes.quote_detail", quote_id=quote.id))
    return render_template(
        "artisan/quote_form.html",
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


@quotes_bp.route("/<quote_id>/send", methods=["POST"])
@web_tenant_required
def send_quote(quote_id):
    """Actually send the devis to the client (email and/or SMS) with the RIB.

    Unlike the plain "mark sent" transition, this delivers the online devis link
    to the client through the selected channels and includes the bank details
    for the acompte. The devis is marked sent when at least one channel went out.
    """
    from app.services import quote_delivery

    quote = _get_quote(quote_id)
    if quote.is_invoice:
        return redirect(url_for("quotes.quote_detail", quote_id=quote.id))

    tenant = db.session.get(Tenant, g.tenant_id)

    channels = request.form.getlist("channel") or None
    result = quote_delivery.send_quote(quote, tenant, channels=channels)

    if result["any"]:
        quote_engine.mark_sent(quote)
        quote.sent_channel = result["channel"]
        db.session.commit()
        notifications.notify_quote_sent(quote)
        status = result["channel"]
    else:
        status = "none"

    return redirect(url_for("quotes.quote_detail", quote_id=quote.id, sent=status))


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
def _send_booking_confirmed_emails(quote, appointment):
    """After devis signature, notify client and artisan that the visit is confirmed."""
    if not appointment:
        return
    from zoneinfo import ZoneInfo

    tenant = db.session.get(Tenant, quote.tenant_id)
    if not tenant:
        return
    paris = ZoneInfo("Europe/Paris")
    when_label = appointment.date_time.astimezone(paris).strftime("%A %d/%m/%Y à %H:%M")
    try:
        from app.services.transactional_email import (
            send_appointment_confirmation,
            send_new_booking_to_artisan,
        )

        if quote.client_email:
            send_appointment_confirmation(
                quote.client_email,
                when_label,
                tenant.name,
                customer_name=(quote.client_name or "").split()[0] if quote.client_name else None,
                tenant_id=tenant.id,
            )
        artisan_user = next((u for u in tenant.users), None) if tenant.users else None
        if artisan_user and artisan_user.email:
            send_new_booking_to_artisan(
                artisan_user.email,
                when_label,
                quote.client_name or "Client",
                tenant_id=tenant.id,
                customer_phone=quote.client_phone,
                issue=quote.notes,
            )
    except Exception:
        pass


def _public_quote_context(quote, tenant, token, **extra):
    return {
        "quote": quote,
        "tenant": tenant,
        "token": token,
        "stripe_deposit": quote_payment.deposit_required(quote),
        "deposit_paid": bool(quote.deposit_paid_at),
        **extra,
    }


def _normalize_public_email(raw: str) -> str | None:
    email = (raw or "").strip().lower()
    if email and EMAIL_REGEX.match(email):
        return email
    return None


def _apply_client_signature(quote, form):
    """Persist client email + typed signature before acceptance or Stripe."""
    client_email = _normalize_public_email(form.get("client_email"))
    client_signed_name = (form.get("client_signed_name") or "").strip()
    if not client_email:
        return "Veuillez indiquer une adresse e-mail valide."
    if not client_signed_name or len(client_signed_name) < 2:
        return "Veuillez signer le devis en indiquant votre nom complet."
    quote.client_email = client_email
    quote.client_signed_name = client_signed_name
    quote.client_signed_at = utcnow()
    if quote.lead_id:
        lead = db.session.get(Lead, quote.lead_id)
        if lead:
            lead.email = client_email
    return None


def _finalize_acceptance(quote):
    result = quote_engine.accept_quote(quote)
    db.session.commit()
    if not result["already"]:
        notifications.notify_quote_accepted(
            quote, appointment=result["appointment"], invoice=result["invoice"]
        )
        _send_booking_confirmed_emails(quote, result.get("appointment"))
    return result


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
        "public/quote_public.html",
        **_public_quote_context(quote, tenant, token),
    )


@quotes_bp.route("/public/<quote_id>/<token>/deposit-success", methods=["GET"])
def deposit_success(quote_id, token):
    try:
        qid = uuid.UUID(str(quote_id))
    except ValueError:
        abort(404)
    quote = Quote.query.filter_by(id=qid).first()
    if not quote or not quote.public_token or quote.public_token != token:
        abort(404)

    session_id = request.args.get("session_id")
    outcome = quote_payment.verify_session_and_finalize(session_id, quote)
    tenant = db.session.get(Tenant, quote.tenant_id)

    if outcome.get("accepted"):
        result = outcome.get("result") or {}
        if not result.get("already"):
            notifications.notify_quote_accepted(
                quote, appointment=result.get("appointment"), invoice=result.get("invoice")
            )
            _send_booking_confirmed_emails(quote, result.get("appointment"))

    return render_template(
        "public/quote_public.html",
        **_public_quote_context(
            quote,
            tenant,
            token,
            submitted=True,
            deposit_return=True,
            payment_ok=outcome.get("paid"),
        ),
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
    form_error = None
    if quote.doc_type == DOC_DEVIS and quote.status in (STATUS_SENT, STATUS_DRAFT):
        decision = request.form.get("decision")
        if decision == "accept":
            form_error = _apply_client_signature(quote, request.form)
            if not form_error:
                if quote_payment.deposit_required(quote):
                    try:
                        checkout_url = quote_payment.create_deposit_session(
                            quote,
                            quote_payment.deposit_success_url(quote, token),
                            quote_payment.deposit_cancel_url(quote, token),
                        )
                        db.session.commit()
                        return redirect(checkout_url)
                    except Exception:
                        form_error = (
                            "Le paiement en ligne est temporairement indisponible. "
                            "Merci de contacter l'artisan."
                        )
                else:
                    _finalize_acceptance(quote)
        elif decision == "refuse":
            quote.status = STATUS_REFUSED
            quote.refused_at = utcnow()
            if quote.lead_id:
                from app.services.availability import cancel_tentative_for_lead

                cancel_tentative_for_lead(quote.lead_id)
                lead = db.session.get(Lead, quote.lead_id)
                if lead and lead.status == "new":
                    lead.status = "lost"
            db.session.commit()
            notifications.notify_quote_refused(quote)

    tenant = db.session.get(Tenant, quote.tenant_id)
    return render_template(
        "public/quote_public.html",
        **_public_quote_context(
            quote, tenant, token, submitted=not form_error, form_error=form_error
        ),
    )
