"""Join a sourced prospect to its registry listing: prospect SIREN + frozen link

The two halves of artisan acquisition never touched: 12 000+ registry listings
(a page per company, with no e-mail — INSEE does not publish one) on one side,
and prospects sourced from the ADEME RGE register (an e-mail, and a SIRET that
was written into a free-text note and thrown away) on the other.

``outreach_prospects.siren`` is that SIRET promoted to a column, so a prospect
can be matched to the listing that carries their own company name.
``campaign_recipients.listing_siren`` freezes the match when the audience is
prepared, the same way the name and city are frozen.

Revision ID: p1q2r3s4t5u6
Revises: o0p1q2r3s4t5
Create Date: 2026-08-28
"""
from alembic import op
import sqlalchemy as sa


revision = "p1q2r3s4t5u6"
down_revision = "o0p1q2r3s4t5"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("outreach_prospects", "siren", True),
    ("campaign_recipients", "listing_siren", False),
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    for table, column, indexed in _COLUMNS:
        if table not in tables:
            continue
        if column in {col["name"] for col in inspector.get_columns(table)}:
            continue
        op.add_column(table, sa.Column(column, sa.String(length=9), nullable=True))
        if indexed:
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    for table, column, indexed in _COLUMNS:
        if table not in tables:
            continue
        if column not in {col["name"] for col in inspector.get_columns(table)}:
            continue
        if indexed:
            existing = {ix["name"] for ix in inspector.get_indexes(table)}
            if f"ix_{table}_{column}" in existing:
                op.drop_index(f"ix_{table}_{column}", table_name=table)
        op.drop_column(table, column)
