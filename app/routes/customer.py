"""Customer (particulier) area — register, login, dashboard and online booking."""
import logging
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from flask import (
    Blueprint,
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

PARIS = ZoneInfo("Europe/Paris")


def _safe_next(raw: str | None) -> str:
    """Only allow same-site relative redirects; never replay POST-only URLs."""
    if raw and raw.startswith("/") and not raw.startswith("//"):
        path = raw.split("?")[0]
        if path.startswith("/client/book/"):
            slug = path.rstrip("/").rsplit("/", 1)[-1]
            if slug and slug != "complete":
                return url_for("web.artisan_profile", slug=slug, booking="pending")
        return raw
    return url_for("customer.account")


def _redirect_after_customer_auth(next_url: str | None):
    """Finish a deferred booking or send the user to their dashboard."""
    if session.get("pending_booking"):
        return redirect(url_for("customer.complete_pending_booking"))
    if next_url:
        return redirect(_safe_next(next_url))
    return redirect(url_for("customer.account"))


def _current_customer() -> User | None:
    user_id = session.get("user_id")
    if not user_id or session.get("role") != "customer":
        return None
    try:
        user = db.session.get(User, uuid.UUID(str(user_id)))
    except (ValueError, TypeError):
        return None
    if not user or user.role != "customer":
        return None
    return user


def _create_booking(user: User, tenant: Tenant, slot_dt: datetime, issue: str | None):
    """Create lead + appointment and send confirmation emails. Returns appointment or None."""
    from app.services.availability import book_appointment

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
        return None

    try:
        from app.services.transactional_email import (
            send_appointment_confirmation,
            send_new_booking_to_artisan,
        )

        when_label = appt.date_time.astimezone(PARIS).strftime("%A %d/%m/%Y à %H:%M")
        send_appointment_confirmation(
            user.email,
            when_label,
            tenant.name,
            customer_name=user.first_name,
            tenant_id=tenant.id,
        )
        artisan_user = next((u for u in tenant.users), None) if tenant.users else None
        if artisan_user and artisan_user.email:
            send_new_booking_to_artisan(
                artisan_user.email,
                when_label,
                user.full_name or user.email,
                tenant_id=tenant.id,
                customer_phone=user.phone,
                issue=issue,
            )
    except Exception:
        logger.exception("Booking confirmation emails failed for tenant=%s", tenant.id)

    return appt


def _build_bookings(user: User) -> list[dict]:
    leads = (
        Lead.query.filter(Lead.email == user.email)
        .order_by(Lead.created_at.desc())
        .limit(50)
        .all()
    )
    bookings = []
    now = datetime.now(timezone.utc)
    for lead in leads:
        appt = lead.appointments.order_by(Appointment.date_time.desc()).first()
        tenant = db.session.get(Tenant, lead.tenant_id)
        appt_dt = appt.date_time if appt else None
        bookings.append(
            {
                "artisan": tenant.name if tenant else "—",
                "artisan_slug": tenant.public_slug if tenant else None,
                "when": appt_dt.astimezone(PARIS).strftime("%a %d/%m/%Y · %H:%M") if appt_dt else None,
                "when_sort": appt_dt,
                "status": appt.status if appt else lead.status,
                "issue": lead.summary,
                "is_upcoming": bool(appt_dt and appt_dt >= now and (appt.status if appt else "") not in ("cancelled", "completed")),
            }
        )
    return bookings


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
                return _redirect_after_customer_auth(next_url)
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
                    return _redirect_after_customer_auth(next_url)

    return render_template("customer/login.html", error=error, next=next_url)


@customer_bp.route("/logout", methods=["GET", "POST"])
def logout():
    logout_user_session()
    return redirect(url_for("web.artisan_directory"))


@customer_bp.route("/account", methods=["GET"])
@web_customer_required
def account():
    user = g.current_user
    bookings = _build_bookings(user)
    upcoming = [b for b in bookings if b["is_upcoming"]]
    past = [b for b in bookings if not b["is_upcoming"]]
    return render_template(
        "customer/account.html",
        user=user,
        bookings=bookings,
        upcoming=upcoming,
        past=past,
        upcoming_count=len(upcoming),
        total_count=len(bookings),
    )


@customer_bp.route("/profile", methods=["POST"])
@web_customer_required
def update_profile():
    user = g.current_user
    user.first_name = (request.form.get("first_name") or "").strip() or user.first_name
    user.last_name = (request.form.get("last_name") or "").strip() or None
    user.phone = (request.form.get("phone") or "").strip() or None
    db.session.commit()
    return redirect(url_for("customer.account", profile="ok"))


@customer_bp.route("/book/<slug>", methods=["POST"])
def book(slug):
    from app.services.artisan_directory import get_public_artisan_by_slug

    tenant = get_public_artisan_by_slug(slug)
    if not tenant:
        return redirect(url_for("web.artisan_directory"))

    slot_iso = (request.form.get("slot_iso") or "").strip()
    issue = (request.form.get("issue") or "").strip() or None
    if not slot_iso:
        return redirect(url_for("web.artisan_profile", slug=slug, booking="noslot"))

    user = _current_customer()
    if not user:
        session["pending_booking"] = {"slug": slug, "slot_iso": slot_iso, "issue": issue}
        return redirect(
            url_for(
                "customer.register",
                next=url_for("web.artisan_profile", slug=slug, booking="pending"),
            )
        )

    try:
        slot_dt = datetime.fromisoformat(slot_iso)
    except ValueError:
        return redirect(url_for("web.artisan_profile", slug=slug, booking="noslot"))

    appt = _create_booking(user, tenant, slot_dt, issue)
    if not appt:
        return redirect(url_for("web.artisan_profile", slug=slug, booking="taken"))

    return redirect(url_for("customer.account", booking="ok"))


@customer_bp.route("/book/complete", methods=["GET"])
@web_customer_required
def complete_pending_booking():
    """Finalize a booking stored in session before the user signed in."""
    pending = session.pop("pending_booking", None)
    if not pending:
        return redirect(url_for("customer.account"))

    from app.services.artisan_directory import get_public_artisan_by_slug

    slug = pending.get("slug")
    slot_iso = (pending.get("slot_iso") or "").strip()
    issue = pending.get("issue")

    tenant = get_public_artisan_by_slug(slug) if slug else None
    if not tenant or not slot_iso:
        return redirect(url_for("customer.account", booking="error"))

    try:
        slot_dt = datetime.fromisoformat(slot_iso)
    except ValueError:
        return redirect(url_for("web.artisan_profile", slug=slug, booking="noslot"))

    appt = _create_booking(g.current_user, tenant, slot_dt, issue)
    if not appt:
        session["pending_booking"] = pending
        return redirect(url_for("web.artisan_profile", slug=slug, booking="taken"))

    return redirect(url_for("customer.account", booking="ok"))


def customer_session_payload() -> dict | None:
    """Public customer profile for chatbot pre-fill (no secrets)."""
    user = _current_customer()
    if not user:
        return None
    return {
        "name": user.full_name or user.first_name or user.email,
        "first_name": user.first_name,
        "email": user.email,
        "phone": user.phone,
    }
