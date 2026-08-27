"""users.google_sub: link an account to the Google identity that signed in

Revision ID: n9o0p1q2r3s4
Revises: m8n9o0p1q2r3
Create Date: 2026-08-27
"""
from alembic import op
import sqlalchemy as sa


revision = "n9o0p1q2r3s4"
down_revision = "m8n9o0p1q2r3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "users" not in inspector.get_table_names():
        return

    columns = {col["name"] for col in inspector.get_columns("users")}
    if "google_sub" in columns:
        return

    op.add_column("users", sa.Column("google_sub", sa.String(length=64), nullable=True))
    op.create_index("ix_users_google_sub", "users", ["google_sub"], unique=True)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "users" not in inspector.get_table_names():
        return

    indexes = {ix["name"] for ix in inspector.get_indexes("users")}
    if "ix_users_google_sub" in indexes:
        op.drop_index("ix_users_google_sub", table_name="users")

    columns = {col["name"] for col in inspector.get_columns("users")}
    if "google_sub" in columns:
        op.drop_column("users", "google_sub")
