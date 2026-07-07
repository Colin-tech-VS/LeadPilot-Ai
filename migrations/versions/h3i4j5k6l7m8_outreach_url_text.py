"""widen outreach prospect url columns to text

Revision ID: h3i4j5k6l7m8
Revises: g2h3i4j5k6l7
Create Date: 2026-07-07

Search-engine redirect/tracking links (e.g. DuckDuckGo sponsored /y.js URLs)
routinely exceed 500 chars and were raising StringDataRightTruncation on
INSERT. Store website_url / source_url unbounded.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "h3i4j5k6l7m8"
down_revision = "g2h3i4j5k6l7"
branch_labels = None
depends_on = None


def _has_table(bind) -> bool:
    return "outreach_prospects" in inspect(bind).get_table_names()


def upgrade():
    bind = op.get_bind()
    if not _has_table(bind):
        return
    with op.batch_alter_table("outreach_prospects") as batch:
        batch.alter_column(
            "website_url",
            existing_type=sa.String(length=500),
            type_=sa.Text(),
            existing_nullable=True,
        )
        batch.alter_column(
            "source_url",
            existing_type=sa.String(length=500),
            type_=sa.Text(),
            existing_nullable=True,
        )


def downgrade():
    bind = op.get_bind()
    if not _has_table(bind):
        return
    with op.batch_alter_table("outreach_prospects") as batch:
        batch.alter_column(
            "source_url",
            existing_type=sa.Text(),
            type_=sa.String(length=500),
            existing_nullable=True,
        )
        batch.alter_column(
            "website_url",
            existing_type=sa.Text(),
            type_=sa.String(length=500),
            existing_nullable=True,
        )
