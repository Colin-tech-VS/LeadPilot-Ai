"""email mailbox fields

Revision ID: a8b3c4d5e6f7
Revises: 27f2ccdb4231
Create Date: 2026-07-06 12:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a8b3c4d5e6f7"
down_revision: Union[str, None] = "27f2ccdb4231"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("email_messages", sa.Column("html_body", sa.Text(), nullable=True))
    op.add_column("email_messages", sa.Column("cc_addrs", sa.String(length=500), nullable=True))
    op.add_column("email_messages", sa.Column("in_reply_to_id", sa.Uuid(), nullable=True))
    op.add_column("email_messages", sa.Column("rfc_in_reply_to", sa.String(length=255), nullable=True))
    op.add_column("email_messages", sa.Column("references_header", sa.Text(), nullable=True))
    op.add_column("email_messages", sa.Column("imap_uid", sa.String(length=64), nullable=True))
    op.add_column("email_messages", sa.Column("imap_folder", sa.String(length=64), nullable=True))
    op.add_column("email_messages", sa.Column("attachments_json", sa.Text(), nullable=True))
    op.create_index(
        op.f("ix_email_messages_imap_uid"), "email_messages", ["imap_uid"], unique=False
    )
    op.create_index(
        op.f("ix_email_messages_provider_id"), "email_messages", ["provider_id"], unique=False
    )
    op.create_foreign_key(
        "fk_email_messages_in_reply_to",
        "email_messages",
        "email_messages",
        ["in_reply_to_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_email_messages_in_reply_to", "email_messages", type_="foreignkey")
    op.drop_index(op.f("ix_email_messages_provider_id"), table_name="email_messages")
    op.drop_index(op.f("ix_email_messages_imap_uid"), table_name="email_messages")
    op.drop_column("email_messages", "attachments_json")
    op.drop_column("email_messages", "imap_folder")
    op.drop_column("email_messages", "imap_uid")
    op.drop_column("email_messages", "references_header")
    op.drop_column("email_messages", "rfc_in_reply_to")
    op.drop_column("email_messages", "in_reply_to_id")
    op.drop_column("email_messages", "cc_addrs")
    op.drop_column("email_messages", "html_body")
