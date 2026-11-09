"""users and 2FA, platform and super admin, settings and domains, routing, channels,
trash, scoped integrations, backups

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-09
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.db.rls import policy_sql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

APP_ROLE = "invoiceflow_app"
TENANT_TABLES: list[str] = []


def _tenant(table: str) -> None:
    TENANT_TABLES.append(table)


def upgrade() -> None:
    # ---- A: users, links, tombstones ---------------------------------------------------------
    op.add_column(
        "users", sa.Column("recovery_codes", postgresql.JSONB(), nullable=False, server_default="[]")
    )
    op.add_column("users", sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("password_set_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "user_sessions",
        sa.Column("acting_firm_id", sa.Uuid(), sa.ForeignKey("firms.id", ondelete="SET NULL"), nullable=True),
    )
    op.add_column("user_sessions", sa.Column("meta", postgresql.JSONB(), nullable=False, server_default="{}"))
    op.create_table(
        "user_links",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "firm_id", sa.Uuid(), sa.ForeignKey("firms.id", ondelete="CASCADE"), nullable=True, index=True
        ),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("kind IN ('invite','reset')", name="user_link_kind_known"),
    )
    _tenant("user_links")
    op.create_table(
        "user_tombstones",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "firm_id", sa.Uuid(), sa.ForeignKey("firms.id", ondelete="CASCADE"), nullable=False, index=True
        ),
        sa.Column("user_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("label", sa.String(64), nullable=False),
        sa.Column("identity_enc", sa.LargeBinary(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("deleted_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("erase_after", sa.DateTime(timezone=True), nullable=False),
    )
    _tenant("user_tombstones")
    op.add_column(
        "sender_mappings", sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true())
    )

    # ---- I: platform ----------------------------------------------------------------------------
    op.add_column("firms", sa.Column("kind", sa.String(16), nullable=False, server_default="firm"))
    op.add_column("firms", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("firms", sa.Column("erase_after", sa.DateTime(timezone=True), nullable=True))
    op.add_column("firms", sa.Column("deletion_reason", sa.String(500), nullable=True))
    op.create_check_constraint("firm_kind_known", "firms", "kind IN ('firm','platform')")
    op.create_index(
        "uq_firms_one_platform", "firms", ["kind"], unique=True, postgresql_where=sa.text("kind = 'platform'")
    )
    op.create_table(
        "platform_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("setup_nonce", sa.String(64), nullable=True),
        sa.Column("setup_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("platform_firm_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("id = 1", name="platform_state_singleton"),
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON platform_state TO {APP_ROLE}")

    # ---- J: settings in the dashboard ---------------------------------------------------------
    op.create_table(
        "config_entries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "firm_id", sa.Uuid(), sa.ForeignKey("firms.id", ondelete="CASCADE"), nullable=False, index=True
        ),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("value", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("secrets_enc", sa.LargeBinary(), nullable=True),
        sa.Column("source", sa.String(16), nullable=False, server_default="dashboard"),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("firm_id", "key", name="config_entries_firm_key"),
    )
    _tenant("config_entries")
    op.create_table(
        "inbound_domains",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "firm_id", sa.Uuid(), sa.ForeignKey("firms.id", ondelete="CASCADE"), nullable=False, index=True
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("domain", sa.String(253), nullable=False, unique=True),
        sa.Column("label", sa.String(63), nullable=True),
        sa.Column("verification_token", sa.String(64), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("last_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_check_error", sa.String(200), nullable=True),
        sa.Column("mx_hosts", sa.String(500), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("grace_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("kind IN ('root','platform_sub','custom')", name="inbound_domain_kind_known"),
    )
    _tenant("inbound_domains")

    # ---- C: retired inbound addresses --------------------------------------------------------
    op.create_table(
        "inbound_addresses",
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
        sa.Column("local_part", sa.String(64), nullable=False, unique=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("grace_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.String(500), nullable=False, server_default=""),
        sa.Column("retired_by_user_id", sa.Uuid(), nullable=True),
    )
    _tenant("inbound_addresses")

    # ---- D: bots, chat pairings, multi-company senders ---------------------------------------
    op.create_table(
        "channel_bots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "firm_id", sa.Uuid(), sa.ForeignKey("firms.id", ondelete="CASCADE"), nullable=False, index=True
        ),
        sa.Column(
            "client_company_id",
            sa.Uuid(),
            sa.ForeignKey("client_companies.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("public", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("secrets_enc", sa.LargeBinary(), nullable=False),
        sa.Column("accept_from", sa.String(16), nullable=False, server_default="anyone"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_error", sa.String(300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("channel IN ('telegram','whatsapp')", name="channel_bot_channel_known"),
    )
    _tenant("channel_bots")
    op.create_table(
        "sender_selections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "firm_id", sa.Uuid(), sa.ForeignKey("firms.id", ondelete="CASCADE"), nullable=False, index=True
        ),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("identity", sa.String(320), nullable=False, index=True),
        sa.Column(
            "client_company_id",
            sa.Uuid(),
            sa.ForeignKey("client_companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("set_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    _tenant("sender_selections")
    op.create_table(
        "held_messages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("identity", sa.String(320), nullable=False, index=True),
        sa.Column("bot_id", sa.Uuid(), nullable=True),
        sa.Column("reply_to", sa.String(320), nullable=False, server_default=""),
        sa.Column("options", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("files", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("text", sa.String(2000), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON held_messages TO {APP_ROLE}")
    op.add_column(
        "sender_mappings", sa.Column("scope", sa.String(8), nullable=False, server_default="person")
    )
    op.add_column("sender_mappings", sa.Column("bot_id", sa.Uuid(), nullable=True))
    op.add_column(
        "sender_mappings", sa.Column("accept_from", sa.String(16), nullable=False, server_default="anyone")
    )
    op.drop_index("uq_sender_mappings_channel_identity", table_name="sender_mappings")
    op.create_index(
        "uq_sender_mappings_identity_client",
        "sender_mappings",
        ["channel", "scope", "identity", "client_company_id"],
        unique=True,
        postgresql_where=sa.text("identity IS NOT NULL"),
    )
    op.add_column("documents", sa.Column("channel_bot_id", sa.Uuid(), nullable=True))

    # ---- E: trash -----------------------------------------------------------------------------
    op.add_column("documents", sa.Column("deleted_by_user_id", sa.Uuid(), nullable=True))
    op.add_column("documents", sa.Column("delete_reason", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("purge_after", sa.DateTime(timezone=True), nullable=True))
    op.add_column("documents", sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "trash_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "firm_id", sa.Uuid(), sa.ForeignKey("firms.id", ondelete="CASCADE"), nullable=False, index=True
        ),
        sa.Column("requested_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("document_ids", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("filter", postgresql.JSONB(), nullable=True),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("decided_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("self_approved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("executed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    _tenant("trash_requests")

    # ---- F + G: API keys and webhooks per client company ------------------------------------
    op.add_column(
        "api_keys", sa.Column("client_company_ids", postgresql.JSONB(), nullable=False, server_default="[]")
    )
    op.add_column(
        "api_keys", sa.Column("allowed_cidrs", postgresql.JSONB(), nullable=False, server_default="[]")
    )
    op.add_column("api_keys", sa.Column("description", sa.String(300), nullable=False, server_default=""))
    op.add_column("api_keys", sa.Column("last_used_ip", sa.String(64), nullable=True))
    op.add_column(
        "webhook_endpoints",
        sa.Column("client_company_ids", postgresql.JSONB(), nullable=False, server_default="[]"),
    )
    # defence in depth (F): a client-scoped principal sets app.client_scope; these RESTRICTIVE policies
    # are AND-ed with the tenant policies, so even a forgotten application filter cannot leak a row
    scope = (
        "(coalesce(current_setting('app.client_scope', true), '') = '' "
        "OR current_setting('app.bypass_rls', true) = 'on' "
        "OR {col}::text = ANY (string_to_array(current_setting('app.client_scope', true), ',')))"
    )
    op.execute(
        "CREATE POLICY documents_client_scope ON documents AS RESTRICTIVE FOR ALL "
        f"USING {scope.format(col='client_company_id')} WITH CHECK {scope.format(col='client_company_id')}"
    )
    op.execute(
        "CREATE POLICY client_companies_client_scope ON client_companies AS RESTRICTIVE FOR ALL "
        f"USING {scope.format(col='id')} WITH CHECK {scope.format(col='id')}"
    )

    # ---- K: backups (platform-level metadata: no RLS, filtered by owner in the application) -----
    op.create_table(
        "backups",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("trigger", sa.String(16), nullable=False, server_default="manual"),
        sa.Column("target", sa.String(16), nullable=False, server_default="storage"),
        sa.Column("object_key", sa.String(512), nullable=False, server_default=""),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("sha256", sa.String(64), nullable=False, server_default=""),
        sa.Column("schema_revision", sa.String(32), nullable=False, server_default=""),
        sa.Column("app_version", sa.String(32), nullable=False, server_default=""),
        sa.Column("counts", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("password_enc", sa.LargeBinary(), nullable=True),
        sa.Column("reveals_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("recovery_key_id", sa.Uuid(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("error", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "recovery_keys",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "owner_id", sa.Uuid(), sa.ForeignKey("firms.id", ondelete="CASCADE"), nullable=False, index=True
        ),
        sa.Column("public_key", sa.String(100), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "restores",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("backup_id", sa.Uuid(), nullable=True),
        sa.Column("upload_key", sa.String(512), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("mode", sa.String(16), nullable=False, server_default="replace"),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("secret_enc", sa.LargeBinary(), nullable=True),
        sa.Column("pre_backup_id", sa.Uuid(), nullable=True),
        sa.Column("requested_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("error", sa.String(500), nullable=True),
        sa.Column("counts", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    for t in ("backups", "recovery_keys", "restores"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {t} TO {APP_ROLE}")
    op.execute(f"GRANT SELECT ON alembic_version TO {APP_ROLE}")

    # ---- shared: RLS and grants for every new tenant table --------------------------------------
    for table in TENANT_TABLES:
        for stmt in policy_sql(table):
            op.execute(stmt)
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {APP_ROLE}")
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}")


def downgrade() -> None:
    for t in ("restores", "recovery_keys", "backups"):
        op.drop_table(t)
    op.execute("DROP POLICY IF EXISTS client_companies_client_scope ON client_companies")
    op.execute("DROP POLICY IF EXISTS documents_client_scope ON documents")
    op.drop_column("webhook_endpoints", "client_company_ids")
    for col in ("last_used_ip", "description", "allowed_cidrs", "client_company_ids"):
        op.drop_column("api_keys", col)
    op.drop_table("trash_requests")
    for col in ("purged_at", "purge_after", "delete_reason", "deleted_by_user_id"):
        op.drop_column("documents", col)
    op.drop_column("documents", "channel_bot_id")
    op.drop_index("uq_sender_mappings_identity_client", table_name="sender_mappings")
    op.create_index(
        "uq_sender_mappings_channel_identity",
        "sender_mappings",
        ["channel", "identity"],
        unique=True,
        postgresql_where=sa.text("identity IS NOT NULL"),
    )
    for col in ("accept_from", "bot_id", "scope"):
        op.drop_column("sender_mappings", col)
    op.drop_table("held_messages")
    op.drop_table("sender_selections")
    op.drop_table("channel_bots")
    op.drop_table("inbound_addresses")
    op.drop_table("inbound_domains")
    op.drop_table("config_entries")
    op.drop_table("platform_state")
    op.drop_index("uq_firms_one_platform", table_name="firms")
    op.drop_constraint("firm_kind_known", "firms", type_="check")
    for col in ("deletion_reason", "erase_after", "deleted_at", "kind"):
        op.drop_column("firms", col)
    op.drop_column("sender_mappings", "active")
    op.drop_table("user_tombstones")
    op.drop_table("user_links")
    op.drop_column("user_sessions", "meta")
    op.drop_column("user_sessions", "acting_firm_id")
    for col in ("password_set_at", "deleted_at", "deactivated_at", "recovery_codes"):
        op.drop_column("users", col)
