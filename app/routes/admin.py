"""Admin console (/admin) — analytics, database editor, email center and event
log. Fully separate from the artisan-facing app: its own auth, templates and
static assets.
"""
import hmac
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
from app.models.email_campaign import CampaignRecipient, EmailCampaign
from app.models.email_message import EmailMessage
from app.models.event import Event
from app.models.lead import Lead
from app.models.notification import Notification
from app.models.outreach_prospect import OutreachProspect
from app.models.offer import Offer
from app.models.page_view import PageView
from app.models.quote import Quote
from app.models.blog_category import BlogCategory
from app.models.blog_post import BlogPost
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
    linkedin_social,
    social,
    traffic,
    twilio_admin,
)
from app.services.events import CAT_ADMIN, CAT_AUTH, LEVEL_ERROR, LEVEL_SUCCESS, LEVEL_WARNING, log_event

logger = logging.getLogger(__name__)

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
                                 "subject", "open_count", "click_count", "body"]),
    "page_views": TableSpec(
        PageView, "Pages vues", ["path", "referrer_host", "device", "geo_city",
                                 "geo_postal_code", "utm_source", "utm_campaign"]),
    "outreach_prospects": TableSpec(
        OutreachProspect, "Prospection B2B",
        ["first_name", "last_name", "company_name", "email", "phone", "trade_type",
         "city", "status", "notes"]),
    "email_campaigns": TableSpec(
        EmailCampaign, "Campagnes e-mail",
        ["name", "subject", "status", "scheduled_at", "started_at", "finished_at"]),
    "campaign_recipients": TableSpec(
        CampaignRecipient, "Destinataires de campagne",
        ["campaign_id", "email", "company_name", "city", "status", "sent_at", "error"]),
}


def _ping_indexnow(*paths: str) -> None:
    """Nudge IndexNow so Bing/Yandex fetch a freshly published URL in minutes
    rather than on their next organic crawl. Best-effort by design: a search
    engine being unreachable must never turn a successful publish into an error
    for the editor."""
    try:
        from app.services import indexnow

        indexnow.submit([p for p in paths if p])
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).exception("IndexNow ping failed")


@admin_bp.context_processor
def inject_admin():
    nova_available = False
    if is_admin_logged_in():
        try:
            from app.services import assistant

            nova_available = assistant.available()
        except Exception:  # noqa: BLE001 — never break page render over the copilot
            nova_available = False
    pending_claims = 0
    if is_admin_logged_in():
        try:
            from app.services.listing_claims import pending_count

            pending_claims = pending_count()
        except Exception:  # noqa: BLE001 — a badge must never break the layout
            pending_claims = 0
    return {
        "admin_username": g.get("admin_username"),
        "is_admin": is_admin_logged_in(),
        "admin_tables": {k: v.label for k, v in TABLES.items()},
        "current_year": datetime.now(timezone.utc).year,
        "nova_available": nova_available,
        "nova_name": "Nova",
        "pending_claims": pending_claims,
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


# ------------------------------------------------------------------ Nova (AI copilot)
@admin_bp.route("/api/assistant/chat", methods=["POST"])
@admin_required
@rate_limit(limit=40, window=300, scope="admin_assistant")
def api_assistant_chat():
    from app.services import assistant

    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    history = data.get("history") if isinstance(data.get("history"), list) else []
    if not message:
        return jsonify({"error": "Message vide."}), 400
    try:
        result = assistant.chat(message, history=history)
        return jsonify(result)
    except assistant.AssistantError as exc:
        return jsonify({"error": str(exc)}), 503
    except Exception as exc:  # noqa: BLE001 — never 500 the chat widget
        current_app.logger.exception("assistant chat failed")
        return jsonify({"error": f"Nova a rencontré une erreur : {exc}"}), 502


@admin_bp.route("/api/assistant/insights")
@admin_required
def api_assistant_insights():
    from app.services import assistant

    try:
        return jsonify(assistant.insights())
    except Exception as exc:  # noqa: BLE001
        current_app.logger.exception("assistant insights failed")
        return jsonify({"available": False, "headline": "", "insights": [], "error": str(exc)[:200]})


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


# ------------------------------------------------------------------ heatmap
@admin_bp.route("/heatmap")
@admin_required
def heatmap_page():
    return render_template("admin/heatmap.html")


@admin_bp.route("/api/heatmap/overview")
@admin_required
def api_heatmap_overview():
    from app.services import heatmap as heatmap_service

    return jsonify(heatmap_service.overview(_range_days()))


@admin_bp.route("/api/heatmap/points")
@admin_required
def api_heatmap_points():
    from app.services import heatmap as heatmap_service

    path = request.args.get("path", "/")
    return jsonify(heatmap_service.clicks_for_path(path, _range_days()))


@admin_bp.route("/api/heatmap/journeys")
@admin_required
def api_heatmap_journeys():
    from app.services import heatmap as heatmap_service

    return jsonify({"journeys": heatmap_service.journeys(_range_days())})


@admin_bp.route("/api/heatmap/journey/<visitor_id>")
@admin_required
def api_heatmap_journey(visitor_id):
    from app.services import heatmap as heatmap_service

    return jsonify(heatmap_service.journey_detail(visitor_id))


@admin_bp.route("/api/heatmap/recordings")
@admin_required
def api_heatmap_recordings():
    from app.services import heatmap as heatmap_service

    return jsonify({"recordings": heatmap_service.recordings(_range_days())})


@admin_bp.route("/api/heatmap/recording/<rec_id>")
@admin_required
def api_heatmap_recording(rec_id):
    from app.services import heatmap as heatmap_service

    detail = heatmap_service.recording_detail(rec_id)
    if detail is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(detail)


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
            days = int(request.args.get("days") or 28)
        except (TypeError, ValueError):
            days = 28
        try:
            dashboard = google_gsc.dashboard_payload(days)
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


@admin_bp.route("/clients/artisans/<tenant_id>/delete", methods=["POST"])
@admin_required
def delete_tenant(tenant_id):
    """Delete a single artisan account (tenant) and all its dependent rows.

    Deletes in FK-safe order — appointments/quotes reference leads, everything
    references tenants — inside one transaction. Other accounts are untouched.
    The typed confirmation must match the account name to avoid mistakes.
    """
    tenant = db.session.get(Tenant, _pk_value(Tenant, tenant_id))
    if not tenant:
        abort(404)

    confirm = (request.form.get("confirm") or "").strip()
    if confirm.lower() != (tenant.name or "").strip().lower():
        flash(
            "Confirmation incorrecte — retapez le nom exact du compte pour "
            "confirmer la suppression.",
            "error",
        )
        return redirect(url_for("admin.clients", tab="artisans"))

    name = tenant.name
    tid = tenant.id
    try:
        from app.services.twilio_provisioning import release_ai_number

        if not release_ai_number(tenant):
            flash(
                "Compte supprimé côté PilotCore, mais Twilio n'a pas libéré le "
                "numéro (compte suspendu ?). Libérez-le dans la console Twilio "
                "sinon il continue d'être facturé.",
                "error",
            )
    except Exception:
        logger.exception("Twilio release before tenant delete failed for %s", tid)
    # tenant_id-scoped models, ordered so children (appointments/quotes ->
    # leads) go before leads, and everything before the tenant row itself.
    # ORM deletes bind the Uuid column type correctly on both Postgres (prod)
    # and SQLite (tests), unlike raw text SQL.
    scoped = [
        ("appointments", Appointment),
        ("quotes", Quote),
        ("notifications", Notification),
        ("users", User),
        ("email_messages", EmailMessage),
        ("events", Event),
        ("leads", Lead),
    ]
    try:
        counts = {}
        for label, model in scoped:
            counts[label] = (
                model.query.filter(model.tenant_id == tid).delete(
                    synchronize_session=False
                )
            )
        db.session.delete(tenant)
        db.session.commit()
        summary = ", ".join(f"{n} {t}" for t, n in counts.items() if n)
        log_event(
            CAT_ADMIN,
            "tenant_delete",
            summary=f"Compte « {name} » supprimé ({summary or 'aucune donnée liée'})",
            level=LEVEL_WARNING,
        )
        flash(f"Compte « {name} » supprimé, avec toutes ses données liées.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Erreur pendant la suppression : {exc}", "error")
    return redirect(url_for("admin.clients", tab="artisans"))


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
    from app.services.email_tracking import outbound_stats

    return render_template(
        "admin/emails.html",
        messages=messages,
        box=box,
        q=q,
        unread=unread,
        mail_stats=outbound_stats() if box in ("outbox", "all") else None,
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
    from app.services.email_tracking import format_rate

    unique_open = 1 if msg.was_opened else 0
    unique_click = 1 if msg.was_clicked else 0
    return render_template(
        "admin/email_detail.html",
        message=msg,
        thread=thread,
        attachments=msg.attachments(),
        smtp_configured=admin_email.is_configured(),
        open_rate=format_rate(unique_open, 1) if msg.track_token else None,
        click_rate=format_rate(unique_click, 1) if msg.track_token else None,
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


@admin_bp.route("/emails/process-bounces", methods=["POST"])
@admin_required
def emails_process_bounces():
    """Read delivery reports sitting in the inbox and quarantine dead addresses."""
    from app.services import bounce_processing

    try:
        result = bounce_processing.process_bounces()
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        current_app.logger.exception("bounce processing failed")
        flash(f"Traitement des rebonds impossible : {exc}", "error")
        return redirect(url_for("admin.emails", box="inbox"))

    if result["marked"]:
        log_event(
            CAT_ADMIN,
            "bounces_processed",
            summary=f"{result['marked']} adresse(s) retirée(s) après rebond définitif",
            level=LEVEL_WARNING,
        )
        flash(
            f"{result['reports']} rapport(s) lus — {result['marked']} adresse(s) "
            f"retirée(s) des envois ({result['already_marked']} déjà traitées, "
            f"{result['temporary_ignored']} rebond(s) temporaire(s) ignoré(s)).",
            "success",
        )
    else:
        flash(
            f"{result['reports']} rapport(s) lus — aucune nouvelle adresse à retirer "
            f"({result['already_marked']} déjà traitées).",
            "success",
        )
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


@admin_bp.route("/diagnostics/voice-routing-test", methods=["POST"])
@admin_required
def diagnostics_voice_routing_test():
    """Check the phone numbers still point at tenants that exist."""
    result = diagnostics.voice_routing_probe()
    if result.get("ok"):
        flash(
            f"Routage vocal OK — {result.get('tenants_with_number', 0)} compte(s) "
            "avec un numéro dédié, numéro partagé rattaché.",
            "success",
        )
    else:
        for problem in result.get("problems", []):
            flash(f"Routage vocal KO — {problem}", "error")
    return redirect(url_for("admin.diagnostics_page"))


@admin_bp.route("/diagnostics/places-test", methods=["POST"])
@admin_required
def diagnostics_places_test():
    """Live Google Places round-trip (costs one billed request)."""
    result = diagnostics.places_probe()
    stats = result.get("stats") or {}
    if result.get("ok"):
        flash(
            f"Google Places OK — « {result.get('sample')} » "
            f"({result.get('count')} suggestions, {stats.get('errors', 0)} erreur(s) cumulée(s))",
            "success",
        )
    else:
        flash(f"Google Places KO — {result.get('reason')}", "error")
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
        "Test d'envoi PilotCore",
        "Ceci est un e-mail de test.",
        kicker="Diagnostics",
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
        if not hmac.compare_digest(provided, secret):
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


def _unique_blog_slug(base, exclude_id=None):
    slug = base
    i = 2
    with db.session.no_autoflush:
        while True:
            existing = BlogPost.query.filter(BlogPost.slug == slug).first()
            if existing is None or existing.id == exclude_id:
                return slug
            slug = f"{base}-{i}"
            i += 1


def _unique_category_slug(base, exclude_id=None):
    slug = base
    i = 2
    with db.session.no_autoflush:
        while True:
            existing = BlogCategory.query.filter(BlogCategory.slug == slug).first()
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
        blog_count=BlogPost.query.count(),
        blog_published_count=BlogPost.query.filter(BlogPost.status == "published").count(),
        offer_count=Offer.query.count(),
        social_count=SocialPost.query.count(),
        facebook_connected=social.is_configured(),
        linkedin_connected=linkedin_social.is_configured(),
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
    from app.constants.cities import TOP_CITIES
    from app.constants.trades import SEO_LOCAL_TRADES, TRADES, trade_icon, trade_label

    all_pages = SitePage.query.order_by(SitePage.updated_at.desc()).all()

    # Programmatic local-SEO pages (generated from routes, not stored in DB).
    # Surfaced here so the Pages tab shows the full public footprint at a glance.
    seo_local_trades = set(SEO_LOCAL_TRADES)
    seo_pages = []
    seo_city_count = 0
    for key, meta in TRADES.items():
        if key == "autre":
            continue
        cities = (
            [{"slug": s, "name": n} for s, n in TOP_CITIES]
            if key in seo_local_trades
            else []
        )
        seo_city_count += len(cities)
        seo_pages.append(
            {
                "key": key,
                "label": trade_label(key, "fr"),
                "icon": trade_icon(key),
                "pillar_path": f"/artisans/metier/{key}",
                "cities": cities,
            }
        )
    seo_total = len(seo_pages) + seo_city_count

    return render_template(
        "admin/pages.html",
        pages=all_pages,
        seo_pages=seo_pages,
        seo_total=seo_total,
        seo_city_count=seo_city_count,
    )


@admin_bp.route("/pages/new")
@admin_required
def page_new():
    return redirect(url_for("admin.blog_new"))


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


# ------------------------------------------------------------------ blog
@admin_bp.route("/blog")
@admin_required
def blog():
    from app.services import blog as blog_svc

    return render_template("admin/blog_posts.html", posts=blog_svc.admin_list_posts())


@admin_bp.route("/blog/generate-now", methods=["POST"])
@admin_required
@rate_limit(limit=6, window=3600, scope="admin_blog_generate")
def blog_generate_now():
    """Fire the daily blog auto-generator on demand (bypass cron schedule).

    Uses the same script as the cron so admin previews match production output.
    Blocks up to ~30 s while Mistral generates + DB writes — acceptable inside
    an admin form submit.
    """
    import subprocess
    import sys as _sys

    topic = (request.form.get("topic") or "").strip() or None
    args = [_sys.executable, "scripts/generate_daily_blog.py"]
    if topic:
        args.extend(["--topic", topic])
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=90)
    except subprocess.TimeoutExpired:
        flash("Génération trop lente (>90 s). Ré-essaie ou vérifie le quota Mistral.", "error")
        return redirect(url_for("admin.blog"))
    if proc.returncode == 0:
        flash("Article généré. Regarde la liste ci-dessous.", "success")
    else:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:] or ["erreur inconnue"]
        flash(f"Échec génération : {detail[-1][:180]}", "error")
    return redirect(url_for("admin.blog"))


@admin_bp.route("/blog/new")
@admin_required
def blog_new():
    from app.services import blog as blog_svc

    return render_template(
        "admin/blog_editor.html",
        post=None,
        categories=blog_svc.list_categories(),
        ai_available=content_ai.is_available(),
    )


@admin_bp.route("/blog/<post_id>")
@admin_required
def blog_edit(post_id):
    from app.services import blog as blog_svc

    post = db.session.get(BlogPost, _pk_value(BlogPost, post_id))
    if not post:
        abort(404)
    return render_template(
        "admin/blog_editor.html",
        post=post,
        categories=blog_svc.list_categories(),
        ai_available=content_ai.is_available(),
    )


@admin_bp.route("/blog/save", methods=["POST"])
@admin_required
def blog_save():
    import json as _json

    from app.services import blog as blog_svc

    post_id = request.form.get("id") or None
    post = None
    if post_id:
        post = db.session.get(BlogPost, _pk_value(BlogPost, post_id))
        if not post:
            abort(404)
    title = request.form.get("title", "").strip() or "Sans titre"
    slug_input = request.form.get("slug", "").strip()
    base_slug = _slugify(slug_input or title)
    is_new = post is None
    if is_new:
        post = BlogPost()
        db.session.add(post)
    post.title = title
    post.slug = _unique_blog_slug(base_slug, exclude_id=None if is_new else post.id)
    post.excerpt = request.form.get("excerpt", "").strip()[:400] or None
    post.meta_description = request.form.get("meta_description", "").strip()[:300] or None
    post.meta_keywords = request.form.get("meta_keywords", "").strip()[:400] or None
    post.body_html = request.form.get("body_html", "")
    post.featured = request.form.get("featured") == "on"
    try:
        rt = request.form.get("reading_time_min", "").strip()
        post.reading_time_min = int(rt) if rt else None
    except ValueError:
        post.reading_time_min = None
    cat_raw = request.form.get("category_id", "").strip()
    post.category_id = _pk_value(BlogCategory, cat_raw) if cat_raw else None
    faq_raw = request.form.get("faq_json", "").strip()
    if faq_raw:
        try:
            post.set_faq(_json.loads(faq_raw))
        except _json.JSONDecodeError:
            pass
    publishing = request.form.get("publish") == "on"
    if publishing:
        post.status = "published"
        blog_svc.touch_published_at(post, publishing=True)
    elif request.form.get("save_draft") == "1" or request.form.get("status") in ("draft", "published"):
        post.status = request.form.get("status") if request.form.get("status") in ("draft", "published") else "draft"
    try:
        db.session.commit()
        log_event(CAT_ADMIN, "blog_save", summary=f"Article « {post.title} » ({post.status})", level=LEVEL_SUCCESS)
        if post.status == "published":
            _ping_indexnow(f"/blog/{post.slug}")
        flash("Article enregistré.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Erreur: {exc}", "error")
        return redirect(url_for("admin.blog"))
    return redirect(url_for("admin.blog_edit", post_id=post.id))


@admin_bp.route("/blog/<post_id>/status", methods=["POST"])
@admin_required
def blog_status(post_id):
    from app.services import blog as blog_svc

    post = db.session.get(BlogPost, _pk_value(BlogPost, post_id))
    if not post:
        abort(404)
    publishing = post.status != "published"
    post.status = "published" if publishing else "draft"
    if publishing:
        blog_svc.touch_published_at(post, publishing=True)
    db.session.commit()
    if publishing:
        _ping_indexnow(f"/blog/{post.slug}")
    flash(f"Article {'publié' if post.status == 'published' else 'en brouillon'}.", "success")
    return redirect(request.referrer or url_for("admin.blog"))


@admin_bp.route("/blog/<post_id>/delete", methods=["POST"])
@admin_required
def blog_delete(post_id):
    post = db.session.get(BlogPost, _pk_value(BlogPost, post_id))
    if not post:
        abort(404)
    title = post.title
    db.session.delete(post)
    db.session.commit()
    log_event(CAT_ADMIN, "blog_delete", summary=f"Article « {title} » supprimé", level=LEVEL_WARNING)
    flash("Article supprimé.", "success")
    return redirect(url_for("admin.blog"))


@admin_bp.route("/blog/<post_id>/preview")
@admin_required
def blog_preview(post_id):
    from app.services import blog as blog_svc
    from app.utils.seo import blog_posting_json_ld, json_ld_script

    post = db.session.get(BlogPost, _pk_value(BlogPost, post_id))
    if not post:
        abort(404)
    body_html, toc = blog_svc.prepare_article_body(post.body_html or "")
    return render_template(
        "public/blog/article.html",
        post=post,
        body_html=body_html,
        toc=toc,
        related=[],
        preview=True,
        nav_active="blog",
        json_ld=json_ld_script(blog_posting_json_ld(post)),
    )


@admin_bp.route("/blog/categories")
@admin_required
def blog_categories():
    from app.services import blog as blog_svc

    blog_svc.ensure_blog_schema()
    return render_template(
        "admin/blog_categories.html",
        categories=blog_svc.list_categories(),
        post_counts=blog_svc.category_post_counts(),
    )


@admin_bp.route("/blog/categories/save", methods=["POST"])
@admin_required
def blog_category_save():
    action = request.form.get("action", "create")
    name = request.form.get("name", "").strip()
    if not name:
        flash("Le nom est requis.", "error")
        return redirect(url_for("admin.blog_categories"))
    slug_input = request.form.get("slug", "").strip()
    base_slug = _slugify(slug_input or name)
    description = request.form.get("description", "").strip()[:400] or None
    try:
        sort_order = int(request.form.get("sort_order") or 0)
    except ValueError:
        sort_order = 0

    if action == "update":
        cat_id = request.form.get("id")
        cat = db.session.get(BlogCategory, _pk_value(BlogCategory, cat_id))
        if not cat:
            abort(404)
        cat.name = name
        cat.slug = _unique_category_slug(base_slug, exclude_id=cat.id)
        cat.description = description
        cat.sort_order = sort_order
    else:
        cat = BlogCategory(
            name=name,
            slug=_unique_category_slug(base_slug),
            description=description,
            sort_order=sort_order,
        )
        db.session.add(cat)
    try:
        db.session.commit()
        flash("Catégorie enregistrée.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Erreur: {exc}", "error")
    return redirect(url_for("admin.blog_categories"))


@admin_bp.route("/blog/categories/<category_id>/delete", methods=["POST"])
@admin_required
def blog_category_delete(category_id):
    from app.services import blog as blog_svc

    cat = db.session.get(BlogCategory, _pk_value(BlogCategory, category_id))
    if not cat:
        abort(404)
    counts = blog_svc.category_post_counts()
    if counts.get(cat.id, 0) > 0:
        flash("Impossible de supprimer une catégorie qui contient des articles.", "error")
        return redirect(url_for("admin.blog_categories"))
    db.session.delete(cat)
    db.session.commit()
    flash("Catégorie supprimée.", "success")
    return redirect(url_for("admin.blog_categories"))


@admin_bp.route("/api/blog/generate", methods=["POST"])
@admin_required
def api_blog_generate():
    data = request.get_json(silent=True) or {}
    prompt = (data.get("prompt") or "").strip()
    tone = (data.get("tone") or "expert").strip()
    category_hint = (data.get("category_hint") or "").strip()
    if not prompt:
        return jsonify({"error": "Décrivez le sujet de l'article."}), 400
    try:
        result = content_ai.generate_blog_post(prompt, tone, category_hint=category_hint)
        log_event(CAT_ADMIN, "blog_ai_generate", summary=f"Article généré par IA: {prompt[:80]}")
        return jsonify(result)
    except content_ai.ContentAIError as exc:
        return jsonify({"error": str(exc)}), 502


# ------------------------------------------------------------------ social
@admin_bp.route("/social", endpoint="social")
@admin_required
def social_page():
    from app.services import social_autopost
    from app.services.social_links import targets_for_admin
    from app.services.social_schedule import slot_reason

    cfg = social.get_config()
    if social.is_configured():
        try:
            social.refresh_never_expiring_token()
        except Exception:
            logging.getLogger(__name__).exception("Facebook token refresh failed")
    fb_status = social.connection_status()
    groups, groups_error = social.list_member_groups() if social.is_configured() else ([], None)
    queued = social_autopost.queued_preview()
    queued_image = ""
    if queued:
        try:
            from app.services.social_image import ensure_post_visual

            ensure_post_visual(queued, subject=queued.message, theme=queued.target_key)
        except Exception:
            logging.getLogger(__name__).exception("Queued Facebook visual missing")
        queued_image = url_for("web.social_post_image", post_id=queued.id)
    health = fb_status.get("token_health") or {}
    expires_label = "inconnu"
    queued_when = ""
    queued_iso = ""
    queued_reason = ""
    if health.get("never_expires"):
        expires_label = "illimité (n'expire pas)"
    elif health.get("expires_at"):
        from datetime import datetime, timezone

        expires_label = datetime.fromtimestamp(int(health["expires_at"]), tz=timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    if queued and queued.scheduled_for:
        from datetime import timezone
        from zoneinfo import ZoneInfo

        due = queued.scheduled_for
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        queued_iso = due.isoformat()
        queued_when = due.astimezone(ZoneInfo("Europe/Paris")).strftime("%d/%m/%Y à %H:%M")
        queued_reason = slot_reason(due)
    fb_page_choices = []
    pending_token = social.stored_user_token()
    if pending_token:
        try:
            fb_page_choices = social.list_user_pages(pending_token)
        except Exception:
            logging.getLogger(__name__).exception("Facebook page listing failed")
    li_expires_label = "inconnu"
    li_status = {"connected": False, "message": "", "has_refresh": False, "has_token": False, "app_ready": False}
    li_org_choices = []
    li_list_error = None
    try:
        if linkedin_social.has_token():
            linkedin_social.ensure_access_token()
    except Exception:
        logging.getLogger(__name__).exception("LinkedIn token refresh failed")
    li_status = linkedin_social.connection_status()
    if li_status.get("has_refresh"):
        li_expires_label = "renouvelé automatiquement (~60 jours)"
    elif li_status.get("expires_at"):
        from datetime import datetime, timezone as tz

        li_expires_label = datetime.fromtimestamp(int(li_status["expires_at"]), tz=tz.utc).strftime("%d/%m/%Y %H:%M UTC")
    if linkedin_social.has_token() and not (linkedin_social.get_config() or {}).get("org_id"):
        try:
            li_org_choices, li_list_error = linkedin_social.list_admin_orgs()
        except Exception:
            logging.getLogger(__name__).exception("LinkedIn organization listing failed")
            li_list_error = "Impossible de lister les pages entreprise LinkedIn."
        if linkedin_social.is_configured():
            li_list_error = None
    return render_template(
        "admin/social.html",
        posts=social.recent_posts(),
        facebook_connected=social.is_configured(),
        facebook_config=cfg,
        facebook_status=fb_status,
        facebook_app_ready=social.app_credentials_ready(),
        facebook_oauth_redirect=social.facebook_oauth_redirect_uri(),
        facebook_default_page_id=social.DEFAULT_PAGE_ID,
        token_expires_label=expires_label,
        linkedin_connected=linkedin_social.is_configured(),
        linkedin_config=linkedin_social.get_config(),
        linkedin_status=li_status,
        linkedin_app_ready=linkedin_social.app_credentials_ready(),
        linkedin_oauth_redirect=linkedin_social.linkedin_oauth_redirect_uri(),
        linkedin_expires_label=li_expires_label,
        linkedin_org_choices=li_org_choices,
        linkedin_list_error=li_list_error,
        linkedin_verification_url=linkedin_social.APP_VERIFICATION_URL,
        ai_available=content_ai.is_available(),
        link_targets=targets_for_admin(),
        autopost=social_autopost.get_settings(),
        queued=queued,
        queued_image=queued_image,
        queued_when=queued_when,
        queued_iso=queued_iso,
        queued_reason=queued_reason,
        fb_groups=groups,
        fb_groups_error=groups_error,
        selected_group_ids=set(social.selected_group_ids()),
        fb_page_choices=fb_page_choices,
    )


@admin_bp.route("/social/connect", methods=["POST"])
@admin_required
def social_connect():
    page_id = request.form.get("page_id", "").strip()
    token = request.form.get("token", "").strip()
    if not token:
        flash("Token utilisateur requis.", "error")
        return redirect(url_for("admin.social"))
    result = social.connect_page(page_id, token)
    if result.get("ok"):
        flash(f"Page Facebook « {result['message']} » connectée. {result.get('detail') or ''}", "success")
        log_event(CAT_ADMIN, "facebook_connect", summary=f"Page Facebook connectée: {result['message']}", level=LEVEL_SUCCESS)
    elif result.get("needs_page_choice"):
        flash(result.get("message") or "Choisissez une page Facebook ci-dessous.", "success")
    else:
        flash(f"Connexion impossible : {result.get('message') or result.get('detail')}", "error")
    return redirect(url_for("admin.social"))


@admin_bp.route("/social/pick-page", methods=["POST"])
@admin_required
def social_pick_page():
    page_id = request.form.get("page_id", "").strip()
    token = social.stored_user_token()
    if not page_id:
        flash("Choisissez une page dans la liste.", "error")
        return redirect(url_for("admin.social"))
    if not token:
        flash("Token utilisateur manquant — recollez-le ou reconnectez avec Facebook.", "error")
        return redirect(url_for("admin.social"))
    result = social.connect_page(page_id, token)
    if result.get("ok"):
        flash(f"Page Facebook « {result['message']} » connectée. {result.get('detail') or ''}", "success")
        log_event(CAT_ADMIN, "facebook_pick_page", summary=f"Page Facebook choisie: {result['message']}", level=LEVEL_SUCCESS)
    else:
        flash(f"Connexion impossible : {result.get('message') or result.get('detail')}", "error")
    return redirect(url_for("admin.social"))


@admin_bp.route("/social/facebook/login")
@admin_required
def social_facebook_login():
    if not social.app_credentials_ready():
        flash(
            "Configurez FACEBOOK_APP_ID et FACEBOOK_APP_SECRET (app Meta) pour connecter Facebook en un clic.",
            "error",
        )
        return redirect(url_for("admin.social"))
    page_id = (request.args.get("page_id") or "").strip() or (social.get_config().get("page_id") or "")
    state = secrets.token_urlsafe(32)
    redirect_uri = social.facebook_oauth_redirect_uri()
    session["fb_oauth_state"] = state
    session["fb_oauth_page_id"] = page_id
    session["fb_oauth_redirect_uri"] = redirect_uri
    return redirect(social.facebook_oauth_url(state, redirect_uri))


@admin_bp.route("/social/facebook/callback")
@admin_required
def social_facebook_callback():
    oauth_error = request.args.get("error")
    if oauth_error:
        flash(f"Connexion Facebook refusée : {oauth_error}", "error")
        return redirect(url_for("admin.social"))

    state = request.args.get("state")
    if not state or state != session.pop("fb_oauth_state", None):
        flash("État OAuth Facebook invalide — réessayez la connexion.", "error")
        return redirect(url_for("admin.social"))

    code = request.args.get("code")
    if not code:
        flash("Code d'autorisation Facebook manquant.", "error")
        return redirect(url_for("admin.social"))

    redirect_uri = session.pop("fb_oauth_redirect_uri", None) or social.facebook_oauth_redirect_uri()
    page_id = session.pop("fb_oauth_page_id", "") or ""
    user_token, err = social.exchange_oauth_code(code, redirect_uri)
    if not user_token:
        flash(f"Échec de la connexion Facebook : {err}", "error")
        return redirect(url_for("admin.social"))

    result = social.connect_page(page_id, user_token)
    if result.get("ok"):
        flash(f"Page Facebook « {result['message']} » connectée. {result.get('detail') or ''}", "success")
        log_event(CAT_ADMIN, "facebook_oauth", summary=f"Page Facebook connectée via OAuth: {result['message']}", level=LEVEL_SUCCESS)
    elif result.get("needs_page_choice"):
        flash(result.get("message") or "Choisissez une page Facebook ci-dessous.", "success")
        log_event(CAT_ADMIN, "facebook_oauth", summary="Token Facebook reçu — choix de page", level=LEVEL_SUCCESS)
    else:
        flash(f"Connexion impossible : {result.get('message') or result.get('detail')}", "error")
    return redirect(url_for("admin.social"))


@admin_bp.route("/social/disconnect", methods=["POST"])
@admin_required
def social_disconnect():
    social.disconnect()
    flash("Page Facebook déconnectée.", "success")
    return redirect(url_for("admin.social"))


@admin_bp.route("/social/linkedin/login")
@admin_required
def social_linkedin_login():
    if not linkedin_social.app_credentials_ready():
        flash(
            "Configurez LINKEDIN_CLIENT_ID et LINKEDIN_CLIENT_SECRET (app LinkedIn Developers) pour connecter LinkedIn en un clic.",
            "error",
        )
        return redirect(url_for("admin.social"))
    state = secrets.token_urlsafe(32)
    redirect_uri = linkedin_social.linkedin_oauth_redirect_uri()
    session["li_oauth_state"] = state
    session["li_oauth_redirect_uri"] = redirect_uri
    return redirect(linkedin_social.oauth_url(state, redirect_uri))


@admin_bp.route("/social/linkedin/callback")
@admin_required
def social_linkedin_callback():
    oauth_error = request.args.get("error")
    if oauth_error:
        desc = request.args.get("error_description") or oauth_error
        flash(f"Connexion LinkedIn refusée : {desc}", "error")
        return redirect(url_for("admin.social"))

    state = request.args.get("state")
    if not state or state != session.pop("li_oauth_state", None):
        flash("État OAuth LinkedIn invalide — réessayez la connexion.", "error")
        return redirect(url_for("admin.social"))

    code = request.args.get("code")
    if not code:
        flash("Code d'autorisation LinkedIn manquant.", "error")
        return redirect(url_for("admin.social"))

    redirect_uri = session.pop("li_oauth_redirect_uri", None) or linkedin_social.linkedin_oauth_redirect_uri()
    result = linkedin_social.complete_oauth(code, redirect_uri)
    if result.get("ok"):
        if result.get("publish_as") == "member":
            flash(result.get("message") or "Profil LinkedIn connecté.", "success")
        else:
            flash(f"Page LinkedIn « {result['message']} » connectée.", "success")
        log_event(CAT_ADMIN, "linkedin_oauth", summary=result.get("message") or "LinkedIn connecté", level=LEVEL_SUCCESS)
    elif result.get("needs_org_choice"):
        flash(result.get("message") or "Choisissez une page entreprise ci-dessous.", "success")
        log_event(CAT_ADMIN, "linkedin_oauth", summary="Token LinkedIn reçu — choix de page", level=LEVEL_SUCCESS)
    else:
        flash(f"Connexion LinkedIn : {result.get('message')}", "error")
    return redirect(url_for("admin.social"))


@admin_bp.route("/social/linkedin/connect", methods=["POST"])
@admin_required
def social_linkedin_connect():
    token = request.form.get("token", "").strip()
    org_id = request.form.get("org_id", "").strip()
    if not token:
        flash("Jeton LinkedIn requis.", "error")
        return redirect(url_for("admin.social"))
    result = linkedin_social.connect_with_token(token, org_id)
    if result.get("ok"):
        if result.get("publish_as") == "member":
            flash(result.get("message") or "Profil LinkedIn connecté.", "success")
        else:
            flash(f"Page LinkedIn « {result['message']} » connectée.", "success")
        log_event(CAT_ADMIN, "linkedin_connect", summary=result.get("message") or "LinkedIn connecté", level=LEVEL_SUCCESS)
    elif result.get("needs_org_choice"):
        flash(result.get("message") or "Choisissez une page entreprise ci-dessous.", "success")
    else:
        flash(result.get("message") or "Connexion LinkedIn impossible.", "error")
    return redirect(url_for("admin.social"))


@admin_bp.route("/social/linkedin/pick-org", methods=["POST"])
@admin_required
def social_linkedin_pick_org():
    org_id = request.form.get("org_id", "").strip()
    if not org_id:
        flash("Choisissez une page entreprise dans la liste.", "error")
        return redirect(url_for("admin.social"))
    result = linkedin_social.connect_organization(org_id)
    if result.get("ok"):
        flash(f"Page LinkedIn « {result['message']} » connectée.", "success")
        log_event(CAT_ADMIN, "linkedin_pick_org", summary=f"Page LinkedIn choisie: {result['message']}", level=LEVEL_SUCCESS)
    else:
        flash(result.get("message") or "Connexion LinkedIn impossible.", "error")
    return redirect(url_for("admin.social"))


@admin_bp.route("/social/linkedin/disconnect", methods=["POST"])
@admin_required
def social_linkedin_disconnect():
    linkedin_social.disconnect()
    flash("Page LinkedIn déconnectée.", "success")
    return redirect(url_for("admin.social"))


@admin_bp.route("/social/publish", methods=["POST"])
@admin_required
def social_publish():
    from app.services.social_links import display_url, ensure_tracked, with_utm_source

    message = request.form.get("message", "").strip()
    link = request.form.get("link", "").strip()
    target_key = (request.form.get("target_key") or "").strip() or None
    image_path = (request.form.get("image_path") or "").strip() or None
    ai_flag = request.form.get("generated_by_ai") == "1"
    content_tag = "ai_post" if ai_flag else "manual_post"
    if not message:
        flash("Le message ne peut pas être vide.", "error")
        return redirect(url_for("admin.social"))
    if request.form.get("confirmed") != "1":
        flash("Confirmez l'aperçu avant d'envoyer le post.", "error")
        return redirect(url_for("admin.social"))
    if not image_path:
        flash("Générez d'abord le visuel — l'aperçu doit être visible avant l'envoi.", "error")
        return redirect(url_for("admin.social"))
    if not social.is_configured() and not linkedin_social.is_configured():
        flash("Connectez Facebook ou LinkedIn pour publier.", "error")
        return redirect(url_for("admin.social"))
    tracked_link = ensure_tracked(link, target_key=target_key, content=content_tag)
    results = []
    if social.is_configured():
        results.append(
            (
                "Facebook",
                social.publish_post(
                    message,
                    link=tracked_link,
                    generated_by_ai=ai_flag,
                    image_path=image_path,
                ),
            )
        )
    if linkedin_social.is_configured():
        results.append(
            (
                "LinkedIn",
                linkedin_social.publish_post(
                    message,
                    link=with_utm_source(tracked_link, "linkedin"),
                    generated_by_ai=ai_flag,
                    image_path=image_path,
                    target_key=target_key,
                ),
            )
        )
    shown = display_url(tracked_link) if tracked_link else ""
    ok_names = [name for name, post in results if post.status == "published"]
    if ok_names:
        flash(
            f"Post publié sur {' et '.join(ok_names)} 🎉"
            + (f" — lien tracké : {shown}" if shown else ""),
            "success",
        )
    for name, post in results:
        if post.status != "published":
            flash(f"{name} : {post.error or 'échec de publication'}", "error")
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

        payload.update(
            social_image.generate_for_post(
                prompt,
                tone,
                headline=payload.get("image_headline"),
                visual_brief=payload.get("visual_brief"),
                theme=target_key,
            )
        )
        payload.pop("png", None)
        log_event(CAT_ADMIN, "social_ai_generate", summary=f"Post généré par IA: {prompt[:80]}")
        return jsonify(payload)
    except content_ai.ContentAIError as exc:
        return jsonify({"error": str(exc)}), 502
    except Exception as exc:  # noqa: BLE001 — never 500 on IA generate
        current_app.logger.exception("social_ai_generate failed")
        return jsonify({"error": f"Génération impossible : {exc}"}), 502


@admin_bp.route("/social/autopost", methods=["POST"])
@admin_required
def social_autopost_save():
    from app.services import social_autopost

    enabled = request.form.get("enabled") == "1"
    try:
        interval = int(request.form.get("interval") or 24)
    except ValueError:
        interval = 24
    share_groups = request.form.get("share_groups") == "1"
    group_ids = request.form.getlist("group_id")
    social.save_group_ids(group_ids)
    social_autopost.save_settings(interval=interval, share_groups=share_groups)
    if enabled and not social_autopost.any_network_connected():
        flash("Connectez d'abord Facebook ou LinkedIn pour activer l'autopublication.", "error")
        return redirect(url_for("admin.social"))
    try:
        if enabled:
            social_autopost.enable_autopost(interval)
            flash("Autopublication activée. L'aperçu du prochain post reste visible jusqu'à l'envoi.", "success")
        else:
            social_autopost.disable_autopost()
            flash("Autopublication désactivée. Rien ne partira automatiquement.", "success")
    except Exception:
        current_app.logger.exception("social_autopost_save failed")
        flash(
            "Impossible de préparer l'aperçu. Les réglages n'ont pas tous été enregistrés — réessayez.",
            "error",
        )
    return redirect(url_for("admin.social"))


@admin_bp.route("/social/queue/regenerate", methods=["POST"])
@admin_required
def social_queue_regenerate():
    from app.services import social_autopost

    current = social_autopost.queued_preview()
    keep = current.scheduled_for if current else None
    try:
        social_autopost.generate_preview(keep_schedule=keep, use_dalle=True)
        flash("Nouvel aperçu généré — l'heure d'envoi ne change pas.", "success")
    except Exception:
        current_app.logger.exception("social_queue_regenerate failed")
        flash("Génération impossible. Réessayez dans un instant.", "error")
    return redirect(url_for("admin.social"))


@admin_bp.route("/social/queue/save", methods=["POST"])
@admin_required
def social_queue_save():
    from app.services import social_autopost

    try:
        social_autopost.update_queued(
            message=request.form.get("message"),
            target_key=request.form.get("target_key"),
        )
        flash("Aperçu mis à jour. Il restera affiché jusqu'à l'envoi.", "success")
    except Exception:
        current_app.logger.exception("social_queue_save failed")
        flash("Impossible d'enregistrer l'aperçu. Réessayez.", "error")
    return redirect(url_for("admin.social"))


@admin_bp.route("/social/queue/publish-now", methods=["POST"])
@admin_required
def social_queue_publish_now():
    from app.services import social_autopost

    if request.form.get("confirmed") != "1":
        flash("Confirmez l'aperçu avant d'envoyer le post.", "error")
        return redirect(url_for("admin.social"))
    post = social_autopost.publish_queued_now()
    if not post:
        flash("Aucun aperçu en attente.", "error")
    elif post.status == "published":
        flash("Aperçu publié. Le prochain post est déjà visible ci-dessus.", "success")
    else:
        flash(f"Échec de la publication : {post.error}", "error")
    return redirect(url_for("admin.social"))


# ------------------------------------------------------------------ prospection B2B
@admin_bp.route("/prospecting", endpoint="prospecting")
@admin_required
def prospecting_page():
    from app.constants.trades import trade_choices
    from app.services import prospect_search, prospecting

    status_filter = (request.args.get("status") or "").strip() or None
    trade_filter = (request.args.get("trade") or "").strip() or None
    trades = trade_choices("fr")
    return render_template(
        "admin/prospecting.html",
        prospects=prospecting.list_prospects(status=status_filter, trade_type=trade_filter),
        trades=trades,
        trade_lookup={t["key"]: t for t in trades},
        stats=prospecting.prospect_stats(),
        status_labels=prospecting.OUTREACH_STATUS_LABELS,
        status_filter=status_filter,
        trade_filter=trade_filter,
        search_provider=prospect_search.search_provider(),
        ai_available=content_ai.is_available(),
        smtp_configured=admin_email.is_configured(),
    )


@admin_bp.route("/api/prospecting/search", methods=["POST"])
@admin_required
@rate_limit(limit=6, window=3600, scope="admin_prospect_search")
def api_prospecting_search():
    from app.services import prospecting

    data = request.get_json(silent=True) or {}
    trade_type = (data.get("trade_type") or "plombier").strip()
    city = (data.get("city") or "").strip()
    max_results = int(data.get("max_results") or 12)
    try:
        result = prospecting.run_search(trade_type=trade_type, city=city, max_results=max_results)
        log_event(
            CAT_ADMIN,
            "prospect_search",
            summary=f"Prospection {trade_type} · {city} — {result['found']} trouvés ({result['with_email']} emails)",
            level=LEVEL_SUCCESS,
        )
        return jsonify(result)
    except prospecting.ProspectingError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        current_app.logger.exception("prospect_search failed")
        return jsonify({"error": f"Recherche impossible : {exc}"}), 502


@admin_bp.route("/api/prospecting/<prospect_id>/generate-email", methods=["POST"])
@admin_required
def api_prospecting_generate_email(prospect_id):
    from app.services import prospecting

    data = request.get_json(silent=True) or {}
    tone = (data.get("tone") or "professionnel").strip()
    angle = (data.get("angle") or "").strip()
    try:
        prospect = prospecting.generate_outreach_email(prospect_id, tone=tone, angle=angle)
        log_event(CAT_ADMIN, "prospect_email_generate", summary=f"E-mail IA pour {prospect.email or prospect.id}")
        return jsonify(prospect.to_dict())
    except prospecting.ProspectingError as exc:
        return jsonify({"error": str(exc)}), 400
    except content_ai.ContentAIError as exc:
        return jsonify({"error": str(exc)}), 502
    except Exception as exc:  # noqa: BLE001
        current_app.logger.exception("prospect_email_generate failed")
        return jsonify({"error": f"Génération impossible : {exc}"}), 502


@admin_bp.route("/prospecting/<prospect_id>/send", methods=["POST"])
@admin_required
def prospecting_send(prospect_id):
    from app.services import prospecting

    try:
        result = prospecting.send_outreach_email(prospect_id)
        email = result["prospect"].get("email")
        if result["email_status"] == "failed":
            log_event(
                CAT_ADMIN,
                "prospect_email_send",
                summary=f"Échec e-mail prospection à {email} — {result.get('email_error')}",
                level=LEVEL_ERROR,
            )
            flash(
                f"Échec d'envoi à {email} : {result.get('email_error') or 'erreur SMTP inconnue'}",
                "error",
            )
        else:
            log_event(
                CAT_ADMIN,
                "prospect_email_send",
                summary=f"E-mail prospection envoyé à {email}",
                level=LEVEL_SUCCESS,
            )
            flash(f"E-mail envoyé à {email} ({result['email_status']}).", "success")
    except prospecting.ProspectingError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.prospecting"))


@admin_bp.route("/prospecting/resend", methods=["POST"], endpoint="prospecting_resend")
@admin_required
def prospecting_resend():
    """Renvoie l'e-mail de prospection aux contactés dont l'envoi avait échoué.

    ``mode=all`` force le renvoi à tous les contactés, y compris ceux dont le
    dernier e-mail était marqué « sent » (cas d'un serveur qui accepte le
    message puis le bloque en aval).
    """
    from app.services import prospecting

    resend_all = request.form.get("mode") == "all"
    result = prospecting.resend_outreach_emails(only_failed=not resend_all)
    log_event(
        CAT_ADMIN,
        "prospect_email_resend",
        summary=(
            f"Renvoi prospection{' (forcé)' if resend_all else ''} : {result['sent']} envoyé(s), "
            f"{result['skipped']} déjà reçu(s), {len(result['failed'])} échec(s)"
        ),
        level=LEVEL_SUCCESS if not result["failed"] else LEVEL_ERROR,
    )
    if result["sent"]:
        flash(f"{result['sent']} e-mail(s) de prospection renvoyé(s).", "success")
    if result["skipped"]:
        flash(
            f"{result['skipped']} prospect(s) ignoré(s) : leur e-mail était déjà bien parti. "
            "Utilisez « Tout renvoyer » pour forcer le renvoi.",
            "info",
        )
    if result["failed"]:
        extra = len(result["failed"]) - 5
        flash(
            f"Échec pour {len(result['failed'])} envoi(s) : "
            + " · ".join(result["failed"][:5])
            + (f" (+{extra} autres — détail dans le Journal)" if extra > 0 else ""),
            "error",
        )
    if result.get("remaining"):
        flash(
            f"{result['remaining']} envoi(s) en attente (lot limité) — cliquez à nouveau pour continuer.",
            "info",
        )
    if not result["total"]:
        flash("Aucun prospect contacté à renvoyer.", "info")
    return redirect(url_for("admin.prospecting"))


@admin_bp.route("/prospecting/<prospect_id>/status", methods=["POST"])
@admin_required
def prospecting_status(prospect_id):
    from app.services import prospecting

    status = (request.form.get("status") or "").strip()
    try:
        prospecting.update_status(prospect_id, status)
        flash("Statut mis à jour.", "success")
    except prospecting.ProspectingError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.prospecting"))


@admin_bp.route("/prospecting/<prospect_id>/delete", methods=["POST"])
@admin_required
def prospecting_delete(prospect_id):
    from app.services import prospecting

    try:
        prospecting.delete_prospect(prospect_id)
        flash("Prospect supprimé.", "success")
    except prospecting.ProspectingError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.prospecting"))


# ------------------------------------------------------------------ Trade guides (SEO)
@admin_bp.route("/seo/trade-guides")
@admin_required
def seo_trade_guides():
    """Admin table: 1 row per trade, showing whether a guide exists + regen button."""
    from app.constants.trades import TRADES, trade_label
    from app.services import trade_guides

    lang = "fr"
    rows = []
    for key in TRADES:
        if key == "autre":
            continue
        guide = trade_guides.get_guide(key, lang)
        rows.append(
            {
                "key": key,
                "label": trade_label(key, lang),
                "guide": guide,
                "fresh": guide.is_fresh() if guide else False,
                "generated_at": guide.generated_at if guide else None,
            }
        )
    return render_template("admin/trade_guides.html", rows=rows)


@admin_bp.route("/seo/trade-guides/<trade>/regenerate", methods=["POST"])
@admin_required
@rate_limit(limit=20, window=300, scope="admin_trade_guide_regen")
def seo_trade_guide_regenerate(trade):
    from app.constants.trades import TRADES
    from app.services import trade_guides

    trade = (trade or "").strip().lower()
    if trade not in TRADES or trade == "autre":
        flash("Métier inconnu.", "error")
        return redirect(url_for("admin.seo_trade_guides"))
    guide = trade_guides.get_or_generate(trade, "fr", force=True)
    if guide and guide.body_html:
        # Publishing a guide flips this trade's whole page set from noindex to
        # indexable, so tell the engines about the hub straight away.
        _ping_indexnow(f"/artisans/metier/{trade}")
        flash(f"Guide « {trade} » régénéré ({len(guide.body_html)} caractères).", "success")
    else:
        flash(
            "Génération échouée — vérifie MISTRAL_API_KEY, quota Mistral, ou re-essaie.",
            "error",
        )
    return redirect(url_for("admin.seo_trade_guides"))


# ------------------------------------------------------------ Revendications de fiches
@admin_bp.route("/revendications", methods=["GET"], endpoint="listing_claims")
@admin_required
def listing_claims_page():
    from app.models.listing_claim import STATUS_LABELS, STATUS_PENDING, ListingClaim
    from app.services import registry_import

    status = (request.args.get("status") or STATUS_PENDING).strip()
    query = ListingClaim.query
    if status in STATUS_LABELS:
        query = query.filter(ListingClaim.status == status)
    claims = query.order_by(ListingClaim.created_at.desc()).limit(200).all()

    counts = {}
    for key in STATUS_LABELS:
        counts[key] = ListingClaim.query.filter_by(status=key).count()

    return render_template(
        "admin/listing_claims.html",
        claims=claims,
        status=status,
        status_labels=STATUS_LABELS,
        counts=counts,
        registry_stats=registry_import.stats(),
    )


@admin_bp.route("/revendications/<claim_id>/approve", methods=["POST"])
@admin_required
def listing_claim_approve(claim_id):
    from app.models.listing_claim import ListingClaim
    from app.services import listing_claims as svc

    claim = db.session.get(ListingClaim, _pk_value(ListingClaim, claim_id))
    if not claim:
        abort(404)
    if not claim.is_pending:
        flash("Cette demande a déjà été traitée.", "error")
        return redirect(url_for("admin.listing_claims"))

    ok, message = svc.approve(claim, note=(request.form.get("note") or "").strip())
    flash(message, "success" if ok else "error")
    if ok:
        log_event(
            CAT_ADMIN,
            "listing_claim_approved",
            summary=f"Fiche « {claim.listing.name if claim.listing else claim.siren} » transférée à {claim.email}",
            level=LEVEL_SUCCESS,
        )
    return redirect(url_for("admin.listing_claims"))


@admin_bp.route("/revendications/<claim_id>/reject", methods=["POST"])
@admin_required
def listing_claim_reject(claim_id):
    from app.models.listing_claim import ListingClaim
    from app.services import listing_claims as svc

    claim = db.session.get(ListingClaim, _pk_value(ListingClaim, claim_id))
    if not claim:
        abort(404)
    if not claim.is_pending:
        flash("Cette demande a déjà été traitée.", "error")
        return redirect(url_for("admin.listing_claims"))

    svc.reject(claim, note=(request.form.get("note") or "").strip())
    flash("Demande refusée.", "success")
    log_event(
        CAT_ADMIN,
        "listing_claim_rejected",
        summary=f"Revendication refusée — {claim.siren} ({claim.email})",
        level=LEVEL_WARNING,
    )
    return redirect(url_for("admin.listing_claims"))


@admin_bp.route("/promo")
@admin_required
def promo():
    from app.constants.trades import trade_label
    from app.models.founding import (
        SOURCE_LABELS,
        SOURCES,
        STATUS_LABELS,
        STATUSES,
        FoundingParticipant,
        FoundingWaitlist,
    )
    from app.services import founding_program

    try:
        founding_program.tick()
    except Exception:
        current_app.logger.exception("promo tick failed")
    cfg = founding_program.get_config()
    kpis = founding_program.kpis()
    status_filter = (request.args.get("status") or "all").strip()
    trade_filter = (request.args.get("trade") or "").strip()
    city_filter = (request.args.get("city") or "").strip()
    source_filter = (request.args.get("source") or "").strip()
    query = FoundingParticipant.query
    if status_filter and status_filter != "all":
        query = query.filter(FoundingParticipant.status == status_filter)
    rows = query.order_by(FoundingParticipant.place_number.asc()).all()
    participants = []
    for row in rows:
        tenant = row.tenant or db.session.get(Tenant, row.tenant_id)
        user = row.user or db.session.get(User, row.user_id)
        if trade_filter and (not tenant or tenant.trade_type != trade_filter):
            continue
        if city_filter and (not tenant or city_filter.lower() not in (tenant.city or "").lower()):
            continue
        if source_filter and (row.source or "") != source_filter:
            continue
        progress = founding_program.activation_progress(row)
        participants.append(
            {
                "row": row,
                "tenant": tenant,
                "user": user,
                "progress": progress,
                "trade": trade_label(tenant.trade_type, "fr") if tenant else "—",
            }
        )
    waitlist = FoundingWaitlist.query.order_by(FoundingWaitlist.created_at.desc()).all()
    funnel = founding_program.funnel()
    sources = founding_program.sources_breakdown()
    occupancy_pct = int(round(100 * kpis["occupied"] / kpis["max"])) if kpis["max"] else 0
    return render_template(
        "admin/promo.html",
        kpis=kpis,
        funnel=funnel,
        funnel_max=max((step["value"] for step in funnel), default=0),
        sources=sources,
        source_max=max((item["inscrits"] for item in sources), default=0),
        referrals=founding_program.referral_stats(),
        alerts=founding_program.alerts(),
        participants=participants,
        waitlist=waitlist,
        cfg=cfg,
        occupancy_pct=occupancy_pct,
        status_filter=status_filter,
        trade_filter=trade_filter,
        city_filter=city_filter,
        source_filter=source_filter,
        statuses=STATUSES,
        status_labels=STATUS_LABELS,
        sources_list=SOURCES,
        source_labels=SOURCE_LABELS,
    )


@admin_bp.route("/promo/config", methods=["POST"])
@admin_required
def promo_config():
    from app.services import founding_program

    founding_program.save_config(
        {
            "enabled": request.form.get("enabled") == "1",
            "max_participants": request.form.get("max_participants"),
            "duration_days": request.form.get("duration_days"),
            "waitlist_enabled": request.form.get("waitlist_enabled") == "1",
            "nudge_inactive_days": request.form.get("nudge_inactive_days"),
            "nudge_no_usage_days": request.form.get("nudge_no_usage_days"),
            "at_risk_days": request.form.get("at_risk_days"),
            "start_date": request.form.get("start_date"),
            "end_date": request.form.get("end_date"),
            "post_offer": request.form.get("post_offer"),
        }
    )
    flash("Configuration du programme enregistrée.", "success")
    log_event(CAT_ADMIN, "founding_config", summary="Promo 50 artisans : configuration mise à jour")
    return redirect(url_for("admin.promo"))


@admin_bp.route("/promo/<participant_id>/status", methods=["POST"])
@admin_required
def promo_status(participant_id):
    from app.models.founding import FoundingParticipant
    from app.services import founding_program

    row = db.session.get(FoundingParticipant, _pk_value(FoundingParticipant, participant_id))
    if not row:
        abort(404)
    new_status = (request.form.get("status") or "").strip()
    try:
        founding_program.set_status(
            row, new_status, actor=f"admin:{session.get('admin_username') or 'admin'}"
        )
        flash("Statut mis à jour.", "success")
    except Exception:
        flash("Statut invalide.", "error")
    return redirect(url_for("admin.promo"))


@admin_bp.route("/promo/<participant_id>/remind", methods=["POST"])
@admin_required
def promo_remind(participant_id):
    from app.models.founding import FoundingParticipant
    from app.services.transactional_email import send_founding_admin_reminder

    row = db.session.get(FoundingParticipant, _pk_value(FoundingParticipant, participant_id))
    if not row:
        abort(404)
    user = row.user or db.session.get(User, row.user_id)
    tenant = row.tenant or db.session.get(Tenant, row.tenant_id)
    send_founding_admin_reminder(user, tenant, row)
    flash("Rappel envoyé (ou simulé si SMTP absent).", "success")
    return redirect(url_for("admin.promo"))


# --------------------------------------------------------------------------- #
# Mailing campaigns — Brevo-style designer, sending and reporting
# --------------------------------------------------------------------------- #
def _campaign_or_404(campaign_id):
    from app.services import campaigns

    try:
        return campaigns.get_campaign(campaign_id)
    except campaigns.CampaignError:
        abort(404)


@admin_bp.route("/campagnes", endpoint="campaigns")
@admin_required
def campaigns_page():
    from app.constants.trades import trade_choices
    from app.services import campaign_ai, campaigns

    status_filter = (request.args.get("status") or "").strip() or None
    rows = campaigns.list_campaigns(status=status_filter)
    return render_template(
        "admin/campaigns.html",
        campaigns=rows,
        stats=campaigns.overview_stats(),
        campaign_stats={str(c.id): campaigns.campaign_stats(c.id) for c in rows},
        status_filter=status_filter,
        trades=trade_choices("fr"),
        ai_available=campaign_ai.is_available(),
        smtp_configured=admin_email.is_configured(),
    )


@admin_bp.route("/campagnes/new", methods=["POST"])
@admin_required
def campaign_new():
    from app.services import campaigns

    campaign = campaigns.create_campaign(
        name=request.form.get("name") or "",
        template=(request.form.get("template") or "offre").strip(),
    )
    log_event(CAT_ADMIN, "campaign_create", summary=f"Campagne créée : {campaign.name}")
    return redirect(url_for("admin.campaign_editor", campaign_id=campaign.id))


@admin_bp.route("/campagnes/<campaign_id>", endpoint="campaign_editor")
@admin_required
def campaign_editor_page(campaign_id):
    from app.constants.trades import trade_choices
    from app.services import campaign_ai, campaign_render, campaigns

    campaign = _campaign_or_404(campaign_id)
    return render_template(
        "admin/campaign_editor.html",
        campaign=campaign,
        campaign_json=campaign.to_dict(),
        stats=campaigns.campaign_stats(campaign.id),
        audience=campaigns.preview_audience(campaign.segment()),
        trades=trade_choices("fr"),
        merge_tags=campaign_render.MERGE_TAGS,
        ai_available=campaign_ai.is_available(),
        smtp_configured=admin_email.is_configured(),
        default_from=admin_email.default_from_addr(),
    )


@admin_bp.route("/campagnes/<campaign_id>/rapport", endpoint="campaign_report")
@admin_required
def campaign_report_page(campaign_id):
    from app.models.email_campaign import RECIPIENT_STATUS_LABELS
    from app.services import campaigns

    campaign = _campaign_or_404(campaign_id)
    status_filter = (request.args.get("r") or "").strip() or None
    return render_template(
        "admin/campaign_report.html",
        campaign=campaign,
        stats=campaigns.campaign_stats(campaign.id),
        recipients=campaigns.recipient_rows(campaign.id, status=status_filter),
        status_labels=RECIPIENT_STATUS_LABELS,
        status_filter=status_filter,
    )


@admin_bp.route("/campagnes/<campaign_id>/apercu", endpoint="campaign_preview")
@admin_required
def campaign_preview(campaign_id):
    """The rendered e-mail on its own, for the editor iframe and the report."""
    from app.services import campaign_render

    campaign = _campaign_or_404(campaign_id)
    html = campaign_render.render_html(
        campaign.design(),
        ctx=campaign_render.sample_context(),
        preheader=campaign.preheader,
    )
    return current_app.response_class(html, mimetype="text/html")


@admin_bp.route("/api/campaigns/preview", methods=["POST"])
@admin_required
def api_campaign_preview():
    """Render an unsaved design — what the editor shows while you type."""
    from app.services import campaign_render

    data = request.get_json(silent=True) or {}
    design = data.get("design") if isinstance(data.get("design"), dict) else {}
    html = campaign_render.render_html(
        design,
        ctx=campaign_render.sample_context(),
        preheader=(data.get("preheader") or "").strip() or None,
    )
    return jsonify({"html": html})


@admin_bp.route("/api/campaigns/<campaign_id>/save", methods=["POST"])
@admin_required
def api_campaign_save(campaign_id):
    from app.services import campaigns

    data = request.get_json(silent=True) or {}
    fields = {k: data[k] for k in
              ("name", "subject", "preheader", "from_name", "reply_to", "design", "segment", "ai_prompt")
              if k in data}
    try:
        campaign = campaigns.save_campaign(campaign_id, **fields)
    except campaigns.CampaignError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"campaign": campaign.to_dict(), "saved_at": campaign.updated_at.isoformat()})


@admin_bp.route("/api/campaigns/<campaign_id>/audience", methods=["POST"])
@admin_required
def api_campaign_audience(campaign_id):
    from app.services import campaigns

    _campaign_or_404(campaign_id)
    data = request.get_json(silent=True) or {}
    segment = data.get("segment") if isinstance(data.get("segment"), dict) else {}
    return jsonify(campaigns.preview_audience(segment))


@admin_bp.route("/api/campaigns/generate", methods=["POST"])
@admin_required
@rate_limit(limit=20, window=3600, scope="admin_campaign_ai")
def api_campaign_generate():
    from app.services import campaign_ai

    data = request.get_json(silent=True) or {}
    brief = (data.get("brief") or "").strip()
    if not brief:
        return jsonify({"error": "Décrivez ce que doit dire l'e-mail."}), 400
    try:
        payload = campaign_ai.generate_campaign(
            brief=brief,
            audience=data.get("audience") if isinstance(data.get("audience"), dict) else None,
            tone=(data.get("tone") or "direct").strip(),
            goal=(data.get("goal") or "inscription").strip(),
        )
        log_event(CAT_ADMIN, "campaign_ai_generate", summary=f"Campagne générée par IA : {brief[:80]}")
        return jsonify(payload)
    except campaign_ai.CampaignAIError as exc:
        return jsonify({"error": str(exc)}), 502
    except Exception as exc:  # noqa: BLE001 — never 500 on an AI call
        current_app.logger.exception("campaign_ai_generate failed")
        return jsonify({"error": f"Génération impossible : {exc}"}), 502


@admin_bp.route("/api/campaigns/subjects", methods=["POST"])
@admin_required
@rate_limit(limit=30, window=3600, scope="admin_campaign_ai")
def api_campaign_subjects():
    from app.services import campaign_ai

    data = request.get_json(silent=True) or {}
    try:
        return jsonify({"subjects": campaign_ai.suggest_subjects(brief=(data.get("brief") or "").strip())})
    except campaign_ai.CampaignAIError as exc:
        return jsonify({"error": str(exc)}), 502
    except Exception as exc:  # noqa: BLE001
        current_app.logger.exception("campaign_ai_subjects failed")
        return jsonify({"error": f"Génération impossible : {exc}"}), 502


@admin_bp.route("/api/campaigns/rewrite-block", methods=["POST"])
@admin_required
@rate_limit(limit=60, window=3600, scope="admin_campaign_ai")
def api_campaign_rewrite_block():
    from app.services import campaign_ai

    data = request.get_json(silent=True) or {}
    block = data.get("block")
    if not isinstance(block, dict):
        return jsonify({"error": "Bloc manquant."}), 400
    try:
        return jsonify({"block": campaign_ai.rewrite_block(
            block=block, instruction=(data.get("instruction") or "").strip()
        )})
    except campaign_ai.CampaignAIError as exc:
        return jsonify({"error": str(exc)}), 502
    except Exception as exc:  # noqa: BLE001
        current_app.logger.exception("campaign_ai_rewrite failed")
        return jsonify({"error": f"Réécriture impossible : {exc}"}), 502


@admin_bp.route("/api/campaigns/<campaign_id>/prepare", methods=["POST"])
@admin_required
def api_campaign_prepare(campaign_id):
    from app.services import campaigns

    try:
        result = campaigns.prepare_campaign(campaign_id)
    except campaigns.CampaignError as exc:
        return jsonify({"error": str(exc)}), 400
    log_event(
        CAT_ADMIN,
        "campaign_prepare",
        summary=f"Audience préparée : +{result['added']} destinataires ({result['total']} au total)",
    )
    return jsonify({**result, "stats": campaigns.campaign_stats(campaign_id)})


@admin_bp.route("/api/campaigns/<campaign_id>/send-batch", methods=["POST"])
@admin_required
def api_campaign_send_batch(campaign_id):
    from app.services import campaigns

    data = request.get_json(silent=True) or {}
    try:
        result = campaigns.send_batch(
            campaign_id, batch_size=int(data.get("batch_size") or campaigns.DEFAULT_BATCH)
        )
    except campaigns.CampaignError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001 — a broken batch must not 500 the console
        db.session.rollback()
        current_app.logger.exception("campaign_send_batch failed")
        return jsonify({"error": f"Envoi interrompu : {exc}"}), 502
    if result["done"]:
        log_event(
            CAT_ADMIN,
            "campaign_sent",
            summary=f"Campagne terminée — {result['sent']} envoyés sur le dernier lot",
            level=LEVEL_SUCCESS,
        )
    return jsonify({**result, "stats": campaigns.campaign_stats(campaign_id)})


@admin_bp.route("/campagnes/<campaign_id>/test", methods=["POST"])
@admin_required
def campaign_test(campaign_id):
    from app.services import campaigns

    to_addr = (request.form.get("to") or "").strip() or admin_email.default_from_addr()
    try:
        result = campaigns.send_test(campaign_id, to_addr)
        flash(
            f"E-mail de test envoyé à {to_addr} (statut : {result['status']}).",
            "success" if result["status"] != "failed" else "error",
        )
    except campaigns.CampaignError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.campaign_editor", campaign_id=campaign_id))


@admin_bp.route("/campagnes/<campaign_id>/schedule", methods=["POST"])
@admin_required
def campaign_schedule(campaign_id):
    from app.services import campaigns

    try:
        campaigns.prepare_campaign(campaign_id)
        campaigns.schedule_campaign(campaign_id, (request.form.get("scheduled_at") or "").strip() or None)
        flash("Programmation enregistrée.", "success")
    except campaigns.CampaignError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.campaign_editor", campaign_id=campaign_id))


@admin_bp.route("/campagnes/<campaign_id>/status", methods=["POST"])
@admin_required
def campaign_status(campaign_id):
    from app.services import campaigns

    try:
        campaigns.set_status(campaign_id, (request.form.get("status") or "").strip())
        flash("Statut mis à jour.", "success")
    except campaigns.CampaignError as exc:
        flash(str(exc), "error")
    return redirect(request.referrer or url_for("admin.campaigns"))


@admin_bp.route("/campagnes/<campaign_id>/duplicate", methods=["POST"])
@admin_required
def campaign_duplicate(campaign_id):
    from app.services import campaigns

    try:
        copy = campaigns.duplicate_campaign(campaign_id)
    except campaigns.CampaignError as exc:
        flash(str(exc), "error")
        return redirect(url_for("admin.campaigns"))
    flash("Campagne dupliquée.", "success")
    return redirect(url_for("admin.campaign_editor", campaign_id=copy.id))


@admin_bp.route("/campagnes/<campaign_id>/delete", methods=["POST"])
@admin_required
def campaign_delete(campaign_id):
    from app.services import campaigns

    try:
        campaigns.delete_campaign(campaign_id)
        flash("Campagne supprimée.", "success")
    except campaigns.CampaignError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.campaigns"))


@admin_bp.route("/api/prospecting/import-rge", methods=["POST"])
@admin_required
@rate_limit(limit=10, window=3600, scope="admin_rge_import")
def api_prospecting_import_rge():
    """Bulk-source artisans with a real e-mail from the ADEME open register."""
    from app.services import artisan_sourcing

    data = request.get_json(silent=True) or {}
    trades = [t for t in (data.get("trades") or []) if isinstance(t, str)]
    departments = [d for d in (data.get("departments") or []) if isinstance(d, str)]
    try:
        result = artisan_sourcing.source_artisans(
            target=int(data.get("target") or 200),
            trades=trades or None,
            departments=departments or None,
        )
    except artisan_sourcing.SourcingError as exc:
        return jsonify({"error": str(exc)}), 502
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        current_app.logger.exception("rge_import failed")
        return jsonify({"error": f"Import impossible : {exc}"}), 502

    log_event(
        CAT_ADMIN,
        "prospect_import_rge",
        summary=f"Import registre RGE — {result['imported']} artisans avec e-mail",
        level=LEVEL_SUCCESS,
    )
    return jsonify(result)
