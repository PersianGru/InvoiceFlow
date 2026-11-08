"""compliance: login lockout, erasure requests, SSO configuration, privileged retention functions

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-04
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.db.rls import policy_sql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

APP_ROLE = "invoiceflow_app"


def upgrade() -> None:
    op.add_column("users", sa.Column("failed_logins", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("last_failed_login_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("documents", sa.Column("erasure_requested_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("documents", sa.Column("retention_rule", sa.String(64), nullable=True))

    op.create_table(
        "firm_sso",
        sa.Column("firm_id", sa.Uuid(), sa.ForeignKey("firms.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("issuer", sa.String(500), nullable=False, server_default=""),
        sa.Column("client_id", sa.String(300), nullable=False, server_default=""),
        sa.Column("client_secret_enc", sa.LargeBinary(), nullable=True),
        sa.Column("allowed_domains", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("provisioning", sa.String(16), nullable=False, server_default="invited_only"),
        sa.Column("default_role", sa.String(32), nullable=False, server_default="junior_accountant"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "sso_logins",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "firm_id", sa.Uuid(), sa.ForeignKey("firms.id", ondelete="CASCADE"), nullable=False, index=True
        ),
        sa.Column("state", sa.String(64), nullable=False, unique=True),
        sa.Column("nonce", sa.String(64), nullable=False),
        sa.Column("code_verifier", sa.String(128), nullable=False),
        sa.Column("redirect_to", sa.String(500), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
    )
    for table in ("firm_sso", "sso_logins"):
        for stmt in policy_sql(table):
            op.execute(stmt)
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {APP_ROLE}")

    # Append-only tables cannot be updated by the app role; these owner-defined functions do the
    # minimisation the law asks for (audit IPs after 12 months, channel texts after 12 months).
    op.execute("""
        CREATE OR REPLACE FUNCTION scrub_audit_ips(p_before timestamptz) RETURNS integer
        LANGUAGE plpgsql SECURITY DEFINER AS $$
        DECLARE n integer;
        BEGIN
            UPDATE audit_log SET ip = NULL WHERE ip IS NOT NULL AND created_at < p_before;
            GET DIAGNOSTICS n = ROW_COUNT;
            RETURN n;
        END $$
        """)
    op.execute("""
        CREATE OR REPLACE FUNCTION purge_channel_messages(p_before timestamptz) RETURNS integer
        LANGUAGE plpgsql SECURITY DEFINER AS $$
        DECLARE n integer;
        BEGIN
            DELETE FROM channel_messages WHERE created_at < p_before;
            GET DIAGNOSTICS n = ROW_COUNT;
            RETURN n;
        END $$
        """)
    op.execute(f"GRANT EXECUTE ON FUNCTION scrub_audit_ips(timestamptz) TO {APP_ROLE}")
    op.execute(f"GRANT EXECUTE ON FUNCTION purge_channel_messages(timestamptz) TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS purge_channel_messages(timestamptz)")
    op.execute("DROP FUNCTION IF EXISTS scrub_audit_ips(timestamptz)")
    op.drop_table("sso_logins")
    op.drop_table("firm_sso")
    op.drop_column("documents", "retention_rule")
    op.drop_column("documents", "erasure_requested_at")
    op.drop_column("users", "locked_until")
    op.drop_column("users", "last_failed_login_at")
    op.drop_column("users", "failed_logins")
