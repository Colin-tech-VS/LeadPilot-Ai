"""quote client signature and deposit payment fields

Revision ID: d4e5f6a7b8c9
Revises: c1d2e3f4a5b6
Create Date: 2026-07-07 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c1d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _existing_columns(table: str) -> set:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {col["name"] for col in inspector.get_columns(table)}


def upgrade() -> None:
    existing = _existing_columns("quotes")
    ts_type = sa.DateTime(timezone=True)
    patches = {
        "client_signed_name": sa.String(length=255),
        "client_signed_at": ts_type,
        "deposit_paid_at": ts_type,
        "stripe_deposit_session_id": sa.String(length=255),
    }
    for name, col in patches.items():
        if name not in existing:
            op.add_column("quotes", sa.Column(name, col, nullable=True))


def downgrade() -> None:
    existing = _existing_columns("quotes")
    for name in (
        "stripe_deposit_session_id",
        "deposit_paid_at",
        "client_signed_at",
        "client_signed_name",
    ):
        if name in existing:
            op.drop_column("quotes", name)
