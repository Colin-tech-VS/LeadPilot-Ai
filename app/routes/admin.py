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
from sqlalchemy import inspect as sa_inspect, or_

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
from app.models.offer import Offer
from app.models.page_view import PageView
from app.models.quote import Quote
from app.models.site_page import SitePage
from app.models.social_post import SocialPost
from app.models.tenant import Tenant
from app.models.user import User
from app.services import admin_email, analytics, content_ai, content_studio, imap_mailbox, social, traffic
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


# -------------------------------------------------------------------- PWA
@admin_bp.route("/manifest.webmanifest", methods=["GET"])
def admin_manifest():
    """PWA manifest for the admin console — makes /admin installable as its own
    standalone webapp. Public so the browser can fetch it from the login page."""
    from flask import send_from_directory

    return send_from_directory(
        current_app.static_folder,
        "admin.webmanifest",
        mimetype="application/manifest+json",
    )


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
@admin_bp.route("/maintenance/purge-accounts", methods=["POST"])
@admin_required
def purge_all_accounts():
    """Delete every tenant, user, and all dependent rows. Admin auth is env-based."""
    if request.form.get("confirm") != "TOUT-SUPPRIMER":
        flash("Confirmation incorrecte — tapez TOUT-SUPPRIMER pour valider.", "error")
        return redirect(url_for("admin.database_home"))
    try:
        from sqlalchemy import text

        tables = [
            "appointments",
            "quotes",
            "notifications",
            "page_views",
            "email_messages",
            "leads",
            "users",
            "tenants",
        ]
        counts = {}
        for name in tables:
            counts[name] = db.session.execute(text(f'DELETE FROM "{name}"')).rowcount
        db.session.commit()
        summary = ", ".join(f"{n} {t}" for t, n in counts.items() if n)
        log_event(
            CAT_ADMIN,
            "purge_accounts",
            summary=f"Purge comptes: {summary or '0 lignes'}",
            level=LEVEL_WARNING,
        )
        flash(
            f"Comptes supprimés : {counts.get('users', 0)} user(s), "
            f"{counts.get('tenants', 0)} tenant(s). Données liées effacées.",
            "success",
        )
    except Exception as exc:
        db.session.rollback()
        flash(f"Erreur pendant la purge : {exc}", "error")
    return redirect(url_for("admin.database_home"))


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


# ------------------------------------------------------------------ clients
@admin_bp.route("/clients")
@admin_required
def clients():
    """Who our customers (particuliers) are — accounts + their bookings."""
    from app.models.lead import Lead

    customers = (
        User.query.filter(User.role == "customer")
        .order_by(User.created_at.desc())
        .limit(500)
        .all()
    )
    rows = []
    for c in customers:
        booking_count = Lead.query.filter(Lead.email == c.email).count()
        rows.append(
            {
                "id": str(c.id),
                "name": c.full_name or "—",
                "email": c.email,
                "phone": c.phone or "—",
                "bookings": booking_count,
                "created_at": c.created_at,
            }
        )
    total_customers = User.query.filter(User.role == "customer").count()
    total_leads = Lead.query.count()
    return render_template(
        "admin/clients.html",
        customers=rows,
        total_customers=total_customers,
        total_leads=total_leads,
    )


# ------------------------------------------------------------------ emails
@admin_bp.route("/emails")
@admin_required
def emails():
    box = request.args.get("box", "inbox")
    q = request.args.get("q", "").strip()
    query = EmailMessage.query
    if box == "inbox":
        query = query.filter(EmailMessage.direction == "inbound")
    elif box == "outbox":
        query = query.filter(EmailMessage.direction == "outbound")
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                EmailMessage.subject.ilike(like),
                EmailMessage.from_addr.ilike(like),
                EmailMessage.to_addr.ilike(like),
                EmailMessage.body.ilike(like),
            )
        )
    messages = query.order_by(EmailMessage.created_at.desc()).limit(100).all()
    unread = EmailMessage.query.filter(
        EmailMessage.direction == "inbound",
        EmailMessage.read_at.is_(None),
    ).count()
    return render_template(
        "admin/emails.html",
        messages=messages,
        box=box,
        q=q,
        unread=unread,
        smtp_configured=admin_email.is_configured(),
        imap_configured=imap_mailbox.is_configured(),
        default_from=admin_email.default_from_addr(),
    )


@admin_bp.route("/emails/<uuid:message_id>")
@admin_required
def email_detail(message_id):
    msg = db.session.get(EmailMessage, message_id)
    if not msg:
        abort(404)
    if msg.direction == "inbound" and msg.read_at is None:
        msg.mark_read()
        db.session.commit()
    thread = []
    if msg.in_reply_to_id:
        parent = db.session.get(EmailMessage, msg.in_reply_to_id)
        if parent:
            thread.append(parent)
    thread.extend(
        EmailMessage.query.filter_by(in_reply_to_id=msg.id)
        .order_by(EmailMessage.created_at.asc())
        .all()
    )
    return render_template(
        "admin/email_detail.html",
        message=msg,
        thread=thread,
        attachments=msg.attachments(),
        smtp_configured=admin_email.is_configured(),
    )


@admin_bp.route("/emails/<uuid:message_id>/read", methods=["POST"])
@admin_required
def email_mark_read(message_id):
    msg = db.session.get(EmailMessage, message_id)
    if not msg:
        abort(404)
    msg.mark_read()
    db.session.commit()
    return redirect(url_for("admin.email_detail", message_id=msg.id))


@admin_bp.route("/emails/<uuid:message_id>/reply", methods=["GET", "POST"])
@admin_required
def email_reply(message_id):
    original = db.session.get(EmailMessage, message_id)
    if not original:
        abort(404)

    if request.method == "GET":
        quoted = (original.body or original.html_body or "").strip()
        if quoted:
            quoted = "\n".join(f"> {line}" for line in quoted.splitlines())
        return render_template(
            "admin/email_compose.html",
            original=original,
            to_addr=original.from_addr or "",
            subject=original.reply_subject(),
            body=f"\n\n{quoted}" if quoted else "",
            default_from=admin_email.default_from_addr(),
        )

    to_addr = request.form.get("to", "").strip()
    subject = request.form.get("subject", "").strip()
    body = request.form.get("body", "")
    is_html = request.form.get("is_html") == "on"
    if not to_addr or not subject:
        flash("Destinataire et objet obligatoires.", "error")
        return redirect(url_for("admin.email_reply", message_id=original.id))

    msg = admin_email.send_email(
        to_addr,
        subject,
        body,
        is_html=is_html,
        in_reply_to_row=original,
    )
    flash(f"Réponse {msg.status} → {to_addr}.", "success")
    return redirect(url_for("admin.email_detail", message_id=msg.id))


@admin_bp.route("/emails/sync", methods=["POST"])
@admin_required
def emails_sync():
    result = imap_mailbox.sync_inbox()
    if result.get("ok"):
        flash(
            f"Synchronisation OK — {result.get('synced', 0)} nouveau(x), "
            f"{result.get('skipped', 0)} ignoré(s).",
            "success",
        )
    else:
        flash(f"Échec sync IMAP : {result.get('error', 'erreur inconnue')}", "error")
    return redirect(url_for("admin.emails", box="inbox"))


@admin_bp.route("/emails/attachment/<storage_key>")
@admin_required
def email_attachment(storage_key):
    path = imap_mailbox.get_attachment_path(storage_key)
    if not path:
        abort(404)
    from flask import send_file

    download_name = storage_key
    row = EmailMessage.query.filter(EmailMessage.attachments_json.contains(storage_key)).first()
    if row:
        for att in row.attachments():
            if att.get("storage_key") == storage_key:
                download_name = att.get("filename") or download_name
                break
    return send_file(path, as_attachment=True, download_name=download_name)


@admin_bp.route("/emails/send", methods=["POST"])
@admin_required
def emails_send():
    to_addr = request.form.get("to", "").strip()
    cc_addrs = request.form.get("cc", "").strip() or None
    subject = request.form.get("subject", "").strip()
    body = request.form.get("body", "")
    is_html = request.form.get("is_html") == "on"
    if not to_addr or not subject:
        flash("Destinataire et objet obligatoires.", "error")
        return redirect(url_for("admin.emails", box="outbox"))
    msg = admin_email.send_email(
        to_addr, subject, body, is_html=is_html, cc_addrs=cc_addrs
    )
    flash(f"Email {msg.status} → {to_addr}.", "success")
    return redirect(url_for("admin.email_detail", message_id=msg.id))


@admin_bp.route("/email/inbound", methods=["POST"])
def email_inbound():
    """Provider webhook (Mailgun/SendGrid inbound parse). Public but guarded by
    EMAIL_INBOUND_SECRET when set (?secret= or X-Inbound-Secret header)."""
    secret = current_app.config.get("EMAIL_INBOUND_SECRET")
    if not secret:
        if current_app.config.get("ENV") == "production":
            abort(503)
    else:
        provided = request.args.get("secret") or request.headers.get("X-Inbound-Secret", "")
        if provided != secret:
            abort(401)
    data = request.form if request.form else (request.get_json(silent=True) or {})
    admin_email.store_inbound(
        from_addr=data.get("from") or data.get("sender"),
        to_addr=data.get("to") or data.get("recipient"),
        subject=data.get("subject"),
        body=data.get("body-plain") or data.get("text") or data.get("body"),
        html_body=data.get("body-html") or data.get("html"),
        is_html=bool(data.get("body-html") or data.get("html")),
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


# ============================================================ CONTENT STUDIO
import re as _re


def _slugify(value):
    value = (value or "").strip().lower()
    value = _re.sub(r"[^a-z0-9\s-]", "", value)
    value = _re.sub(r"[\s-]+", "-", value).strip("-")
    return value or "page"


def _unique_slug(base, exclude_id=None):
    slug = base
    i = 2
    # no_autoflush: a pending (not-yet-persisted) SitePage would otherwise be
    # flushed by this query while its slug is still unset, tripping NOT NULL.
    with db.session.no_autoflush:
        while True:
            existing = SitePage.query.filter(SitePage.slug == slug).first()
            if existing is None or existing.id == exclude_id:
                return slug
            slug = f"{base}-{i}"
            i += 1


# ------------------------------------------------------------------ studio hub
@admin_bp.route("/studio")
@admin_required
def studio():
    return render_template(
        "admin/studio.html",
        page_count=SitePage.query.count(),
        published_count=SitePage.query.filter(SitePage.status == "published").count(),
        offer_count=Offer.query.count(),
        social_count=SocialPost.query.count(),
        facebook_connected=social.is_configured(),
        ai_available=content_ai.is_available(),
    )


# ------------------------------------------------------------------ offers
@admin_bp.route("/offers")
@admin_required
def offers():
    return render_template(
        "admin/offers.html",
        offers=content_studio.get_offers(),
    )


@admin_bp.route("/offers/save", methods=["POST"])
@admin_required
def offers_save():
    offers_list = content_studio.get_offers()
    featured_key = request.form.get("featured_key", "")
    for offer in offers_list:
        prefix = f"o_{offer.key}_"
        offer.name = request.form.get(prefix + "name", offer.name).strip()
        offer.badge = request.form.get(prefix + "badge", "").strip()
        offer.price = request.form.get(prefix + "price", offer.price).strip()
        offer.period = request.form.get(prefix + "period", "").strip()
        offer.calls = request.form.get(prefix + "calls", "").strip()
        offer.description = request.form.get(prefix + "description", "").strip()
        offer.cta = request.form.get(prefix + "cta", "").strip()
        offer.active = request.form.get(prefix + "active") == "on"
        offer.featured = (offer.key == featured_key)
        features_raw = request.form.get(prefix + "features", "")
        offer.set_features([ln for ln in features_raw.splitlines()])
    try:
        db.session.commit()
        log_event(CAT_ADMIN, "offers_update", summary="Offres / prix mis à jour", level=LEVEL_SUCCESS)
        flash("Offres mises à jour — visibles immédiatement sur la page d'accueil.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Erreur: {exc}", "error")
    return redirect(url_for("admin.offers"))


# ------------------------------------------------------------------ pages
@admin_bp.route("/pages")
@admin_required
def pages():
    all_pages = SitePage.query.order_by(SitePage.updated_at.desc()).all()
    return render_template("admin/pages.html", pages=all_pages)


@admin_bp.route("/pages/new")
@admin_required
def page_new():
    return render_template("admin/page_editor.html", page=None, ai_available=content_ai.is_available())


@admin_bp.route("/pages/<page_id>")
@admin_required
def page_edit(page_id):
    page = db.session.get(SitePage, _pk_value(SitePage, page_id))
    if not page:
        abort(404)
    return render_template("admin/page_editor.html", page=page, ai_available=content_ai.is_available())


@admin_bp.route("/pages/save", methods=["POST"])
@admin_required
def page_save():
    page_id = request.form.get("id") or None
    page = None
    if page_id:
        page = db.session.get(SitePage, _pk_value(SitePage, page_id))
        if not page:
            abort(404)
    title = request.form.get("title", "").strip() or "Sans titre"
    slug_input = request.form.get("slug", "").strip()
    base_slug = _slugify(slug_input or title)
    is_new = page is None
    if is_new:
        page = SitePage()
        db.session.add(page)
    page.title = title
    page.slug = _unique_slug(base_slug, exclude_id=None if is_new else page.id)
    page.meta_description = request.form.get("meta_description", "").strip()[:300]
    page.body_html = request.form.get("body_html", "")
    if request.form.get("publish") == "on":
        page.status = "published"
    elif request.form.get("status") in ("draft", "published"):
        page.status = request.form.get("status")
    try:
        db.session.commit()
        log_event(CAT_ADMIN, "page_save",
                  summary=f"Page « {page.title} » enregistrée ({page.status})", level=LEVEL_SUCCESS)
        flash("Page enregistrée.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Erreur: {exc}", "error")
        return redirect(url_for("admin.pages"))
    return redirect(url_for("admin.page_edit", page_id=page.id))


@admin_bp.route("/pages/<page_id>/status", methods=["POST"])
@admin_required
def page_status(page_id):
    page = db.session.get(SitePage, _pk_value(SitePage, page_id))
    if not page:
        abort(404)
    page.status = "published" if page.status != "published" else "draft"
    db.session.commit()
    log_event(CAT_ADMIN, "page_status", summary=f"Page « {page.title} » → {page.status}")
    flash(f"Page {'publiée' if page.status == 'published' else 'repassée en brouillon'}.", "success")
    return redirect(request.referrer or url_for("admin.pages"))


@admin_bp.route("/pages/<page_id>/delete", methods=["POST"])
@admin_required
def page_delete(page_id):
    page = db.session.get(SitePage, _pk_value(SitePage, page_id))
    if not page:
        abort(404)
    title = page.title
    db.session.delete(page)
    db.session.commit()
    log_event(CAT_ADMIN, "page_delete", summary=f"Page « {title} » supprimée", level=LEVEL_WARNING)
    flash("Page supprimée.", "success")
    return redirect(url_for("admin.pages"))


@admin_bp.route("/pages/<page_id>/preview")
@admin_required
def page_preview(page_id):
    page = db.session.get(SitePage, _pk_value(SitePage, page_id))
    if not page:
        abort(404)
    return render_template("public/site_page.html", page=page, preview=True)


@admin_bp.route("/api/pages/generate", methods=["POST"])
@admin_required
def api_pages_generate():
    data = request.get_json(silent=True) or {}
    prompt = (data.get("prompt") or "").strip()
    tone = (data.get("tone") or "professionnel").strip()
    if not prompt:
        return jsonify({"error": "Décrivez la page à générer."}), 400
    try:
        result = content_ai.generate_page(prompt, tone)
        log_event(CAT_ADMIN, "page_ai_generate", summary=f"Page générée par IA: {prompt[:80]}")
        return jsonify(result)
    except content_ai.ContentAIError as exc:
        return jsonify({"error": str(exc)}), 502


# ------------------------------------------------------------------ social
@admin_bp.route("/social", endpoint="social")
@admin_required
def social_page():
    cfg = social.get_config()
    return render_template(
        "admin/social.html",
        posts=social.recent_posts(),
        facebook_connected=social.is_configured(),
        facebook_config=cfg,
        ai_available=content_ai.is_available(),
    )


@admin_bp.route("/social/connect", methods=["POST"])
@admin_required
def social_connect():
    page_id = request.form.get("page_id", "").strip()
    token = request.form.get("token", "").strip()
    if not page_id or not token:
        flash("Identifiant de page et token requis.", "error")
        return redirect(url_for("admin.social"))
    social.save_connection(page_id, token)
    ok, message = social.verify_connection()
    if ok:
        flash(f"Page Facebook « {message} » connectée.", "success")
        log_event(CAT_ADMIN, "facebook_connect", summary=f"Page Facebook connectée: {message}", level=LEVEL_SUCCESS)
    else:
        flash(f"Connexion enregistrée mais vérification échouée : {message}", "error")
    return redirect(url_for("admin.social"))


@admin_bp.route("/social/disconnect", methods=["POST"])
@admin_required
def social_disconnect():
    social.disconnect()
    flash("Page Facebook déconnectée.", "success")
    return redirect(url_for("admin.social"))


@admin_bp.route("/social/publish", methods=["POST"])
@admin_required
def social_publish():
    message = request.form.get("message", "").strip()
    link = request.form.get("link", "").strip()
    ai_flag = request.form.get("generated_by_ai") == "1"
    if not message:
        flash("Le message ne peut pas être vide.", "error")
        return redirect(url_for("admin.social"))
    post = social.publish_post(message, link=link, generated_by_ai=ai_flag)
    if post.status == "published":
        flash("Post publié sur Facebook 🎉", "success")
    else:
        flash(f"Échec de la publication : {post.error}", "error")
    return redirect(url_for("admin.social"))


@admin_bp.route("/api/social/generate", methods=["POST"])
@admin_required
def api_social_generate():
    data = request.get_json(silent=True) or {}
    prompt = (data.get("prompt") or "").strip()
    tone = (data.get("tone") or "engageant").strip()
    if not prompt:
        return jsonify({"error": "Décrivez le sujet du post."}), 400
    try:
        message = content_ai.generate_social_post(prompt, tone)
        log_event(CAT_ADMIN, "social_ai_generate", summary=f"Post généré par IA: {prompt[:80]}")
        return jsonify({"message": message})
    except content_ai.ContentAIError as exc:
        return jsonify({"error": str(exc)}), 502
