"""Customer (particulier) area — register, login and book an artisan online.

A customer is a ``User`` with ``role="customer"`` and no tenant. Booking an
artisan requires being signed in as a customer: the booking then creates a Lead
(so the artisan sees the client) + an Appointment, and fires branded
confirmation emails to both sides.
"""
import logging
from datetime import datetime

from flask import (
    Blueprint,
    current_app,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from app.core.errors import AppError, ConflictError
from app.core.extensions import db
from app.core.security import check_rate
from app.core.web_auth import (
    login_user_to_session,
    logout_user_session,
    web_customer_required,
)
from app.models.appointment import Appointment
from app.models.lead import Lead
from app.models.tenant import Tenant
from app.models.user import User
from app.utils.validation import validate_email

logger = logging.getLogger(__name__)

customer_bp = Blueprint("customer", __name__, url_prefix="/client")


def _safe_next(raw: str | None) -> str:
    """Only allow same-site relative redirects."""
    if raw and raw.startswith("/") and not raw.startswith("//"):
        return raw
    return url_for("web.artisan_directory")


@customer_bp.route("/register", methods=["GET", "POST"])
def register():
    if session.get("user_id") and session.get("role") == "customer":
        return redirect(url_for("customer.account"))

    next_url = request.args.get("next") or request.form.get("next")
    error = None
    form = {}
    if request.method == "POST":
        from app.services.signup_service import register_customer
        from app.utils.validation import validate_password

        form = {
            "first_name": (request.form.get("first_name") or "").strip(),
            "last_name": (request.form.get("last_name") or "").strip(),
            "email": (request.form.get("email") or "").strip().lower(),
            "phone": (request.form.get("phone") or "").strip(),
        }
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm_password") or ""

        if not check_rate("customer_register", limit=5, window=3600):
            error = "Trop de tentatives. Réessayez plus tard."
        elif not form["email"] or not password or not form["first_name"]:
            error = "Prénom, e-mail et mot de passe sont requis."
        elif password != confirm:
            error = "Les mots de passe ne correspondent pas."
        else:
            try:
                validate_email(form["email"])
                validate_password(password)
                user = register_customer(
                    email=form["email"],
                    password=password,
                    first_name=form["first_name"],
                    last_name=form["last_name"] or None,
                    phone=form["phone"] or None,
                )
                login_user_to_session(user)
                return redirect(_safe_next(next_url) if next_url else url_for("customer.account"))
            except ConflictError:
                error = "Cet e-mail est déjà utilisé."
            except AppError as e:
                msg = str(e.message).lower()
                if "password" in msg:
                    error = "Mot de passe trop court (8 caractères minimum)."
                else:
                    error = "E-mail invalide."
            except Exception:
                logger.exception("Customer registration failed for %s", form.get("email"))
                db.session.rollback()
                error = "Une erreur est survenue. Réessayez."

    return render_template("customer/register.html", error=error, form=form, next=next_url)


@customer_bp.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id") and session.get("role") == "customer":
        return redirect(url_for("customer.account"))

    next_url = request.args.get("next") or request.form.get("next")
    error = None
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        if not check_rate("customer_login", limit=10, window=300):
            error = "Trop de tentatives. Réessayez plus tard."
        elif not email or not password:
            error = "E-mail et mot de passe requis."
        else:
            try:
                email = validate_email(email)
            except AppError:
                error = "E-mail invalide."
            else:
                user = User.query.filter_by(email=email).first()
                if not user or not user.check_password(password) or user.role != "customer":
                    error = "Identifiants incorrects."
                else:
                    login_user_to_session(user)
                    return redirect(_safe_next(next_url) if next_url else url_for("customer.account"))

    return render_template("customer/login.html", error=error, next=next_url)


@customer_bp.route("/logout", methods=["GET", "POST"])
def logout():
    logout_user_session()
    return redirect(url_for("web.artisan_directory"))


@customer_bp.route("/account", methods=["GET"])
@web_customer_required
def account():
    user = g.current_user
    # Bookings the customer made are Leads carrying their email across tenants.
    leads = (
        Lead.query.filter(Lead.email == user.email)
        .order_by(Lead.created_at.desc())
        .limit(50)
        .all()
    )
    bookings = []
    from zoneinfo import ZoneInfo

    paris = ZoneInfo("Europe/Paris")
    for lead in leads:
        appt = lead.appointments.order_by(Appointment.date_time.desc()).first()
        tenant = db.session.get(Tenant, lead.tenant_id)
        bookings.append(
            {
                "artisan": tenant.name if tenant else "—",
                "artisan_slug": tenant.public_slug if tenant else None,
                "when": appt.date_time.astimezone(paris).strftime("%a %d/%m/%Y · %H:%M") if appt else None,
                "status": (appt.status if appt else lead.status),
                "issue": lead.summary,
            }
        )
    return render_template("customer/account.html", user=user, bookings=bookings)


@customer_bp.route("/book/<slug>", methods=["POST"])
@web_customer_required
def book(slug):
    from app.services.artisan_directory import get_public_artisan_by_slug
    from app.services.availability import book_appointment

    tenant = get_public_artisan_by_slug(slug)
    if not tenant:
        return redirect(url_for("web.artisan_directory"))

    user = g.current_user
    slot_iso = (request.form.get("slot_iso") or "").strip()
    issue = (request.form.get("issue") or "").strip() or None
    if not slot_iso:
        return redirect(url_for("web.artisan_profile", slug=slug, booking="noslot"))

    try:
        slot_dt = datetime.fromisoformat(slot_iso)
    except ValueError:
        return redirect(url_for("web.artisan_profile", slug=slug, booking="noslot"))

    # Create the lead (the artisan's view of this client) then book the slot.
    lead = Lead(
        tenant_id=tenant.id,
        name=user.full_name or user.email,
        phone=user.phone or "",
        email=user.email,
        issue_type="general_inquiry",
        urgency_level="medium",
        status="new",
        summary=issue or "Réservation en ligne",
    )
    db.session.add(lead)
    db.session.flush()

    appt = book_appointment(tenant.id, lead.id, slot_dt)
    if not appt:
        db.session.rollback()
        return redirect(url_for("web.artisan_profile", slug=slug, booking="taken"))

    # Branded confirmation emails to both sides (never block the booking).
    try:
        from zoneinfo import ZoneInfo

        from app.services.transactional_email import (
            send_appointment_confirmation,
            send_new_booking_to_artisan,
        )

        when_label = appt.date_time.astimezone(ZoneInfo("Europe/Paris")).strftime("%A %d/%m/%Y à %H:%M")
        send_appointment_confirmation(
            user.email, when_label, tenant.name,
            customer_name=user.first_name, tenant_id=tenant.id,
        )
        artisan_email = None
        artisan_user = next((u for u in tenant.users), None) if tenant.users else None
        if artisan_user:
            artisan_email = artisan_user.email
        if artisan_email:
            send_new_booking_to_artisan(
                artisan_email, when_label, user.full_name or user.email,
                tenant_id=tenant.id, customer_phone=user.phone, issue=issue,
            )
    except Exception:
        logger.exception("Booking confirmation emails failed for tenant=%s", tenant.id)

    return redirect(url_for("customer.account", booking="ok"))
