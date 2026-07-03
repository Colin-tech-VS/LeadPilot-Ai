import logging

from flask import Blueprint, current_app, g, redirect, render_template, request, url_for

from app.core.web_auth import web_tenant_required
from app.core.extensions import db
from app.models.tenant import Tenant
from app.services import billing

logger = logging.getLogger(__name__)

billing_bp = Blueprint("billing", __name__, url_prefix="/billing")


@billing_bp.route("", methods=["GET"])
@web_tenant_required
def billing_page():
    tenant = db.session.get(Tenant, g.tenant_id)
    return render_template(
        "billing.html",
        tenant=tenant,
        plans=billing.available_plans(),
        stripe_ready=billing.is_configured(),
        checkout_status=request.args.get("status"),
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
