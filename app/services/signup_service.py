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
    trade_type: str = "plombier",
) -> tuple[User, Tenant]:
    email = validate_email(email)
    validate_password(password)
    company_name = (company_name or "").strip()
    if not company_name:
        raise AppError("Company name is required", status_code=422)

    if User.query.filter_by(email=email).first():
        raise ConflictError("Email already registered")

    from app.constants.trades import DEFAULT_TRADE, TRADES
    from app.utils.slug import unique_public_slug

    trade = trade_type if trade_type in TRADES else DEFAULT_TRADE

  slug_base = f"{company_name}-{city}" if city else company_name

    tenant = Tenant(
        name=company_name,
        first_name=(first_name or "").strip() or None,
        last_name=(last_name or "").strip() or None,
        phone_number=phone,
        city=city,
        service_radius_km=30,
        plan="trial",
        trial_ends_at=utcnow() + timedelta(days=TRIAL_DAYS),
        trade_type=trade,
        public_slug=unique_public_slug(slug_base),
        is_public=True,
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

    # Automatically give this artisan a dedicated AI phone number at signup.
    # The dialed number is the only way to route inbound calls to the right tenant.
    try:
        from app.services.twilio_provisioning import provision_ai_number

        number = provision_ai_number(tenant)
        if not number and not tenant.ai_phone_number:
            logger.warning(
                "No dedicated AI number for tenant=%s — enable TWILIO_AUTO_PROVISION_NUMBERS and PUBLIC_BASE_URL",
                tenant.id,
            )
    except Exception:  # pragma: no cover - defensive, provisioning already swallows
        logger.exception("AI number provisioning failed for tenant=%s", tenant.id)

    db.session.commit()
    return user, tenant


register_artisan = register_plumber
