"""Create AskConversations and AskMessages tables for persistent chat history

Revision ID: 0011
Revises: 0010
Create Date: 2026-04-17

Context
-------
The Ask Analytics chatbot previously held conversation history only in the
browser's React state — refreshing the page lost everything.  This migration
adds two tables so conversations are persisted in SQL Server and can be
resumed across sessions:

  dbo.AskConversations — one row per conversation (sidebar list entry)
  dbo.AskMessages      — one row per message turn within a conversation

Design decisions
----------------
- ConversationId is UNIQUEIDENTIFIER (UUID) so it is safe to expose in URLs
  and API responses without leaking sequential row counts.
- Title is derived server-side from the first 80 characters of the first user
  message so the sidebar shows a meaningful label without extra client work.
- ExportJson stores the download-link metadata (format, url, size) as a JSON
  string so the message table stays schema-stable as export features evolve.
- ON DELETE CASCADE on AskMessages means deleting a conversation atomically
  removes all its messages in one statement.
- TenantId on AskConversations enforces the same tenant-isolation boundary
  used across the rest of the schema — all queries filter on it.
"""

from alembic import op
import sqlalchemy as sa


revision    = "0011"
down_revision = "0010"
branch_labels = None
depends_on    = None


def _table_exists(table: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT 1 FROM INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = :t"
    ), {"t": table})
    return result.fetchone() is not None


def upgrade() -> None:
    if not _table_exists("AskConversations"):
        op.create_table(
            "AskConversations",
            sa.Column("ConversationId", sa.String(36),     primary_key=True),
            sa.Column("UserId",         sa.Integer(),       nullable=False),
            sa.Column("TenantId",       sa.Integer(),       nullable=False),
            sa.Column("Title",          sa.Unicode(200),    nullable=False),
            sa.Column("CreatedAt",      sa.DateTime(),      nullable=False,
                      server_default=sa.text("GETUTCDATE()")),
            sa.Column("UpdatedAt",      sa.DateTime(),      nullable=False,
                      server_default=sa.text("GETUTCDATE()")),
            schema="dbo",
        )
        op.create_index(
            "IX_AskConversations_UserTenant",
            "AskConversations",
            ["TenantId", "UserId"],
            schema="dbo",
        )

    if not _table_exists("AskMessages"):
        op.create_table(
            "AskMessages",
            sa.Column("MessageId",      sa.BigInteger(),    primary_key=True,
                      autoincrement=True),
            sa.Column("ConversationId", sa.String(36),      nullable=False),
            sa.Column("Role",           sa.String(20),      nullable=False),
            sa.Column("Content",        sa.UnicodeText(),   nullable=False),
            sa.Column("ExportJson",     sa.UnicodeText(),   nullable=True),
            sa.Column("CreatedAt",      sa.DateTime(),      nullable=False,
                      server_default=sa.text("GETUTCDATE()")),
            schema="dbo",
        )
        op.create_index(
            "IX_AskMessages_ConversationId",
            "AskMessages",
            ["ConversationId"],
            schema="dbo",
        )
        op.create_foreign_key(
            "FK_AskMessages_ConversationId",
            "AskMessages", "AskConversations",
            ["ConversationId"], ["ConversationId"],
            source_schema="dbo", referent_schema="dbo",
            ondelete="CASCADE",
        )


def downgrade() -> None:
    op.drop_table("AskMessages",      schema="dbo")
    op.drop_table("AskConversations", schema="dbo")
