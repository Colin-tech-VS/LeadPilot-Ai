"""tenants.listing_prompt_answered_at: the « est-ce votre fiche ? » question

Asking it during sign-up sent the artisan back to a form with a blanked
password field; it is asked on the dashboard now, and this column records that
it has been answered so it is never asked twice.

Revision ID: o0p1q2r3s4t5
Revises: n9o0p1q2r3s4
Create Date: 2026-08-27
"""
from alembic import op
import sqlalchemy as sa


revision = "o0p1q2r3s4t5"
down_revision = "n9o0p1q2r3s4"
branch_labels = None
depends_on = None

_COLUMN = "listing_prompt_answered_at"


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


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "tenants" not in inspector.get_table_names():
        return

    columns = {col["name"] for col in inspector.get_columns("tenants")}
    if _COLUMN in columns:
        op.drop_column("tenants", _COLUMN)
