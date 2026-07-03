import uuid
from functools import wraps

from flask import g, redirect, session, url_for

from app.core.extensions import db
from app.models.user import User


def login_user_to_session(user):
    session["user_id"] = str(user.id)
    session["tenant_id"] = str(user.tenant_id) if user.tenant_id else None
    session["role"] = user.role


def logout_user_session():
    session.clear()


def web_tenant_required(f):
    """Session-based auth for HTML pages. Sets g.current_user and g.tenant_id."""

    @wraps(f)
    def decorated(*args, **kwargs):
        user_id = session.get("user_id")
        if not user_id:
            return redirect(url_for("web.login"))

        user = db.session.get(User, uuid.UUID(user_id))
        if not user:
            logout_user_session()
            return redirect(url_for("web.login"))

        if not user.tenant_id:
            session["flash_error_key"] = "login.error.no_tenant_session"
            return redirect(url_for("web.login"))

        g.current_user = user
        g.tenant_id = user.tenant_id
        g.user_role = user.role
        return f(*args, **kwargs)

    return decorated
