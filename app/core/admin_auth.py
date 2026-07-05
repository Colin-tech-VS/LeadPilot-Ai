"""Standalone authentication for the admin console (/admin), kept completely
separate from the artisan web/JWT auth. Uses its own session keys so an admin
session is never confused with a tenant session and vice-versa."""
import hmac
from functools import wraps

from flask import current_app, g, redirect, session, url_for
from werkzeug.security import check_password_hash

ADMIN_SESSION_KEY = "admin_authenticated"
ADMIN_USER_KEY = "admin_username"


def verify_admin_credentials(username, password):
    """Constant-time username check + hashed password check."""
    expected_user = current_app.config.get("ADMIN_USERNAME", "")
    if not expected_user or not hmac.compare_digest(username or "", expected_user):
        return False

    plain = current_app.config.get("ADMIN_PASSWORD", "")
    if plain:
        return hmac.compare_digest(password or "", plain)

    pw_hash = current_app.config.get("ADMIN_PASSWORD_HASH", "")
    if pw_hash:
        return check_password_hash(pw_hash, password or "")
    return False


def login_admin(username):
    session[ADMIN_SESSION_KEY] = True
    session[ADMIN_USER_KEY] = username


def logout_admin():
    session.pop(ADMIN_SESSION_KEY, None)
    session.pop(ADMIN_USER_KEY, None)


def is_admin_logged_in():
    return bool(session.get(ADMIN_SESSION_KEY))


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not is_admin_logged_in():
            return redirect(url_for("admin.login"))
        g.admin_username = session.get(ADMIN_USER_KEY)
        return f(*args, **kwargs)

    return decorated
