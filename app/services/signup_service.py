import logging
from datetime import timedelta

from app.core.errors import AppError, ConflictError
from app.core.extensions import db
from app.models.tenant import TRIAL_DAYS, Tenant, utcnow
from app.models.user import User
from app.utils.validation import validate_email, validate_password

logger = logging.getLogger(__name__)


def register_plumber(
    email: str,
    password: str,
    company_name: str,
    phone: str | None = None,
    city: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
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
        first_name=(first_name or "").strip() or None,
        last_name=(last_name or "").strip() or None,
        phone_number=phone,
        city=city,
        service_radius_km=30,
        plan="trial",
        trial_ends_at=utcnow() + timedelta(days=TRIAL_DAYS),
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

    # Automatically give this plumber their own dedicated AI phone number so the
    # multi-tenant voice line can route callers to them. Best-effort: a failure
    # here (Twilio unconfigured, no number available, regulatory bundle missing)
    # must never block the signup — the tenant simply falls back to the shared
    # number until a number is provisioned later (see scripts/provision_numbers.py).
    try:
        from app.services.twilio_provisioning import provision_ai_number

        provision_ai_number(tenant)
    except Exception:  # pragma: no cover - defensive, provisioning already swallows
        logger.exception("AI number provisioning failed for tenant=%s", tenant.id)

    db.session.commit()
    return user, tenant
