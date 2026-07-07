"""normalise outreach_prospects.id to native uuid

Revision ID: i4j5k6l7m8n9
Revises: h3i4j5k6l7m8
Create Date: 2026-07-07

The ``g2h3i4j5k6l7`` migration early-returns when ``outreach_prospects`` already
exists, so on databases where the table was created ahead of Alembic the ``id``
column can remain ``character varying`` instead of native ``uuid``. That breaks
``db.session.get(OutreachProspect, uuid)`` — which binds the parameter as
``::UUID`` on Postgres — with "operator does not exist: character varying = uuid"
when generating outreach emails. Convert the column in place when needed.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "i4j5k6l7m8n9"
down_revision = "h3i4j5k6l7m8"
branch_labels = None
depends_on = None


def _id_column_type(bind):
    inspector = inspect(bind)
    if "outreach_prospects" not in inspector.get_table_names():
        return None
    for col in inspector.get_columns("outreach_prospects"):
        if col["name"] == "id":
            return str(col["type"]).lower()
    return None


def upgrade():
    bind = op.get_bind()
    # Postgres only — SQLite has no native uuid type and stores it as a string.
    if bind.dialect.name != "postgresql":
        return
    col_type = _id_column_type(bind)
    if col_type is not None and "uuid" not in col_type:
        op.execute("ALTER TABLE outreach_prospects ALTER COLUMN id TYPE uuid USING id::uuid")


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    col_type = _id_column_type(bind)
    if col_type is not None and "uuid" in col_type:
        op.execute(
            "ALTER TABLE outreach_prospects ALTER COLUMN id TYPE varchar(36) USING id::text"
        )
