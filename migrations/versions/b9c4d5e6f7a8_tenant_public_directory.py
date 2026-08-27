"""tenant public directory fields

Revision ID: b9c4d5e6f7a8
Revises: a8b3c4d5e6f7
Create Date: 2026-07-06 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b9c4d5e6f7a8"
down_revision: Union[str, None] = "a8b3c4d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns():
    return (
        sa.Column("trade_type", sa.String(length=30), nullable=False, server_default="plombier"),
        sa.Column("public_slug", sa.String(length=100), nullable=True),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("public_blurb", sa.String(length=500), nullable=True),
    )


_INDEXES = (
    ("ix_tenants_trade_type", ["trade_type"], False),
    ("ix_tenants_public_slug", ["public_slug"], True),
    ("ix_tenants_is_public", ["is_public"], False),
)


def upgrade() -> None:
    # The production database predates this chain and already has these columns
    # (added at boot by ``_ensure_schema_updates``). Adding one twice aborts the
    # whole ``upgrade head`` and the later revisions never run, so check first.
    inspector = sa.inspect(op.get_bind())
    if "tenants" not in inspector.get_table_names():
        return

    existing = {col["name"] for col in inspector.get_columns("tenants")}
    for column in _columns():
        if column.name not in existing:
            op.add_column("tenants", column)

    indexes = {ix["name"] for ix in inspector.get_indexes("tenants")}
    for name, cols, unique in _INDEXES:
        if name not in indexes:
            op.create_index(op.f(name), "tenants", cols, unique=unique)


def downgrade() -> None:
    op.drop_index(op.f("ix_tenants_is_public"), table_name="tenants")
    op.drop_index(op.f("ix_tenants_public_slug"), table_name="tenants")
    op.drop_index(op.f("ix_tenants_trade_type"), table_name="tenants")
    op.drop_column("tenants", "public_blurb")
    op.drop_column("tenants", "is_public")
    op.drop_column("tenants", "public_slug")
    op.drop_column("tenants", "trade_type")
