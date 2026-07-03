import re

from app.core.errors import AppError

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def require_json(data):
    if data is None:
        raise AppError("Request body must be JSON", status_code=400)
    if not isinstance(data, dict):
        raise AppError("Request body must be a JSON object", status_code=400)
    return data


def require_fields(data, fields):
    """Validate required fields are present and non-empty strings."""
    errors = {}
    for field in fields:
        value = data.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            errors[field] = "This field is required"
    if errors:
        raise AppError("Validation failed", status_code=422, errors=errors)
    return data


def validate_email(email):
    if not email or not EMAIL_REGEX.match(email):
        raise AppError("Invalid email address", status_code=422)
    return email.strip().lower()


def validate_password(password, min_length=8):
    if not password or len(password) < min_length:
        raise AppError(
            f"Password must be at least {min_length} characters",
            status_code=422,
        )
    return password


def validate_role(role):
    allowed = ("admin", "user")
    if role not in allowed:
        raise AppError(f"Role must be one of: {', '.join(allowed)}", status_code=422)
    return role


def validate_lead_status(status):
    allowed = ("new", "booked", "lost")
    if status not in allowed:
        raise AppError(f"Status must be one of: {', '.join(allowed)}", status_code=422)
    return status
