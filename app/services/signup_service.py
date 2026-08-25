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
    send_welcome: bool = True,
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

    # Persist the account FIRST. Welcome email is best-effort and must NEVER
    # prevent — or roll back — the account creation itself.
    db.session.commit()

    # Dedicated Twilio numbers are bought when the artisan pays (Stripe), not
    # here. Trials and test signups share TWILIO_AI_PHONE_NUMBER.

    # Automatic branded welcome email (never blocks signup).
    if send_welcome:
        try:
            from app.services.transactional_email import send_artisan_welcome

            send_artisan_welcome(user, tenant)
        except Exception:  # pragma: no cover - defensive; sender already swallows
            logger.exception("Welcome email failed for tenant=%s", tenant.id)

    return user, tenant


register_artisan = register_plumber


def register_customer(
    email: str,
    password: str,
    first_name: str | None = None,
    last_name: str | None = None,
    phone: str | None = None,
) -> User:
    """Register a particulier (customer) — a User with role="customer", no tenant."""
    email = validate_email(email)
    validate_password(password)

    if User.query.filter_by(email=email).first():
        raise ConflictError("Email already registered")

    user = User(
        email=email,
        tenant_id=None,
        role="customer",
        first_name=(first_name or "").strip() or None,
        last_name=(last_name or "").strip() or None,
        phone=(phone or "").strip() or None,
    )
    user.set_password(password)
    db.session.add(user)
    db.session.commit()

    try:
        from app.services.transactional_email import send_customer_welcome

        send_customer_welcome(user)
    except Exception:  # pragma: no cover - defensive
        logger.exception("Customer welcome email failed for %s", email)

    return user


def register_customer_via_voice(
    email: str,
    password: str,
    first_name: str | None = None,
    last_name: str | None = None,
    phone: str | None = None,
) -> User:
    """Compte particulier créé par l'assistant vocal — pas d'e-mail de bienvenue générique."""
    email = validate_email(email)
    validate_password(password)

    if User.query.filter_by(email=email).first():
        raise ConflictError("Email already registered")

    user = User(
        email=email,
        tenant_id=None,
        role="customer",
        first_name=(first_name or "").strip() or None,
        last_name=(last_name or "").strip() or None,
        phone=(phone or "").strip() or None,
    )
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return user
