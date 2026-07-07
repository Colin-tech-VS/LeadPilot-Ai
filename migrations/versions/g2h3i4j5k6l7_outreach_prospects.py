"""outreach prospects table

Revision ID: g2h3i4j5k6l7
Revises: f1a2b3c4d5e6
Create Date: 2026-07-07
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "g2h3i4j5k6l7"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if "outreach_prospects" in inspect(bind).get_table_names():
        return

    op.create_table(
        "outreach_prospects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("first_name", sa.String(length=100), nullable=True),
        sa.Column("last_name", sa.String(length=100), nullable=True),
        sa.Column("company_name", sa.String(length=255), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=50), nullable=True),
        sa.Column("trade_type", sa.String(length=30), nullable=False, server_default="plombier"),
        sa.Column("city", sa.String(length=100), nullable=True),
        sa.Column("postal_code", sa.String(length=10), nullable=True),
        sa.Column("website_url", sa.String(length=500), nullable=True),
        sa.Column("source_url", sa.String(length=500), nullable=True),
        sa.Column("source", sa.String(length=50), nullable=False, server_default="web_search"),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="new"),
        sa.Column("email_confidence", sa.String(length=20), nullable=True),
        sa.Column("search_query", sa.String(length=500), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("outreach_subject", sa.String(length=255), nullable=True),
        sa.Column("outreach_body", sa.Text(), nullable=True),
        sa.Column("last_contacted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opted_out_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_outreach_prospects_email", "outreach_prospects", ["email"])
    op.create_index("ix_outreach_prospects_trade_type", "outreach_prospects", ["trade_type"])
    op.create_index("ix_outreach_prospects_city", "outreach_prospects", ["city"])
    op.create_index("ix_outreach_prospects_status", "outreach_prospects", ["status"])


def downgrade():
    bind = op.get_bind()
    if "outreach_prospects" not in inspect(bind).get_table_names():
        return
    op.drop_index("ix_outreach_prospects_status", table_name="outreach_prospects")
    op.drop_index("ix_outreach_prospects_city", table_name="outreach_prospects")
    op.drop_index("ix_outreach_prospects_trade_type", table_name="outreach_prospects")
    op.drop_index("ix_outreach_prospects_email", table_name="outreach_prospects")
    op.drop_table("outreach_prospects")
