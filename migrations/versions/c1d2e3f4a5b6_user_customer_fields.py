"""user customer fields (particuliers)

Adds first_name / last_name / phone so a User can be a customer (role="customer",
no tenant) who books artisans online.

Idempotent: the dev bootstrap (``_ensure_schema_updates``) may already have added
these columns to a shared database, so each add is guarded by an inspector check
to keep ``alembic upgrade head`` from failing on deploy.

Revision ID: c1d2e3f4a5b6
Revises: b9c4d5e6f7a8
Create Date: 2026-07-06 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, None] = "b9c4d5e6f7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _existing_columns(table: str) -> set:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {col["name"] for col in inspector.get_columns(table)}


def upgrade() -> None:
    existing = _existing_columns("users")
    if "first_name" not in existing:
        op.add_column("users", sa.Column("first_name", sa.String(length=100), nullable=True))
    if "last_name" not in existing:
        op.add_column("users", sa.Column("last_name", sa.String(length=100), nullable=True))
    if "phone" not in existing:
        op.add_column("users", sa.Column("phone", sa.String(length=50), nullable=True))


def downgrade() -> None:
    existing = _existing_columns("users")
    if "phone" in existing:
        op.drop_column("users", "phone")
    if "last_name" in existing:
        op.drop_column("users", "last_name")
    if "first_name" in existing:
        op.drop_column("users", "first_name")
