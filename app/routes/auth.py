import uuid

from flask import Blueprint, jsonify, request

from app.core.auth import create_access_token
from app.core.errors import AppError, ConflictError, UnauthorizedError
from app.core.extensions import db
from app.core.security import rate_limit
from app.models.user import User
from app.utils.validation import (
    require_fields,
    require_json,
    validate_email,
    validate_password,
)

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


@auth_bp.route("/register", methods=["POST"])
@rate_limit(limit=5, window=3600, scope="api_register")
def register():
    data = require_json(request.get_json(silent=True))
    require_fields(data, ["email", "password"])
    email = validate_email(data["email"])
    password = validate_password(data["password"])

    if User.query.filter_by(email=email).first():
        raise ConflictError("Email already registered")

    user = User(email=email, role="user")
    user.set_password(password)
    db.session.add(user)
    db.session.commit()

    token = create_access_token(user)
    return jsonify({"user": user.to_dict(), "access_token": token}), 201


@auth_bp.route("/login", methods=["POST"])
@rate_limit(limit=10, window=300, scope="api_login")
def login():
    data = require_json(request.get_json(silent=True))
    require_fields(data, ["email", "password"])
    email = validate_email(data["email"])
    password = data["password"]

    user = User.query.filter_by(email=email).first()
    if not user or not user.check_password(password):
        raise UnauthorizedError("Invalid email or password")

    token = create_access_token(user)
    return jsonify({"user": user.to_dict(), "access_token": token}), 200
