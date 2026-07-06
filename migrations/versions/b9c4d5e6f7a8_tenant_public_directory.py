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


def upgrade() -> None:
    op.add_column("tenants", sa.Column("trade_type", sa.String(length=30), nullable=False, server_default="plombier"))
    op.add_column("tenants", sa.Column("public_slug", sa.String(length=100), nullable=True))
    op.add_column("tenants", sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("tenants", sa.Column("public_blurb", sa.String(length=500), nullable=True))
    op.create_index(op.f("ix_tenants_trade_type"), "tenants", ["trade_type"], unique=False)
    op.create_index(op.f("ix_tenants_public_slug"), "tenants", ["public_slug"], unique=True)
    op.create_index(op.f("ix_tenants_is_public"), "tenants", ["is_public"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_tenants_is_public"), table_name="tenants")
    op.drop_index(op.f("ix_tenants_public_slug"), table_name="tenants")
    op.drop_index(op.f("ix_tenants_trade_type"), table_name="tenants")
    op.drop_column("tenants", "public_blurb")
    op.drop_column("tenants", "is_public")
    op.drop_column("tenants", "public_slug")
    op.drop_column("tenants", "trade_type")
