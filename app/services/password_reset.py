"""Stateless password-reset tokens.

A reset link carries a signed, time-limited token (``itsdangerous``) instead of
storing anything in the DB. The token embeds a fingerprint of the current
password hash, so it becomes invalid as soon as the password changes (single
use) or after ``MAX_AGE`` seconds. Works for any User — artisan or customer.
"""
import logging

from flask import current_app
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

logger = logging.getLogger(__name__)

SALT = "pilotcore-password-reset"
MAX_AGE = 3600  # 1 hour


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=SALT)


def _fingerprint(user) -> str:
    """A short, non-secret marker that changes when the password changes."""
    return (user.password_hash or "")[-16:]


def generate_reset_token(user) -> str:
    return _serializer().dumps({"uid": str(user.id), "fp": _fingerprint(user)})


def verify_reset_token(token: str):
    """Return the User for a valid, unexpired, unused token, else None."""
    import uuid

    from app.core.extensions import db
    from app.models.user import User

    if not token:
        return None
    try:
        data = _serializer().loads(token, max_age=MAX_AGE)
    except SignatureExpired:
        logger.info("Password-reset token expired")
        return None
    except BadSignature:
        logger.info("Password-reset token invalid")
        return None

    uid = data.get("uid")
    if not uid:
        return None
    try:
        user = db.session.get(User, uuid.UUID(str(uid)))
    except (ValueError, TypeError):
        return None
    if not user:
        return None
    # Fingerprint mismatch => password already changed => token consumed.
    if data.get("fp") != _fingerprint(user):
        logger.info("Password-reset token already used (fingerprint mismatch)")
        return None
    return user
