import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps

import jwt
from flask import current_app, g, request

from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.extensions import db
from app.models.user import User


def create_access_token(user):
    """Create a JWT for the given user."""
    payload = {
        "sub": str(user.id),
        "tenant_id": str(user.tenant_id) if user.tenant_id else None,
        "role": user.role,
        "exp": datetime.now(timezone.utc)
        + timedelta(hours=current_app.config["JWT_EXPIRATION_HOURS"]),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(
        payload,
        current_app.config["JWT_SECRET_KEY"],
        algorithm=current_app.config["JWT_ALGORITHM"],
    )


def decode_access_token(token):
    """Decode and validate a JWT."""
    try:
        return jwt.decode(
            token,
            current_app.config["JWT_SECRET_KEY"],
            algorithms=[current_app.config["JWT_ALGORITHM"]],
        )
    except jwt.ExpiredSignatureError:
        raise UnauthorizedError("Token has expired")
    except jwt.InvalidTokenError:
        raise UnauthorizedError("Invalid token")


def _get_bearer_token():
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise UnauthorizedError("Missing or invalid Authorization header")
    return auth_header.split(" ", 1)[1]


def jwt_required(f):
    """Require a valid JWT. Sets g.current_user and g.tenant_id."""

    @wraps(f)
    def decorated(*args, **kwargs):
        token = _get_bearer_token()
        payload = decode_access_token(token)

        user_id = payload.get("sub")
        if not user_id:
            raise UnauthorizedError("Invalid token payload")

        user = db.session.get(User, uuid.UUID(user_id))
        if not user:
            raise UnauthorizedError("User not found")

        g.current_user = user
        g.tenant_id = user.tenant_id
        g.user_role = user.role
        return f(*args, **kwargs)

    return decorated


def tenant_required(f):
    """Require JWT and an associated tenant. Sets g.tenant_id."""

    @wraps(f)
    @jwt_required
    def decorated(*args, **kwargs):
        if not g.tenant_id:
            raise ForbiddenError("No tenant associated with this account")
        return f(*args, **kwargs)

    return decorated


def admin_required(f):
    """Require JWT with admin role."""

    @wraps(f)
    @jwt_required
    def decorated(*args, **kwargs):
        if g.user_role != "admin":
            raise ForbiddenError("Admin access required")
        return f(*args, **kwargs)

    return decorated
