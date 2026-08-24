"""registry_listings: unclaimed directory entries from the public registry

Revision ID: k6l7m8n9o0p1
Revises: j5k6l7m8n9o0
Create Date: 2026-08-24
"""
from alembic import op
import sqlalchemy as sa


revision = "k6l7m8n9o0p1"
down_revision = "j5k6l7m8n9o0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "registry_listings" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "registry_listings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("siren", sa.String(length=9), nullable=False),
        sa.Column("siret", sa.String(length=14), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("trade_key", sa.String(length=30), nullable=False),
        sa.Column("naf_code", sa.String(length=10), nullable=True),
        sa.Column("address", sa.String(length=400), nullable=True),
        sa.Column("postal_code", sa.String(length=10), nullable=True),
        sa.Column("city", sa.String(length=120), nullable=True),
        sa.Column("city_slug", sa.String(length=140), nullable=True),
        sa.Column("dept_code", sa.String(length=5), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("date_creation", sa.String(length=10), nullable=True),
        sa.Column("employee_range", sa.String(length=10), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="listed"),
        sa.Column("claimed_tenant_id", sa.Uuid(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opted_out_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("siren", name="uq_registry_listings_siren"),
        sa.ForeignKeyConstraint(["claimed_tenant_id"], ["tenants.id"]),
    )
    op.create_index("ix_registry_listings_siren", "registry_listings", ["siren"])
    op.create_index("ix_registry_listings_trade_key", "registry_listings", ["trade_key"])
    op.create_index("ix_registry_listings_city_slug", "registry_listings", ["city_slug"])
    op.create_index("ix_registry_listings_postal_code", "registry_listings", ["postal_code"])
    op.create_index("ix_registry_listings_dept_code", "registry_listings", ["dept_code"])
    op.create_index("ix_registry_listings_status", "registry_listings", ["status"])
    op.create_index("ix_registry_listings_claimed_tenant_id", "registry_listings", ["claimed_tenant_id"])
    op.create_index("ix_registry_listings_trade_city", "registry_listings", ["trade_key", "city_slug"])


def downgrade() -> None:
    op.drop_table("registry_listings")
