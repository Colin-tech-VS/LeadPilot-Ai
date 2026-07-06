import logging

from flask import Blueprint, current_app, g, redirect, render_template, request, url_for

from app.core.web_auth import web_tenant_required
from app.core.extensions import db
from app.models.tenant import Tenant
from app.services import billing
from app.utils.i18n import get_lang

logger = logging.getLogger(__name__)

billing_bp = Blueprint("billing", __name__, url_prefix="/billing")


def _fmt_eur(cents: int, lang: str) -> str:
    """Format euro cents for display: '12,50 €' (fr) or '€12.50' (en)."""
    value = f"{cents / 100:.2f}"
    if lang == "fr":
        return value.replace(".", ",") + " €"
    return "€" + value


@billing_bp.route("", methods=["GET"])
@web_tenant_required
def billing_page():
    tenant = db.session.get(Tenant, g.tenant_id)
    lang = get_lang()
    return render_template(
        "billing.html",
        tenant=tenant,
        plans=billing.available_plans(),
        stripe_ready=billing.is_configured(),
        checkout_status=request.args.get("status"),
        call_usage=billing.monthly_call_usage(tenant),
        call_overage=billing.overage_calls(tenant),
        overage_amount=_fmt_eur(billing.overage_amount_cents(tenant), lang),
        overage_unit_price=_fmt_eur(billing.overage_price_cents(), lang),
    )


@billing_bp.route("/checkout/<plan>", methods=["POST"])
@web_tenant_required
def checkout(plan):
    tenant = db.session.get(Tenant, g.tenant_id)
    if not billing.is_configured() or plan not in billing.available_plans():
        return redirect(url_for("billing.billing_page", status="unavailable"))

    success_url = url_for("billing.billing_page", status="success", _external=True)
    cancel_url = url_for("billing.billing_page", status="cancel", _external=True)
    try:
        url = billing.create_checkout_session(tenant, plan, success_url, cancel_url)
    except Exception:
        logger.exception("Stripe checkout failed tenant=%s plan=%s", g.tenant_id, plan)
        return redirect(url_for("billing.billing_page", status="error"))
    return redirect(url, code=303)


@billing_bp.route("/portal", methods=["POST"])
@web_tenant_required
def portal():
    """Redirect to the Stripe Customer Portal to change plan or cancel."""
    tenant = db.session.get(Tenant, g.tenant_id)
    return_url = url_for("billing.billing_page", _external=True)
    try:
        url = billing.create_portal_session(tenant, return_url)
    except Exception:
        logger.exception("Stripe portal session failed tenant=%s", g.tenant_id)
        url = None
    if not url:
        return redirect(url_for("billing.billing_page", status="portal_unavailable"))
    return redirect(url, code=303)


@billing_bp.route("/webhook", methods=["POST"])
def webhook():
    payload = request.get_data()
    signature = request.headers.get("Stripe-Signature", "")
    try:
        handled = billing.handle_webhook(payload, signature)
    except Exception:
        logger.exception("Stripe webhook processing failed")
        return "", 400
    return ("", 200) if handled else ("", 202)
