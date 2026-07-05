"""Admin console (/admin) — analytics, database editor, email center and event
log. Fully separate from the artisan-facing app: its own auth, templates and
static assets.
"""
import uuid
from datetime import datetime, timezone

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from sqlalchemy import inspect as sa_inspect

from app.core.admin_auth import (
    admin_required,
    is_admin_logged_in,
    login_admin,
    logout_admin,
    verify_admin_credentials,
)
from app.core.extensions import db
from app.core.security import rate_limit
from app.models.appointment import Appointment
from app.models.email_message import EmailMessage
from app.models.event import Event
from app.models.lead import Lead
from app.models.notification import Notification
from app.models.page_view import PageView
from app.models.quote import Quote
from app.models.tenant import Tenant
from app.models.user import User
from app.services import admin_email, analytics, traffic
from app.services.events import CAT_ADMIN, CAT_AUTH, LEVEL_SUCCESS, LEVEL_WARNING, log_event

admin_bp = Blueprint(
    "admin",
    __name__,
    url_prefix="/admin",
    template_folder="../../templates/admin",
)


# ---------------------------------------------------------------- DB registry
# Tables exposed in the database editor. ``fields`` are the columns the editor
# lets you add/edit (primary key, timestamps and relations are read-only).
class TableSpec:
    def __init__(self, model, label, fields, protected=False):
        self.model = model
        self.label = label
        self.fields = fields
        self.protected = protected  # deleting these needs the confirm guard


TABLES = {
    "tenants": TableSpec(
        Tenant, "Artisans", ["name", "first_name", "last_name", "phone_number",
                             "ai_phone_number", "city", "postal_code", "plan",
                             "service_radius_km"], protected=True),
    "users": TableSpec(
        User, "Utilisateurs", ["email", "role", "tenant_id"], protected=True),
    "leads": TableSpec(
        Lead, "Prospects", ["name", "phone", "address", "issue_type",
                            "urgency_level", "status", "summary", "tenant_id"]),
    "appointments": TableSpec(
        Appointment, "Rendez-vous", ["lead_id", "tenant_id", "date_time", "status"]),
    "quotes": TableSpec(
        Quote, "Devis / Factures", ["tenant_id", "lead_id", "doc_type", "status"]),
    "notifications": TableSpec(
        Notification, "Notifications", ["tenant_id", "type", "title", "body", "url"]),
    "events": TableSpec(
        Event, "Journal d'évènements", ["category", "action", "level", "summary"]),
    "email_messages": TableSpec(
        EmailMessage, "Emails", ["direction", "status", "from_addr", "to_addr",
                                 "subject", "body"]),
    "page_views": TableSpec(
        PageView, "Pages vues", ["path", "referrer_host", "device"]),
}


@admin_bp.context_processor
def inject_admin():
    return {
        "admin_username": g.get("admin_username"),
        "is_admin": is_admin_logged_in(),
        "admin_tables": {k: v.label for k, v in TABLES.items()},
        "current_year": datetime.now(timezone.utc).year,
    }


# ------------------------------------------------------------------ auth
@admin_bp.route("/login", methods=["GET", "POST"])
@rate_limit(limit=8, window=300, scope="admin_login")
def login():
    if is_admin_logged_in():
        return redirect(url_for("admin.dashboard"))

    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if verify_admin_credentials(username, password):
            login_admin(username)
            log_event(CAT_AUTH, "admin_login", summary=f"Connexion admin: {username}",
                      level=LEVEL_SUCCESS, actor=username)
            return redirect(url_for("admin.dashboard"))
        error = "Identifiants invalides."
        log_event(CAT_AUTH, "admin_login_failed",
                  summary=f"Échec connexion admin: {username or '(vide)'}",
                  level=LEVEL_WARNING, actor=username or "unknown")

    return render_template("admin/login.html", error=error)


@admin_bp.route("/logout", methods=["GET", "POST"])
def logout():
    user = g.get("admin_username")
    logout_admin()
    if user:
        log_event(CAT_AUTH, "admin_logout", summary=f"Déconnexion admin: {user}", actor=user)
    return redirect(url_for("admin.login"))


# ------------------------------------------------------------------ dashboard
@admin_bp.route("")
@admin_bp.route("/")
@admin_required
def dashboard():
    return render_template("admin/dashboard.html")


@admin_bp.route("/api/analytics")
@admin_required
def api_analytics():
    return jsonify(analytics.dashboard_payload(_range_days()))


def _range_days(default=30):
    try:
        return max(1, min(365, int(request.args.get("days", default))))
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------------------ traffic
@admin_bp.route("/traffic")
@admin_required
def traffic_page():
    return render_template("admin/traffic.html")


@admin_bp.route("/api/traffic")
@admin_required
def api_traffic():
    return jsonify(traffic.payload(_range_days()))


@admin_bp.route("/api/traffic/realtime")
@admin_required
def api_traffic_realtime():
    return jsonify(traffic.realtime())


# ------------------------------------------------------------------ database
@admin_bp.route("/maintenance/purge-leads", methods=["POST"])
@admin_required
def purge_leads():
    """Delete every prospect and everything that hangs off it (RDV, devis,
    notifications) while keeping the accounts (artisans + identifiants +
    abonnement). FK-safe order so PostgreSQL never rejects the deletion."""
    if request.form.get("confirm") != "SUPPRIMER":
        flash("Confirmation incorrecte — tapez SUPPRIMER pour valider.", "error")
        return redirect(url_for("admin.database_home"))
    try:
        quotes = Quote.query.delete()
        appts = Appointment.query.delete()
        notifs = Notification.query.delete()
        leads = Lead.query.delete()
        db.session.commit()
        log_event(CAT_ADMIN, "purge_leads",
                  summary=f"Purge: {leads} prospects, {appts} RDV, {quotes} devis, {notifs} notifs",
                  level=LEVEL_WARNING)
        flash(f"Supprimé : {leads} prospect(s), {appts} RDV, {quotes} devis, "
              f"{notifs} notification(s). Comptes conservés.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Erreur pendant la purge : {exc}", "error")
    return redirect(url_for("admin.database_home"))


@admin_bp.route("/database")
@admin_required
def database_home():
    counts = {}
    for name, spec in TABLES.items():
        try:
            counts[name] = spec.model.query.count()
        except Exception:
            counts[name] = "?"
    return render_template("admin/database.html", counts=counts)


def _serialize_row(row):
    mapper = sa_inspect(row.__class__)
    out = {}
    for col in mapper.columns:
        val = getattr(row, col.key)
        out[col.key] = "" if val is None else str(val)
    return out


def _columns(model):
    return [c.key for c in sa_inspect(model).columns]


@admin_bp.route("/database/<table>")
@admin_required
def database_table(table):
    spec = TABLES.get(table)
    if not spec:
        abort(404)
    page = max(1, int(request.args.get("page", 1)))
    per_page = 25
    query = spec.model.query
    order_col = getattr(spec.model, "created_at", None)
    if order_col is not None:
        query = query.order_by(order_col.desc())
    total = query.count()
    rows = query.offset((page - 1) * per_page).limit(per_page).all()
    return render_template(
        "admin/database_table.html",
        table=table,
        spec=spec,
        columns=_columns(spec.model),
        rows=[_serialize_row(r) for r in rows],
        editable_fields=spec.fields,
        page=page,
        per_page=per_page,
        total=total,
        pages=(total + per_page - 1) // per_page,
    )


def _coerce(model, field, value):
    """Turn a form string into the right Python type for the column."""
    from sqlalchemy import Boolean, DateTime, Float, Integer, Uuid

    if value == "":
        return None
    col = sa_inspect(model).columns.get(field)
    if col is None:
        return value
    coltype = col.type
    try:
        if isinstance(coltype, Uuid):
            return uuid.UUID(value)
        if isinstance(coltype, Integer):
            return int(value)
        if isinstance(coltype, Float):
            return float(value)
        if isinstance(coltype, Boolean):
            return value.lower() in ("1", "true", "on", "yes", "oui")
        if isinstance(coltype, DateTime):
            return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return value
    return value


@admin_bp.route("/database/<table>/create", methods=["POST"])
@admin_required
def database_create(table):
    spec = TABLES.get(table)
    if not spec:
        abort(404)
    obj = spec.model()
    for field in spec.fields:
        if field in request.form:
            setattr(obj, field, _coerce(spec.model, field, request.form.get(field, "")))
    # Special-case: hashing a user password if provided.
    if table == "users" and request.form.get("password"):
        obj.set_password(request.form["password"])
    try:
        db.session.add(obj)
        db.session.commit()
        log_event(CAT_ADMIN, "db_create", summary=f"Création dans {table}", level=LEVEL_SUCCESS)
        flash(f"Ligne ajoutée dans {spec.label}.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Erreur: {exc}", "error")
    return redirect(url_for("admin.database_table", table=table))


@admin_bp.route("/database/<table>/<row_id>/update", methods=["POST"])
@admin_required
def database_update(table, row_id):
    spec = TABLES.get(table)
    if not spec:
        abort(404)
    obj = db.session.get(spec.model, _pk_value(spec.model, row_id))
    if not obj:
        abort(404)
    for field in spec.fields:
        if field in request.form:
            setattr(obj, field, _coerce(spec.model, field, request.form.get(field, "")))
    if table == "users" and request.form.get("password"):
        obj.set_password(request.form["password"])
    try:
        db.session.commit()
        log_event(CAT_ADMIN, "db_update", summary=f"Modification {table} #{row_id}", level=LEVEL_SUCCESS)
        flash("Ligne modifiée.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Erreur: {exc}", "error")
    return redirect(url_for("admin.database_table", table=table))


@admin_bp.route("/database/<table>/<row_id>/delete", methods=["POST"])
@admin_required
def database_delete(table, row_id):
    spec = TABLES.get(table)
    if not spec:
        abort(404)
    obj = db.session.get(spec.model, _pk_value(spec.model, row_id))
    if not obj:
        abort(404)
    try:
        db.session.delete(obj)
        db.session.commit()
        log_event(CAT_ADMIN, "db_delete", summary=f"Suppression {table} #{row_id}", level=LEVEL_WARNING)
        flash("Ligne supprimée.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Erreur (clé étrangère ?): {exc}", "error")
    return redirect(url_for("admin.database_table", table=table))


def _pk_value(model, row_id):
    pk = sa_inspect(model).primary_key[0]
    from sqlalchemy import Uuid

    if isinstance(pk.type, Uuid):
        try:
            return uuid.UUID(row_id)
        except ValueError:
            abort(404)
    return row_id


# ------------------------------------------------------------------ emails
@admin_bp.route("/emails")
@admin_required
def emails():
    box = request.args.get("box", "outbox")
    query = EmailMessage.query
    if box == "inbox":
        query = query.filter(EmailMessage.direction == "inbound")
    elif box == "outbox":
        query = query.filter(EmailMessage.direction == "outbound")
    messages = query.order_by(EmailMessage.created_at.desc()).limit(100).all()
    return render_template(
        "admin/emails.html",
        messages=messages,
        box=box,
        smtp_configured=admin_email.is_configured(),
    )


@admin_bp.route("/emails/send", methods=["POST"])
@admin_required
def emails_send():
    to_addr = request.form.get("to", "").strip()
    subject = request.form.get("subject", "").strip()
    body = request.form.get("body", "")
    is_html = request.form.get("is_html") == "on"
    if not to_addr or not subject:
        flash("Destinataire et objet obligatoires.", "error")
        return redirect(url_for("admin.emails", box="outbox"))
    msg = admin_email.send_email(to_addr, subject, body, is_html=is_html)
    flash(f"Email {msg.status} → {to_addr}.", "success")
    return redirect(url_for("admin.emails", box="outbox"))


@admin_bp.route("/email/inbound", methods=["POST"])
def email_inbound():
    """Provider webhook (Mailgun/SendGrid inbound parse). Public but guarded by
    EMAIL_INBOUND_SECRET when set (?secret= or X-Inbound-Secret header)."""
    secret = current_app.config.get("EMAIL_INBOUND_SECRET")
    if secret:
        provided = request.args.get("secret") or request.headers.get("X-Inbound-Secret", "")
        if provided != secret:
            abort(401)
    data = request.form if request.form else (request.get_json(silent=True) or {})
    admin_email.store_inbound(
        from_addr=data.get("from") or data.get("sender"),
        to_addr=data.get("to") or data.get("recipient"),
        subject=data.get("subject"),
        body=data.get("body-plain") or data.get("text") or data.get("body"),
        provider_id=data.get("Message-Id") or data.get("message_id"),
    )
    return jsonify({"ok": True}), 200


# ------------------------------------------------------------------ logs
@admin_bp.route("/logs")
@admin_required
def logs():
    category = request.args.get("category") or None
    level = request.args.get("level") or None
    query = Event.query
    if category:
        query = query.filter(Event.category == category)
    if level:
        query = query.filter(Event.level == level)
    events = query.order_by(Event.created_at.desc()).limit(300).all()
    return render_template("admin/logs.html", events=events, category=category, level=level)


@admin_bp.route("/api/logs")
@admin_required
def api_logs():
    since = request.args.get("since")
    query = Event.query
    if since:
        try:
            query = query.filter(Event.created_at > datetime.fromisoformat(since))
        except ValueError:
            pass
    events = query.order_by(Event.created_at.desc()).limit(50).all()
    return jsonify([e.to_dict() for e in events])
