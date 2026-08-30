"""tenants.line_requested_at: when the artisan asked for their own line

A trial without a dedicated Twilio number has no receptionist at all — calls to
the shared line route to TWILIO_DEFAULT_TENANT_ID — so the free trial expired
without a single call handled. The line is now bought on request instead of on
payment, and this column records that request.

Revision ID: q2r3s4t5u6v7
Revises: p1q2r3s4t5u6
Create Date: 2026-08-30
"""
from alembic import op
import sqlalchemy as sa


revision = "q2r3s4t5u6v7"
down_revision = "p1q2r3s4t5u6"
branch_labels = None
depends_on = None

_COLUMN = "line_requested_at"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "tenants" not in inspector.get_table_names():
        return

    columns = {col["name"] for col in inspector.get_columns("tenants")}
    if _COLUMN in columns:
        return

    op.add_column(
        "tenants", sa.Column(_COLUMN, sa.DateTime(timezone=True), nullable=True)
    )

    # Every artisan already paying has a line and never has to ask for it;
    # backfilling them keeps the activation step out of their dashboard.
    op.execute(
        "UPDATE tenants SET line_requested_at = COALESCE(line_requested_at, created_at) "
        "WHERE ai_phone_number IS NOT NULL"
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "tenants" not in inspector.get_table_names():
        return

    columns = {col["name"] for col in inspector.get_columns("tenants")}
    if _COLUMN in columns:
        op.drop_column("tenants", _COLUMN)
