from app.core.errors import AppError, ConflictError
from app.core.extensions import db
from app.models.tenant import Tenant
from app.models.user import User
from app.utils.validation import validate_email, validate_password


def register_plumber(
    email: str,
    password: str,
    company_name: str,
    phone: str | None = None,
    city: str | None = None,
) -> tuple[User, Tenant]:
    email = validate_email(email)
    validate_password(password)
    company_name = (company_name or "").strip()
    if not company_name:
        raise AppError("Company name is required", status_code=422)

    if User.query.filter_by(email=email).first():
        raise ConflictError("Email already registered")

    tenant = Tenant(
        name=company_name,
        phone_number=phone,
        city=city,
        service_radius_km=30,
    )
    db.session.add(tenant)
    db.session.flush()

    user = User(
        email=email,
        tenant_id=tenant.id,
        role="admin",
    )
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return user, tenant
