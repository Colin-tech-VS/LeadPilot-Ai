"""Accounts that belong to us, not to a customer.

The team signs up through the real forms to check that the real forms work.
Those accounts are indistinguishable from a customer's in the database, so
without this they land in the KPIs (« 4 artisans » when three are ours), in the
admin accounts list, and — worse — in the public directory, where a visitor can
book an appointment with a test company.

One list, consulted everywhere something is counted or shown. Configure it with
INTERNAL_ACCOUNT_EMAILS (comma-separated); the default covers the address the
team actually tests with.
"""
from __future__ import annotations

from flask import current_app, has_app_context

DEFAULT_INTERNAL_EMAILS = ("contact@pilotcore.fr",)


def internal_emails() -> set[str]:
    configured = ""
    if has_app_context():
        configured = current_app.config.get("INTERNAL_ACCOUNT_EMAILS") or ""
    emails = {e.strip().lower() for e in str(configured).split(",") if e.strip()}
    return emails or {e.lower() for e in DEFAULT_INTERNAL_EMAILS}


def is_internal_email(email: str | None) -> bool:
    return bool(email) and email.strip().lower() in internal_emails()


def internal_tenant_ids() -> list:
    """Tenants owned by an internal account.

    A tenant carries no e-mail of its own — the address lives on its users — so
    the exclusion has to resolve through the users table.
    """
    from app.models.user import User

    emails = internal_emails()
    if not emails:
        return []
    rows = (
        User.query.with_entities(User.tenant_id)
        .filter(User.tenant_id.isnot(None))
        .filter(_lower(User.email).in_(emails))
        .all()
    )
    return [row[0] for row in rows if row[0] is not None]


def exclude_users(query):
    """Drop internal accounts from a User query."""
    from app.models.user import User

    emails = internal_emails()
    if not emails:
        return query
    return query.filter(~_lower(User.email).in_(emails))


def exclude_tenants(query):
    """Drop internal accounts from a Tenant query."""
    from app.models.tenant import Tenant

    ids = internal_tenant_ids()
    if not ids:
        return query
    return query.filter(~Tenant.id.in_(ids))


def _lower(column):
    from sqlalchemy import func

    return func.lower(column)
