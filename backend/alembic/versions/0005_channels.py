"""channels: sender mappings, channel message log

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-04
"""

from alembic import op
import sqlalchemy as sa

from app.db.rls import append_only_policy_sql, policy_sql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

APP_ROLE = "invoiceflow_app"


def upgrade() -> None:
    op.create_table(
        "sender_mappings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "firm_id", sa.Uuid(), sa.ForeignKey("firms.id", ondelete="CASCADE"), nullable=False, index=True
        ),
        sa.Column(
            "client_company_id",
            sa.Uuid(),
            sa.ForeignKey("client_companies.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("identity", sa.String(320), nullable=True),
        sa.Column("display_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("pairing_code", sa.String(16), nullable=True, unique=True),
        sa.Column("pairing_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("channel IN ('email','telegram','whatsapp')", name="sender_channel_known"),
    )
    op.create_index(
        "uq_sender_mappings_channel_identity",
        "sender_mappings",
        ["channel", "identity"],
        unique=True,
        postgresql_where=sa.text("identity IS NOT NULL"),
    )
    op.create_table(
        "channel_messages",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "firm_id", sa.Uuid(), sa.ForeignKey("firms.id", ondelete="CASCADE"), nullable=True, index=True
        ),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("direction", sa.String(3), nullable=False),
        sa.Column("sender_identity", sa.String(320), nullable=False, server_default=""),
        sa.Column("external_id", sa.String(200), nullable=True),
        sa.Column("document_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "uq_channel_messages_external",
        "channel_messages",
        ["channel", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL AND direction = 'in'"),
    )
    op.create_index("ix_channel_messages_document", "channel_messages", ["document_id"])
    for stmt in policy_sql("sender_mappings"):
        op.execute(stmt)
    for stmt in append_only_policy_sql("channel_messages"):
        op.execute(stmt)
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON sender_mappings TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON channel_messages TO {APP_ROLE}")
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}")


def downgrade() -> None:
    op.drop_table("channel_messages")
    op.drop_table("sender_mappings")
