"""initial schema: firms, users, sessions, client companies, documents, audit, licensing; RLS

Revision ID: 0001
Revises:
Create Date: 2026-11-04
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.db.rls import append_only_policy_sql, policy_sql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

APP_ROLE = "invoiceflow_app"


def upgrade() -> None:
    op.execute(f"""
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
            CREATE ROLE {APP_ROLE} NOLOGIN;
          END IF;
        END $$;
        """)

    op.create_table(
        "firms",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("country", sa.String(2), nullable=False, server_default="DE"),
        sa.Column("locale", sa.String(8), nullable=False, server_default="en"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("settings", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("ai_routing_policy", sa.String(32), nullable=False, server_default="local_only"),
        sa.Column("retention_days", sa.Integer(), nullable=False, server_default="3650"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("data_key_wrapped", sa.LargeBinary(), nullable=False),
        sa.Column("instance_id", sa.String(38), nullable=False, unique=True),
        sa.Column("instance_fp", sa.String(8), nullable=False),
        sa.Column("instance_fp_source", sa.String(16), nullable=False, server_default="random"),
        sa.Column("license_key_enc", sa.LargeBinary(), nullable=True),
        sa.Column("license_token_enc", sa.LargeBinary(), nullable=True),
        sa.Column("license_use", sa.String(8), nullable=True),
        sa.Column("license_id", sa.String(64), nullable=True),
        sa.Column("activation_id", sa.String(64), nullable=True),
        sa.Column("key_hint", sa.String(5), nullable=True),
        sa.Column("license_state", sa.String(16), nullable=False, server_default="unlicensed"),
        sa.Column("license_state_reason", sa.String(64), nullable=True),
        sa.Column("last_seen", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("last_refresh_at", sa.BigInteger(), nullable=True),
        sa.Column("last_validate_call_at", sa.BigInteger(), nullable=True),
        sa.Column("next_refresh_at", sa.BigInteger(), nullable=True),
        sa.Column("refresh_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("backoff_until", sa.BigInteger(), nullable=True),
        sa.Column("blocked_until", sa.BigInteger(), nullable=True),
        sa.Column("reactivation_attempted_at", sa.BigInteger(), nullable=True),
        sa.Column("hub_message", sa.String(500), nullable=True),
        sa.Column("hub_error", sa.String(64), nullable=True),
        sa.Column("shown_message_ids", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("revoked_by_list_at", sa.BigInteger(), nullable=True),
    )
    op.create_index("ix_firms_license_state", "firms", ["license_state"])
    op.create_index("ix_firms_next_refresh_at", "firms", ["next_refresh_at"])

    op.create_table(
        "client_companies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "firm_id", sa.Uuid(), sa.ForeignKey("firms.id", ondelete="CASCADE"), nullable=False, index=True
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("country", sa.String(2), nullable=False, server_default="DE"),
        sa.Column("inbound_email_local_part", sa.String(64), nullable=True, unique=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("settings", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "legal_entities",
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
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("vat_id", sa.String(32), nullable=True),
        sa.Column("tax_id", sa.String(32), nullable=True),
        sa.Column("address", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "firm_id", sa.Uuid(), sa.ForeignKey("firms.id", ondelete="CASCADE"), nullable=True, index=True
        ),
        sa.Column(
            "client_company_id",
            sa.Uuid(),
            sa.ForeignKey("client_companies.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("display_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("locale", sa.String(8), nullable=False, server_default="en"),
        sa.Column("totp_secret_enc", sa.LargeBinary(), nullable=True),
        sa.Column("totp_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "role IN ('super_admin','firm_owner','accountant','junior_accountant','client_admin','client_user')",
            name="role_known",
        ),
        sa.CheckConstraint("(role = 'super_admin') = (firm_id IS NULL)", name="super_admin_has_no_firm"),
    )

    op.create_table(
        "user_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "firm_id", sa.Uuid(), sa.ForeignKey("firms.id", ondelete="CASCADE"), nullable=True, index=True
        ),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("mfa_passed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "firm_id", sa.Uuid(), sa.ForeignKey("firms.id", ondelete="CASCADE"), nullable=False, index=True
        ),
        sa.Column(
            "client_company_id",
            sa.Uuid(),
            sa.ForeignKey("client_companies.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("sender_identity", sa.String(320), nullable=False, server_default=""),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False, server_default=""),
        sa.Column("mime_type", sa.String(127), nullable=False, server_default="application/octet-stream"),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("sha256", sa.String(64), nullable=False, index=True),
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="received"),
        sa.Column("uploaded_by_user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("extracted", postgresql.JSONB(), nullable=True),
        sa.Column("retention_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_restricted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_documents_firm_received", "documents", ["firm_id", "received_at"])

    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "firm_id", sa.Uuid(), sa.ForeignKey("firms.id", ondelete="CASCADE"), nullable=True, index=True
        ),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("actor_role", sa.String(32), nullable=True),
        sa.Column("action", sa.String(64), nullable=False, index=True),
        sa.Column("target_type", sa.String(64), nullable=False),
        sa.Column("target_id", sa.String(128), nullable=True),
        sa.Column("result", sa.String(64), nullable=False, server_default="ok"),
        sa.Column("before", postgresql.JSONB(), nullable=True),
        sa.Column("after", postgresql.JSONB(), nullable=True),
        sa.Column("ip", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "license_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "firm_id", sa.Uuid(), sa.ForeignKey("firms.id", ondelete="CASCADE"), nullable=False, index=True
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("outcome", sa.String(64), nullable=False),
        sa.Column("state_before", sa.String(16), nullable=True),
        sa.Column("state_after", sa.String(16), nullable=True),
        sa.Column("detail", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "revocation_list_cache",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("jws", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.BigInteger(), nullable=False),
        sa.Column("list_exp", sa.BigInteger(), nullable=False),
    )

    # --- Row-Level Security ---------------------------------------------------------------
    # firms: a tenant sees only its own row; the platform bypass sees all.
    for stmt in policy_sql("firms", column="id"):
        op.execute(stmt)
    for table in ("client_companies", "legal_entities", "documents", "user_sessions"):
        for stmt in policy_sql(table):
            op.execute(stmt)
    # users: super_admin rows have firm_id NULL and are reachable only with the bypass
    for stmt in policy_sql("users"):
        op.execute(stmt)
    for stmt in append_only_policy_sql("audit_log"):
        op.execute(stmt)
    for stmt in append_only_policy_sql("license_events"):
        op.execute(stmt)

    # The app role (members: the login role the API uses) gets DML, never DDL, never bypass.
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}")
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE}"
    )
    op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {APP_ROLE}")
    # audit tables: the app role may never update or delete, policy or not
    op.execute(f"REVOKE UPDATE, DELETE ON audit_log FROM {APP_ROLE}")
    op.execute(f"REVOKE UPDATE, DELETE ON license_events FROM {APP_ROLE}")


def downgrade() -> None:
    for table in (
        "revocation_list_cache",
        "license_events",
        "audit_log",
        "documents",
        "user_sessions",
        "users",
        "legal_entities",
        "client_companies",
        "firms",
    ):
        op.drop_table(table)
