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


_COLUMNS = (
    ("html_body", sa.Text()),
    ("cc_addrs", sa.String(length=500)),
    ("in_reply_to_id", sa.Uuid()),
    ("rfc_in_reply_to", sa.String(length=255)),
    ("references_header", sa.Text()),
    ("imap_uid", sa.String(length=64)),
    ("imap_folder", sa.String(length=64)),
    ("attachments_json", sa.Text()),
)


def upgrade() -> None:
    # The production database is older than this chain and already carries these
    # columns (added at boot by ``_ensure_schema_updates``). Adding one twice
    # aborts the whole ``upgrade head``, which then never reaches the revisions
    # that do have work to do — so every step checks before it acts.
    inspector = sa.inspect(op.get_bind())
    if "email_messages" not in inspector.get_table_names():
        return

    existing = {col["name"] for col in inspector.get_columns("email_messages")}
    for name, col_type in _COLUMNS:
        if name not in existing:
            op.add_column("email_messages", sa.Column(name, col_type, nullable=True))

    indexes = {ix["name"] for ix in inspector.get_indexes("email_messages")}
    if "ix_email_messages_imap_uid" not in indexes:
        op.create_index(
            op.f("ix_email_messages_imap_uid"), "email_messages", ["imap_uid"], unique=False
        )
    if "ix_email_messages_provider_id" not in indexes:
        op.create_index(
            op.f("ix_email_messages_provider_id"), "email_messages", ["provider_id"], unique=False
        )

    # SQLite cannot ALTER a constraint into an existing table; the FK is there
    # to protect the Postgres database this chain actually runs against.
    if op.get_bind().dialect.name == "sqlite":
        return

    constraints = {fk["name"] for fk in inspector.get_foreign_keys("email_messages")}
    if "fk_email_messages_in_reply_to" not in constraints:
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
