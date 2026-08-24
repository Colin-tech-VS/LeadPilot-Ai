"""trade_guides: cached SEO content per (trade, lang)

Revision ID: j5k6l7m8n9o0
Revises: i4j5k6l7m8n9
Create Date: 2026-08-24

Adds the ``trade_guides`` table that stores Mistral-generated long-form
content (intro, body, FAQ, prices) attached to trade pillar pages so the
public directory landing pages carry unique, indexable substance instead
of thin placeholder text.
"""
from alembic import op
import sqlalchemy as sa


revision = "j5k6l7m8n9o0"
down_revision = "i4j5k6l7m8n9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "trade_guides" in inspector.get_table_names():
        return
    op.create_table(
        "trade_guides",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("trade_key", sa.String(length=40), nullable=False),
        sa.Column("lang", sa.String(length=5), nullable=False, server_default="fr"),
        sa.Column("intro_html", sa.Text(), nullable=True),
        sa.Column("body_html", sa.Text(), nullable=True),
        sa.Column("faq_json", sa.Text(), nullable=True),
        sa.Column("price_hints", sa.Text(), nullable=True),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("trade_key", "lang", name="uq_trade_guides_trade_lang"),
    )
    op.create_index("ix_trade_guides_trade_key", "trade_guides", ["trade_key"])


def downgrade() -> None:
    op.drop_index("ix_trade_guides_trade_key", table_name="trade_guides")
    op.drop_table("trade_guides")
