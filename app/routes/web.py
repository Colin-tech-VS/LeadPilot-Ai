import uuid
from datetime import datetime, timedelta, timezone

from flask import Blueprint, g, jsonify, make_response, redirect, render_template, request, session, url_for
from sqlalchemy.orm import joinedload

from app.core.errors import AppError
from app.core.i18n import set_language_preference
from app.core.extensions import db
from app.core.web_auth import login_user_to_session, logout_user_session, web_tenant_required
from app.models.appointment import Appointment
from app.models.lead import Lead
from app.models.tenant import Tenant
from app.models.user import User
from app.utils.i18n import translate
from app.utils.validation import validate_email

web_bp = Blueprint("web", __name__)


@web_bp.context_processor
def inject_tenant():
    from flask import current_app

    tid = session.get("tenant_id")
    tenant = None
    if tid:
        try:
            tenant = db.session.get(Tenant, uuid.UUID(tid))
        except ValueError:
            tenant = None
    return {
        "current_tenant": tenant,
        "twilio_ai_phone_display": current_app.config.get(
            "TWILIO_AI_PHONE_DISPLAY", "+33 1 59 16 96 91"
        ),
        "twilio_ai_phone_e164": current_app.config.get(
            "TWILIO_AI_PHONE_NUMBER", "+33159169691"
        ),
    }


@web_bp.route("/set-language/<lang>", methods=["GET"])
def set_language(lang):
    lang = set_language_preference(lang)
    redirect_to = request.referrer or url_for("web.landing")
    response = make_response(redirect(redirect_to))
    response.set_cookie("lang", lang, max_age=365 * 24 * 3600)
    return response


@web_bp.route("/manifest.webmanifest", methods=["GET"])
def web_manifest():
    """PWA manifest — makes the dashboard installable on mobile and desktop."""
    from flask import current_app, send_from_directory

    return send_from_directory(
        current_app.static_folder,
        "manifest.webmanifest",
        mimetype="application/manifest+json",
    )


@web_bp.route("/sw.js", methods=["GET"])
def service_worker():
    """Serve the service worker from the root so its scope covers every page."""
    from flask import current_app, send_from_directory

    response = make_response(
        send_from_directory(
            current_app.static_folder, "sw.js", mimetype="application/javascript"
        )
    )
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-cache"
    return response


@web_bp.route("/api/notifications/feed", methods=["GET"])
@web_tenant_required
def notifications_feed():
    """Return notifications created since a cursor (for live web/mobile alerts).

    Powers static/js/notifications.js: any new lead, urgent call, booked RDV,
    accepted/refused devis surfaces as a toast + native OS notification.
    """
    from app.models.notification import Notification

    now = datetime.now(timezone.utc)
    since_raw = request.args.get("since")
    since = None
    if since_raw:
        # A literal "+" in the query string decodes to a space; the ISO cursor
        # uses "T" as its date/time separator, so the only space is the offset's
        # "+" — restore it before parsing.
        since_raw = since_raw.strip().replace(" ", "+").replace("Z", "+00:00")
        try:
            since = datetime.fromisoformat(since_raw)
        except (ValueError, AttributeError):
            since = None
    if since is None:
        since = now - timedelta(minutes=1)

    # Compare against a naive-UTC cutoff: created_at is stored as UTC, and a
    # timezone-aware bind is silently ignored by SQLite (dev), which would make
    # the same row resurface on every poll. Naive-UTC is correct on both SQLite
    # and UTC Postgres (prod).
    if since.tzinfo is not None:
        since = since.astimezone(timezone.utc).replace(tzinfo=None)

    rows = (
        Notification.query.filter(
            Notification.tenant_id == g.tenant_id,
            Notification.created_at > since,
        )
        .order_by(Notification.created_at.asc())
        .limit(30)
        .all()
    )

    unread = (
        Notification.query.filter(
            Notification.tenant_id == g.tenant_id,
            Notification.read_at.is_(None),
        ).count()
    )

    return jsonify(
        {
            "now": now.isoformat(),
            "unread": unread,
            "notifications": [n.to_dict() for n in rows],
        }
    ), 200


@web_bp.route("/api/notifications/read", methods=["POST"])
@web_tenant_required
def notifications_mark_read():
    """Mark all of the tenant's notifications as read (clears the badge)."""
    from app.models.notification import Notification

    Notification.query.filter(
        Notification.tenant_id == g.tenant_id,
        Notification.read_at.is_(None),
    ).update({Notification.read_at: datetime.now(timezone.utc)})
    db.session.commit()
    return jsonify({"ok": True}), 200


@web_bp.route("/robots.txt", methods=["GET"])
def robots_txt():
    base = request.url_root.rstrip("/")
    body = f"User-agent: *\nAllow: /\nDisallow: /dashboard\nDisallow: /leads\nDisallow: /appointments\nDisallow: /settings\nDisallow: /test-call\nSitemap: {base}/sitemap.xml\n"
    return make_response(body, 200, {"Content-Type": "text/plain; charset=utf-8"})


@web_bp.route("/sitemap.xml", methods=["GET"])
def sitemap_xml():
    base = request.url_root.rstrip("/")
    urls = [
        ("", "daily", "1.0"),
        ("/register", "monthly", "0.9"),
        ("/login", "monthly", "0.6"),
    ]
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for path, freq, priority in urls:
        loc = base + path
        lines.append("  <url>")
        lines.append(f"    <loc>{loc}</loc>")
        lines.append(f"    <changefreq>{freq}</changefreq>")
        lines.append(f"    <priority>{priority}</priority>")
        lines.append("  </url>")
    lines.append("</urlset>")
    return make_response("\n".join(lines), 200, {"Content-Type": "application/xml; charset=utf-8"})


@web_bp.route("/", methods=["GET"])
def landing():
    if session.get("user_id") and session.get("tenant_id"):
        return redirect(url_for("web.dashboard"))
    return render_template("landing.html")


@web_bp.route("/demo/simulate", methods=["POST"])
def demo_simulate():
    from app.services.demo_simulate import simulate_inbound_demo

    data = request.get_json(silent=True) or {}
    transcript = (data.get("transcript") or "").strip()
    phone = (data.get("phone") or "+33600000000").strip()
    if not transcript:
        return jsonify({"error": "transcript required"}), 422
    try:
        result = simulate_inbound_demo(transcript, phone)
        return jsonify(result), 200
    except Exception:
        return jsonify({"error": "demo failed", "demo": True}), 503


@web_bp.route("/register", methods=["GET", "POST"])
def register():
    if session.get("user_id") and session.get("tenant_id"):
        return redirect(url_for("web.dashboard"))

    error = None
    form = {}

    if request.method == "POST":
        from app.core.errors import ConflictError
        from app.services.signup_service import register_plumber
        from app.utils.validation import validate_password

        form = {
            "company_name": (request.form.get("company_name") or "").strip(),
            "first_name": (request.form.get("first_name") or "").strip(),
            "last_name": (request.form.get("last_name") or "").strip(),
            "email": (request.form.get("email") or "").strip().lower(),
            "phone": (request.form.get("phone") or "").strip(),
            "city": (request.form.get("city") or "").strip(),
        }
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm_password") or ""

        if not form["company_name"] or not form["email"] or not password:
            error = translate("register.error.required")
        elif password != confirm:
            error = translate("register.error.password_mismatch")
        else:
            try:
                validate_email(form["email"])
                validate_password(password)
                user, _tenant = register_plumber(
                    email=form["email"],
                    password=password,
                    company_name=form["company_name"],
                    phone=form["phone"] or None,
                    city=form["city"] or None,
                    first_name=form["first_name"] or None,
                    last_name=form["last_name"] or None,
                )
                login_user_to_session(user)
                return redirect(url_for("web.dashboard"))
            except ConflictError:
                error = translate("register.error.email_taken")
            except AppError as e:
                if "email" in str(e.message).lower():
                    error = translate("login.error.invalid_email")
                elif "password" in str(e.message).lower():
                    error = translate("register.error.password_short")
                else:
                    error = str(e.message)

    return render_template("register.html", error=error, form=form)


@web_bp.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id") and session.get("tenant_id"):
        return redirect(url_for("web.dashboard"))

    error_key = session.pop("flash_error_key", None)
    error = translate(error_key) if error_key else None

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        if not email or not password:
            error = translate("login.error.required")
        else:
            try:
                email = validate_email(email)
            except AppError:
                error = translate("login.error.invalid_email")
            else:
                user = User.query.filter_by(email=email).first()
                if not user or not user.check_password(password):
                    error = translate("login.error.invalid_credentials")
                elif not user.tenant_id:
                    error = translate("login.error.no_tenant")
                else:
                    login_user_to_session(user)
                    return redirect(url_for("web.dashboard"))

    return render_template("login.html", error=error)


@web_bp.route("/logout", methods=["GET"])
def logout():
    lang = session.get("lang")
    logout_user_session()
    if lang:
        session["lang"] = lang
    return redirect(url_for("web.landing"))


@web_bp.route("/dashboard", methods=["GET"])
@web_tenant_required
def dashboard():
    tenant = db.session.get(Tenant, g.tenant_id)
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow = today_start + timedelta(days=1)

    all_leads = Lead.query.filter_by(tenant_id=g.tenant_id).filter(Lead.archived_at.is_(None)).all()
    calls_today = Lead.query.filter(
        Lead.tenant_id == g.tenant_id,
        Lead.created_at >= today_start,
        Lead.archived_at.is_(None),
    ).count()
    appointments_today = Appointment.query.filter(
        Appointment.tenant_id == g.tenant_id, Appointment.created_at >= today_start
    ).count()
    from app.services import quote_engine

    pending_quotes = quote_engine.pending_quote_count(g.tenant_id)
    quote_followups = quote_engine.followup_count(g.tenant_id)
    urgencies = Lead.query.filter(
        Lead.tenant_id == g.tenant_id,
        Lead.urgency_level == "high",
        Lead.archived_at.is_(None),
    ).count()

    recent_leads = (
        Lead.query.filter_by(tenant_id=g.tenant_id)
        .filter(Lead.archived_at.is_(None))
        .order_by(Lead.created_at.desc())
        .limit(8)
        .all()
    )
    today_appointments = (
        Appointment.query.filter_by(tenant_id=g.tenant_id)
        .filter(Appointment.date_time >= today_start, Appointment.date_time < tomorrow)
        .options(joinedload(Appointment.lead))
        .order_by(Appointment.date_time.asc())
        .all()
    )
    upcoming_appointments = (
        Appointment.query.filter_by(tenant_id=g.tenant_id)
        .filter(Appointment.date_time >= today_start)
        .options(joinedload(Appointment.lead))
        .order_by(Appointment.date_time.asc())
        .limit(5)
        .all()
    )
    total_leads = Lead.query.filter_by(tenant_id=g.tenant_id).filter(Lead.archived_at.is_(None)).count()
    next_appointment = (
        Appointment.query.filter_by(tenant_id=g.tenant_id)
        .filter(Appointment.date_time >= datetime.now(timezone.utc))
        .options(joinedload(Appointment.lead))
        .order_by(Appointment.date_time.asc())
        .first()
    )

    return render_template(
        "dashboard.html",
        tenant=tenant,
        calls_today=calls_today,
        appointments_today=appointments_today,
        pending_quotes=pending_quotes,
        quote_followups=quote_followups,
        urgencies=urgencies,
        recent_leads=recent_leads,
        today_appointments=today_appointments,
        upcoming_appointments=upcoming_appointments,
        total_leads=total_leads,
        next_appointment=next_appointment,
    )


@web_bp.route("/leads", methods=["GET"])
@web_tenant_required
def leads_page():
    show_archived = request.args.get("view") == "archived"
    query = Lead.query.filter_by(tenant_id=g.tenant_id)
    if show_archived:
        query = query.filter(Lead.archived_at.isnot(None))
    else:
        query = query.filter(Lead.archived_at.is_(None))
    leads = query.order_by(Lead.created_at.desc()).all()
    return render_template(
        "leads.html",
        leads=leads,
        show_archived=show_archived,
    )


@web_bp.route("/leads/<lead_id>/archive", methods=["POST"])
@web_tenant_required
def archive_lead(lead_id):
    try:
        lid = uuid.UUID(lead_id)
    except ValueError:
        return redirect(url_for("web.leads_page"))

    lead = Lead.query.filter_by(id=lid, tenant_id=g.tenant_id).first()
    if lead:
        lead.archived_at = datetime.now(timezone.utc)
        db.session.commit()

    return redirect(request.referrer or url_for("web.leads_page"))


@web_bp.route("/leads/<lead_id>/unarchive", methods=["POST"])
@web_tenant_required
def unarchive_lead(lead_id):
    try:
        lid = uuid.UUID(lead_id)
    except ValueError:
        return redirect(url_for("web.leads_page", view="archived"))

    lead = Lead.query.filter_by(id=lid, tenant_id=g.tenant_id).first()
    if lead:
        lead.archived_at = None
        db.session.commit()

    return redirect(url_for("web.leads_page", view="archived"))


@web_bp.route("/appointments", methods=["GET"])
@web_tenant_required
def appointments_page():
    from collections import defaultdict

    from app.utils.geocoding import geocode_address

    DAYS_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]

    appointments = (
        Appointment.query.filter_by(tenant_id=g.tenant_id)
        .options(joinedload(Appointment.lead))
        .order_by(Appointment.date_time.asc())
        .all()
    )

    geocoded = False
    for appt in appointments:
        lead = appt.lead
        if not lead or not lead.address:
            continue
        if lead.latitude is None or lead.longitude is None:
            coords = geocode_address(lead.address)
            if coords:
                lead.latitude, lead.longitude = coords
                geocoded = True

    if geocoded:
        db.session.commit()

    agenda_by_day = defaultdict(list)
    for appt in appointments:
        if appt.date_time:
            day_key = appt.date_time.strftime("%Y-%m-%d")
            agenda_by_day[day_key].append(appt)

    agenda_days = []
    for day_key in sorted(agenda_by_day.keys()):
        day_appts = agenda_by_day[day_key]
        dt = day_appts[0].date_time
        agenda_days.append(
            {
                "day_name": DAYS_FR[dt.weekday()],
                "date_display": dt.strftime("%d/%m/%Y"),
                "count": len(day_appts),
                "appointments": day_appts,
            }
        )

    tenant = db.session.get(Tenant, g.tenant_id)
    depot = None
    if tenant and tenant.latitude is not None and tenant.longitude is not None:
        depot = {
            "lat": tenant.latitude,
            "lng": tenant.longitude,
            "name": tenant.name,
            "label": tenant.city or tenant.name,
        }

    map_markers = []
    if depot:
        map_markers.append(
            {
                "id": "depot",
                "lat": depot["lat"],
                "lng": depot["lng"],
                "name": depot["label"],
                "phone": "",
                "address": tenant.full_address or "",
                "issue": "",
                "time": "Base",
                "date": "",
                "status": "depot",
                "is_depot": True,
            }
        )

    for appt in appointments:
        lead = appt.lead
        if not lead or lead.latitude is None or lead.longitude is None:
            continue
        map_markers.append(
            {
                "id": str(appt.id),
                "lat": lead.latitude,
                "lng": lead.longitude,
                "name": lead.name,
                "phone": lead.phone,
                "address": lead.address or "",
                "issue": lead.issue_type or "",
                "time": appt.date_time.strftime("%H:%M") if appt.date_time else "",
                "date": appt.date_time.strftime("%d/%m/%Y") if appt.date_time else "",
                "status": appt.status,
                "is_depot": False,
            }
        )

    route_days = []
    for day_key in sorted(agenda_by_day.keys()):
        day_appts = sorted(agenda_by_day[day_key], key=lambda a: a.date_time or datetime.min)
        stops = []
        if depot:
            stops.append(
                {
                    "id": f"depot-{day_key}",
                    "lat": depot["lat"],
                    "lng": depot["lng"],
                    "time": "—",
                    "name": depot["label"],
                    "address": tenant.full_address or "",
                    "is_depot": True,
                }
            )
        for appt in day_appts:
            lead = appt.lead
            if not lead or lead.latitude is None or lead.longitude is None:
                continue
            if appt.status in ("cancelled",):
                continue
            stops.append(
                {
                    "id": str(appt.id),
                    "lat": lead.latitude,
                    "lng": lead.longitude,
                    "name": lead.name,
                    "phone": lead.phone,
                    "address": lead.address or "",
                    "issue": lead.issue_type or "",
                    "time": appt.date_time.strftime("%H:%M") if appt.date_time else "",
                    "date": appt.date_time.strftime("%d/%m/%Y") if appt.date_time else "",
                    "status": appt.status,
                    "is_depot": False,
                }
            )

        if len(stops) >= 2:
            dt = day_appts[0].date_time
            route_days.append(
                {
                    "day_key": day_key,
                    "label": f"{DAYS_FR[dt.weekday()]} {dt.strftime('%d/%m/%Y')}",
                    "stops": stops,
                }
            )

    return render_template(
        "appointments.html",
        appointments=appointments,
        agenda_days=agenda_days,
        map_markers=map_markers,
        route_days=route_days,
    )


@web_bp.route("/test-call", methods=["GET"])
@web_tenant_required
def test_call_page():
    import json
    from pathlib import Path

    scenarios_path = Path(__file__).resolve().parent.parent.parent / "scripts" / "test_ia_scenarios.json"
    scenarios = []
    if scenarios_path.exists():
        scenarios = json.loads(scenarios_path.read_text(encoding="utf-8"))

    return render_template(
        "test_call.html",
        tenant_id=str(g.tenant_id),
        scenarios=scenarios,
    )


def _normalize_phone(value):
    if not value:
        return None
    cleaned = "".join(c for c in value.strip() if c.isdigit() or c == "+")
    return cleaned or None


def _normalize_siret(value):
    if not value:
        return None
    digits = "".join(c for c in value if c.isdigit())
    return digits if len(digits) == 14 else None


@web_bp.route("/settings", methods=["GET", "POST"])
@web_tenant_required
def settings_page():
    from app.utils.geocoding import geocode_address

    tenant = db.session.get(Tenant, g.tenant_id)
    user = g.current_user
    success = None
    error = None

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        first_name = (request.form.get("first_name") or "").strip() or None
        last_name = (request.form.get("last_name") or "").strip() or None
        ai_assistant_name = (request.form.get("ai_assistant_name") or "").strip() or None
        siret_raw = (request.form.get("siret") or "").strip()
        phone_number = _normalize_phone(request.form.get("phone_number"))
        ai_phone_number = _normalize_phone(request.form.get("ai_phone_number"))
        address = (request.form.get("address") or "").strip() or None
        postal_code = (request.form.get("postal_code") or "").strip() or None
        city = (request.form.get("city") or "").strip() or None
        service_radius_raw = (request.form.get("service_radius_km") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        new_password = request.form.get("new_password") or ""
        confirm_password = request.form.get("confirm_password") or ""

        if not name:
            error = translate("settings.error.company_required")
        elif siret_raw and not _normalize_siret(siret_raw):
            error = translate("settings.error.siret_invalid")
        elif service_radius_raw:
            try:
                radius_val = int(service_radius_raw)
                if radius_val < 5 or radius_val > 200:
                    error = translate("settings.error.radius_invalid")
            except ValueError:
                error = translate("settings.error.radius_invalid")
        else:
            try:
                validate_email(email)
            except AppError:
                error = translate("login.error.invalid_email")

        if not error and email != user.email:
            existing = User.query.filter(User.email == email, User.id != user.id).first()
            if existing:
                error = translate("settings.error.email_taken")

        if not error and new_password:
            if len(new_password) < 8:
                error = translate("settings.error.password_short")
            elif new_password != confirm_password:
                error = translate("settings.error.password_mismatch")

        if not error:
            tenant.name = name
            tenant.first_name = first_name
            tenant.last_name = last_name
            tenant.ai_assistant_name = ai_assistant_name
            tenant.siret = _normalize_siret(siret_raw) if siret_raw else None
            tenant.phone_number = phone_number
            tenant.ai_phone_number = ai_phone_number
            tenant.address = address
            tenant.postal_code = postal_code
            tenant.city = city
            if service_radius_raw:
                tenant.service_radius_km = int(service_radius_raw)
            elif tenant.service_radius_km is None:
                tenant.service_radius_km = 30

            full_address = tenant.full_address
            if full_address:
                coords = geocode_address(full_address)
                if coords:
                    tenant.latitude, tenant.longitude = coords
                else:
                    tenant.latitude = None
                    tenant.longitude = None
            else:
                tenant.latitude = None
                tenant.longitude = None

            user.email = email
            if new_password:
                user.set_password(new_password)

            db.session.commit()
            success = translate("settings.success")

    return render_template(
        "settings.html",
        tenant=tenant,
        user=user,
        success=success,
        error=error,
    )


@web_bp.route("/api/route-leg", methods=["GET"])
@web_tenant_required
def route_leg():
    from app.utils.routing import fetch_driving_route

    try:
        from_lat = float(request.args.get("from_lat", ""))
        from_lng = float(request.args.get("from_lng", ""))
        to_lat = float(request.args.get("to_lat", ""))
        to_lng = float(request.args.get("to_lng", ""))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid coordinates"}), 422

    route = fetch_driving_route(from_lat, from_lng, to_lat, to_lng)
    if not route:
        return jsonify({"error": "Route not found"}), 404
    return jsonify(route)
