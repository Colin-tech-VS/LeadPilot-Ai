"""email_campaigns + campaign_recipients: Brevo-style mailing campaigns

Revision ID: m8n9o0p1q2r3
Revises: l7m8n9o0p1q2
Create Date: 2026-08-26
"""
from alembic import op
import sqlalchemy as sa


revision = "m8n9o0p1q2r3"
down_revision = "l7m8n9o0p1q2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = sa.inspect(bind).get_table_names()

    if "email_campaigns" not in tables:
        op.create_table(
            "email_campaigns",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("name", sa.String(length=160), nullable=False, server_default="Nouvelle campagne"),
            sa.Column("subject", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("preheader", sa.String(length=255), nullable=True),
            sa.Column("from_name", sa.String(length=120), nullable=True),
            sa.Column("reply_to", sa.String(length=255), nullable=True),
            sa.Column("design_json", sa.Text(), nullable=True),
            sa.Column("html_body", sa.Text(), nullable=True),
            sa.Column("plain_body", sa.Text(), nullable=True),
            sa.Column("segment_json", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
            sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("ai_prompt", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_email_campaigns_status", "email_campaigns", ["status"])
        op.create_index("ix_email_campaigns_scheduled_at", "email_campaigns", ["scheduled_at"])
        op.create_index("ix_email_campaigns_created_at", "email_campaigns", ["created_at"])

    if "campaign_recipients" not in tables:
        op.create_table(
            "campaign_recipients",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("campaign_id", sa.Uuid(), nullable=False),
            sa.Column("prospect_id", sa.Uuid(), nullable=True),
            sa.Column("email", sa.String(length=255), nullable=False),
            sa.Column("first_name", sa.String(length=100), nullable=True),
            sa.Column("company_name", sa.String(length=255), nullable=True),
            sa.Column("city", sa.String(length=100), nullable=True),
            sa.Column("trade_type", sa.String(length=30), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
            sa.Column("email_message_id", sa.Uuid(), nullable=True),
            sa.Column("unsub_token", sa.String(length=64), nullable=False),
            sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("unsubscribed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("error", sa.String(length=500), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["campaign_id"], ["email_campaigns.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["prospect_id"], ["outreach_prospects.id"]),
            sa.ForeignKeyConstraint(["email_message_id"], ["email_messages.id"]),
            sa.UniqueConstraint("campaign_id", "email", name="uq_campaign_recipient_email"),
        )
        op.create_index("ix_campaign_recipients_campaign_id", "campaign_recipients", ["campaign_id"])
        op.create_index("ix_campaign_recipients_prospect_id", "campaign_recipients", ["prospect_id"])
        op.create_index("ix_campaign_recipients_email", "campaign_recipients", ["email"])
        op.create_index("ix_campaign_recipients_status", "campaign_recipients", ["status"])
        op.create_index("ix_campaign_recipients_email_message_id", "campaign_recipients", ["email_message_id"])
        op.create_index(
            "ix_campaign_recipients_unsub_token", "campaign_recipients", ["unsub_token"], unique=True
        )
        op.create_index(
            "ix_campaign_recipients_campaign_status", "campaign_recipients", ["campaign_id", "status"]
        )


def downgrade() -> None:
    op.drop_table("campaign_recipients")
    op.drop_table("email_campaigns")
