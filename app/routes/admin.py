"""Admin console (/admin) — analytics, database editor, email center and event
log. Fully separate from the artisan-facing app: its own auth, templates and
static assets.
"""
import secrets
import uuid
import logging
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
    session,
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
from app.services import (
    admin_email,
    analytics,
    content_ai,
    content_studio,
    diagnostics,
    google_gsc,
    imap_mailbox,
    social,
    traffic,
    twilio_admin,
)
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
        PageView, "Pages vues", ["path", "referrer_host", "device", "geo_city",
                                 "geo_postal_code", "utm_source", "utm_campaign"]),
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


# ------------------------------------------------------------------ GSC (Google Search Console)
@admin_bp.route("/gsc")
@admin_required
def gsc_page():
    gsc_status = google_gsc.status()
    dashboard = {
        "sites": [],
        "site_url": None,
        "summary": None,
        "queries": [],
        "pages": [],
        "error": None,
    }
    if google_gsc.is_connected():
        try:
            dashboard = google_gsc.dashboard_payload()
        except google_gsc.GscError as exc:
            dashboard["error"] = str(exc)
    return render_template("admin/gsc.html", gsc=gsc_status, dashboard=dashboard)


@admin_bp.route("/gsc/connect")
@admin_required
def gsc_connect():
    if not google_gsc.is_configured():
        flash("Configurez GOOGLE_GSC_CLIENT_ID et GOOGLE_GSC_CLIENT_SECRET.", "error")
        return redirect(url_for("admin.gsc_page"))
    state = secrets.token_urlsafe(32)
    oauth_redirect_uri = google_gsc.redirect_uri()
    session["gsc_oauth_state"] = state
    session["gsc_oauth_redirect_uri"] = oauth_redirect_uri
    return redirect(google_gsc.build_auth_url(state, oauth_redirect_uri=oauth_redirect_uri))


@admin_bp.route("/gsc/callback")
@admin_required
def gsc_callback():
    oauth_error = request.args.get("error")
    if oauth_error:
        if oauth_error == "access_denied":
            flash(
                "Google a refusé l'accès (403 access_denied). Votre appli OAuth est probablement "
                "en mode « Test » : ajoutez votre adresse Gmail dans Google Cloud Console → "
                "APIs & Services → OAuth consent screen → Test users, puis réessayez.",
                "error",
            )
        else:
            flash(f"Connexion Google refusée : {oauth_error}", "error")
        return redirect(url_for("admin.gsc_page"))

    state = request.args.get("state")
    if not state or state != session.pop("gsc_oauth_state", None):
        flash("État OAuth invalide — réessayez la connexion.", "error")
        return redirect(url_for("admin.gsc_page"))

    code = request.args.get("code")
    if not code:
        flash("Code d'autorisation Google manquant.", "error")
        return redirect(url_for("admin.gsc_page"))

    try:
        google_gsc.exchange_code(
            code,
            oauth_redirect_uri=session.pop("gsc_oauth_redirect_uri", None),
        )
    except google_gsc.GscError as exc:
        flash(f"Échec de la connexion Search Console : {exc}", "error")
        return redirect(url_for("admin.gsc_page"))
    except Exception:
        logging.getLogger(__name__).exception("GSC OAuth callback failed")
        flash(
            "Erreur interne lors de la connexion Search Console. Vérifiez PUBLIC_BASE_URL "
            "et l'URI de redirection Google, puis réessayez.",
            "error",
        )
        return redirect(url_for("admin.gsc_page"))

    flash("Google Search Console connecté.", "success")
    log_event(
        CAT_ADMIN,
        "gsc_connect",
        summary="Google Search Console connecté",
        level=LEVEL_SUCCESS,
    )
    return redirect(url_for("admin.gsc_page"))


@admin_bp.route("/gsc/disconnect", methods=["POST"])
@admin_required
def gsc_disconnect():
    google_gsc.disconnect()
    flash("Search Console déconnecté.", "success")
    return redirect(url_for("admin.gsc_page"))


@admin_bp.route("/gsc/site", methods=["POST"])
@admin_required
def gsc_select_site():
    site_url = (request.form.get("site_url") or "").strip()
    if not site_url:
        flash("Sélectionnez une propriété Search Console.", "error")
        return redirect(url_for("admin.gsc_page"))
    google_gsc.set_site_url(site_url)
    flash(f"Propriété active : {site_url}", "success")
    return redirect(url_for("admin.gsc_page"))


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


@admin_bp.route("/maintenance/purge-bot-views", methods=["POST"])
@admin_required
def purge_bot_views():
    """Remove page-view rows left by bots/tools so analytics only shows humans.

    Newer traffic is already filtered at write time; this cleans the history
    recorded before the detection was tightened, by re-scanning stored
    user-agents with the same :func:`is_bot` heuristic.
    """
    from app.core.tracking import is_bot

    try:
        uas = [row[0] for row in db.session.query(PageView.user_agent).distinct().all()]
        bot_uas = [u for u in uas if u and is_bot(u)]
        deleted = PageView.query.filter(PageView.user_agent.is_(None)).delete(
            synchronize_session=False
        )
        # Delete in chunks to keep the IN clause reasonable.
        for i in range(0, len(bot_uas), 100):
            chunk = bot_uas[i : i + 100]
            deleted += PageView.query.filter(PageView.user_agent.in_(chunk)).delete(
                synchronize_session=False
            )
        db.session.commit()
        log_event(CAT_ADMIN, "purge_bot_views",
                  summary=f"Purge robots: {deleted} vue(s) supprimée(s)", level=LEVEL_WARNING)
        flash(f"{deleted} vue(s) de robots supprimée(s) des statistiques.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Erreur pendant la purge des robots : {exc}", "error")
    return redirect(url_for("admin.traffic_page"))


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


# ------------------------------------------------------------------ clients / accounts
@admin_bp.route("/clients")
@admin_required
def clients():
    """Particuliers (customers) and artisans (tenants) — unified accounts view."""
    from app.constants.trades import trade_label

    tab = request.args.get("tab", "particuliers")
    if tab not in ("particuliers", "artisans"):
        tab = "particuliers"
    q = request.args.get("q", "").strip()

    total_customers = User.query.filter(User.role == "customer").count()
    total_artisans = Tenant.query.count()
    total_public_artisans = Tenant.query.filter(Tenant.is_public.is_(True)).count()
    total_leads = Lead.query.count()

    customers = []
    artisans = []

    if tab == "particuliers":
        query = User.query.filter(User.role == "customer")
        if q:
            like = f"%{q}%"
            query = query.filter(
                or_(
                    User.email.ilike(like),
                    User.first_name.ilike(like),
                    User.last_name.ilike(like),
                    User.phone.ilike(like),
                )
            )
        for c in query.order_by(User.created_at.desc()).limit(500).all():
            booking_count = Lead.query.filter(Lead.email == c.email).count()
            customers.append(
                {
                    "id": str(c.id),
                    "name": c.full_name or "—",
                    "email": c.email,
                    "phone": c.phone or "—",
                    "bookings": booking_count,
                    "created_at": c.created_at,
                }
            )
    else:
        query = Tenant.query
        if q:
            like = f"%{q}%"
            query = query.filter(
                or_(
                    Tenant.name.ilike(like),
                    Tenant.city.ilike(like),
                    Tenant.postal_code.ilike(like),
                    Tenant.public_slug.ilike(like),
                    Tenant.phone_number.ilike(like),
                    Tenant.ai_phone_number.ilike(like),
                )
            )
        for t in query.order_by(Tenant.created_at.desc()).limit(500).all():
            admin_user = (
                User.query.filter(User.tenant_id == t.id, User.role == "admin")
                .order_by(User.created_at.asc())
                .first()
            )
            lead_count = Lead.query.filter(Lead.tenant_id == t.id).count()
            artisans.append(
                {
                    "id": str(t.id),
                    "name": t.name,
                    "trade": trade_label(t.trade_type, "fr"),
                    "city": t.city or "—",
                    "email": admin_user.email if admin_user else "—",
                    "phone": t.ai_phone_number or t.phone_number or "—",
                    "plan": t.plan or "—",
                    "is_public": t.is_public,
                    "public_slug": t.public_slug,
                    "leads": lead_count,
                    "created_at": t.created_at,
                }
            )

    return render_template(
        "admin/clients.html",
        tab=tab,
        q=q,
        customers=customers,
        artisans=artisans,
        total_customers=total_customers,
        total_artisans=total_artisans,
        total_public_artisans=total_public_artisans,
        total_leads=total_leads,
    )


# Legacy alias kept for bookmarks
@admin_bp.route("/clients/")
@admin_required
def clients_redirect():
    return redirect(url_for("admin.clients", **request.args))


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


@admin_bp.route("/twilio")
@admin_required
def twilio_page():
    """Twilio balance, usage and billing console links."""
    status = twilio_admin.collect_status()
    return render_template("admin/twilio.html", twilio=status)


@admin_bp.route("/diagnostics")
@admin_required
def diagnostics_page():
    """System diagnostics — Scalingo variables & integration status."""
    groups = diagnostics.collect()
    return render_template(
        "admin/diagnostics.html",
        groups=groups,
        summary=diagnostics.summary(groups),
        smtp_configured=admin_email.is_configured(),
        imap_configured=imap_mailbox.is_configured(),
        default_from=admin_email.default_from_addr(),
        admin_email_hint=current_app.config.get("EMAIL_FROM") or "",
    )


@admin_bp.route("/diagnostics/smtp-test", methods=["POST"])
@admin_required
def diagnostics_smtp_test():
    """Live SMTP connect + login probe (no message sent)."""
    result = admin_email.smtp_test()
    if result.get("ok"):
        flash(f"SMTP OK — {result.get('detail')}", "success")
    else:
        flash(f"SMTP KO — {result.get('detail')}", "error")
    return redirect(url_for("admin.diagnostics_page"))


@admin_bp.route("/diagnostics/db-test", methods=["POST"])
@admin_required
def diagnostics_db_test():
    """Live database connectivity probe."""
    result = diagnostics.database_probe()
    if result.get("ok"):
        flash(f"Base de données OK — {result.get('detail')}", "success")
    else:
        flash(f"Base de données KO — {result.get('detail')}", "error")
    return redirect(url_for("admin.diagnostics_page"))


@admin_bp.route("/diagnostics/test-email", methods=["POST"])
@admin_required
def diagnostics_test_email():
    """Send a real branded test email end-to-end and report the result."""
    to_addr = (request.form.get("to") or "").strip()
    if not to_addr:
        flash("Indiquez une adresse de destination pour le test.", "error")
        return redirect(url_for("admin.diagnostics_page"))

    from app.services.transactional_email import render_email

    html = render_email(
        "Test d'envoi PilotCore ✅",
        "Ceci est un e-mail de test.",
        lines=[
            "Si vous recevez ce message, la configuration SMTP de PilotCore "
            "fonctionne : les e-mails transactionnels seront bien délivrés.",
        ],
        outro="Envoyé depuis /admin/diagnostics.",
    )
    msg = admin_email.send_email(
        to_addr=to_addr,
        subject="Test d'envoi PilotCore",
        body="Ceci est un e-mail de test PilotCore. La configuration SMTP fonctionne.",
        is_html=True,
        html_body=html,
    )
    if msg.status == "sent":
        flash(f"Email de test envoyé à {to_addr} (statut : {msg.status}).", "success")
    elif msg.status == "simulated":
        flash(
            f"Email SIMULÉ (statut : {msg.status}) — SMTP non configuré, rien n'a été "
            "réellement envoyé. Renseignez les variables SMTP_* sur Scalingo.",
            "error",
        )
    else:
        flash(
            f"Échec de l'envoi (statut : {msg.status}) — {msg.error or 'voir le journal'}.",
            "error",
        )
    return redirect(url_for("admin.diagnostics_page"))


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
    from app.services.social_links import targets_for_admin

    cfg = social.get_config()
    return render_template(
        "admin/social.html",
        posts=social.recent_posts(),
        facebook_connected=social.is_configured(),
        facebook_config=cfg,
        ai_available=content_ai.is_available(),
        link_targets=targets_for_admin(),
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
    from app.services.social_links import display_url, ensure_tracked

    message = request.form.get("message", "").strip()
    link = request.form.get("link", "").strip()
    target_key = (request.form.get("target_key") or "").strip() or None
    image_path = (request.form.get("image_path") or "").strip() or None
    ai_flag = request.form.get("generated_by_ai") == "1"
    content_tag = "ai_post" if ai_flag else "manual_post"
    if not message:
        flash("Le message ne peut pas être vide.", "error")
        return redirect(url_for("admin.social"))
    if not image_path:
        try:
            from app.services import social_image

            generated = social_image.generate_for_post(message[:500], tone="engageant")
            image_path = generated["image_path"]
        except content_ai.ContentAIError as exc:
            flash(f"Impossible de créer le visuel : {exc}", "error")
            return redirect(url_for("admin.social"))
    tracked_link = ensure_tracked(link, target_key=target_key, content=content_tag)
    post = social.publish_post(
        message,
        link=tracked_link,
        generated_by_ai=ai_flag,
        image_path=image_path,
    )
    if post.status == "published":
        shown = display_url(tracked_link) if tracked_link else ""
        flash(
            f"Post publié sur Facebook 🎉"
            + (f" — lien tracké : {shown}" if shown else ""),
            "success",
        )
    else:
        flash(f"Échec de la publication : {post.error}", "error")
    return redirect(url_for("admin.social"))


@admin_bp.route("/api/social/generate", methods=["POST"])
@admin_required
def api_social_generate():
    data = request.get_json(silent=True) or {}
    prompt = (data.get("prompt") or "").strip()
    tone = (data.get("tone") or "engageant").strip()
    target_key = (data.get("target_key") or "home").strip()
    if not prompt:
        return jsonify({"error": "Décrivez le sujet du post."}), 400
    try:
        payload = content_ai.generate_social_post(
            prompt,
            tone,
            target_key=target_key,
            content_tag="ai_post",
        )
        from app.services import social_image

        payload.update(social_image.generate_for_post(prompt, tone))
        log_event(CAT_ADMIN, "social_ai_generate", summary=f"Post généré par IA: {prompt[:80]}")
        return jsonify(payload)
    except content_ai.ContentAIError as exc:
        return jsonify({"error": str(exc)}), 502
