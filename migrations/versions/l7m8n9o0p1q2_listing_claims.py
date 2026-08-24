"""listing_claims: ownership requests on registry listings

Revision ID: l7m8n9o0p1q2
Revises: k6l7m8n9o0p1
Create Date: 2026-08-24
"""
from alembic import op
import sqlalchemy as sa


revision = "l7m8n9o0p1q2"
down_revision = "k6l7m8n9o0p1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "listing_claims" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "listing_claims",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("listing_id", sa.Uuid(), nullable=False),
        sa.Column("siren", sa.String(length=9), nullable=False),
        sa.Column("contact_name", sa.String(length=160), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("phone", sa.String(length=50), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("created_tenant_id", sa.Uuid(), nullable=True),
        sa.Column("ip_hash", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["listing_id"], ["registry_listings.id"]),
        sa.ForeignKeyConstraint(["created_tenant_id"], ["tenants.id"]),
    )
    op.create_index("ix_listing_claims_listing_id", "listing_claims", ["listing_id"])
    op.create_index("ix_listing_claims_siren", "listing_claims", ["siren"])
    op.create_index("ix_listing_claims_email", "listing_claims", ["email"])
    op.create_index("ix_listing_claims_status", "listing_claims", ["status"])


def downgrade() -> None:
    op.drop_table("listing_claims")
