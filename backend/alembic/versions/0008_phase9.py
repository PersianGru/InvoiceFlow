"""network identifiers, payment status, per-client connector credentials, bank, ViDA

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-04
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.db.rls import policy_sql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

APP_ROLE = "invoiceflow_app"
NEW_TABLES = (
    "client_connector_credentials",
    "bank_accounts",
    "bank_statements",
    "bank_transactions",
    "payment_matches",
    "bank_feeds",
    "vida_reports",
)


def upgrade() -> None:
    op.add_column("client_companies", sa.Column("peppol_id", sa.String(64), nullable=True))
    op.add_column("client_companies", sa.Column("tax_number", sa.String(32), nullable=True))
    op.create_index("ix_client_companies_peppol_id", "client_companies", ["peppol_id"])
    op.create_index("ix_client_companies_tax_number", "client_companies", ["tax_number"])
    op.add_column(
        "documents", sa.Column("payment_status", sa.String(16), nullable=False, server_default="unpaid")
    )
    op.add_column("documents", sa.Column("paid_amount", sa.Numeric(18, 2), nullable=True))
    op.add_column("documents", sa.Column("paid_at", sa.Date(), nullable=True))

    op.create_table(
        "client_connector_credentials",
        sa.Column("firm_id", sa.Uuid(), sa.ForeignKey("firms.id", ondelete="CASCADE"), primary_key=True),
        sa.Column(
            "client_company_id",
            sa.Uuid(),
            sa.ForeignKey("client_companies.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("key", sa.String(32), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("config_enc", sa.LargeBinary(), nullable=True),
        sa.Column("settings", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("cursor", sa.String(200), nullable=True),
        sa.Column("last_pull_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "bank_accounts",
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
        sa.Column("iban", sa.String(34), nullable=False),
        sa.Column("bic", sa.String(11), nullable=True),
        sa.Column("name", sa.String(200), nullable=False, server_default=""),
        sa.Column("currency", sa.String(3), nullable=False, server_default="EUR"),
        sa.Column("source", sa.String(16), nullable=False, server_default="file"),
        sa.Column("external_id", sa.String(100), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cursor", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("firm_id", "iban", name="bank_accounts_firm_iban"),
    )
    op.create_table(
        "bank_statements",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "firm_id", sa.Uuid(), sa.ForeignKey("firms.id", ondelete="CASCADE"), nullable=False, index=True
        ),
        sa.Column(
            "account_id",
            sa.Uuid(),
            sa.ForeignKey("bank_accounts.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("format", sa.String(8), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False, server_default=""),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("from_date", sa.Date(), nullable=True),
        sa.Column("to_date", sa.Date(), nullable=True),
        sa.Column("opening_balance", sa.Numeric(18, 2), nullable=True),
        sa.Column("closing_balance", sa.Numeric(18, 2), nullable=True),
        sa.Column("transactions_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("imported_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("firm_id", "sha256", name="bank_statements_firm_sha256"),
    )
    op.create_table(
        "bank_transactions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "firm_id", sa.Uuid(), sa.ForeignKey("firms.id", ondelete="CASCADE"), nullable=False, index=True
        ),
        sa.Column(
            "account_id",
            sa.Uuid(),
            sa.ForeignKey("bank_accounts.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "statement_id", sa.Uuid(), sa.ForeignKey("bank_statements.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("external_id", sa.String(120), nullable=False),
        sa.Column("booking_date", sa.Date(), nullable=False, index=True),
        sa.Column("value_date", sa.Date(), nullable=True),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="EUR"),
        sa.Column("counterparty_name", sa.String(200), nullable=True),
        sa.Column("counterparty_iban", sa.String(34), nullable=True),
        sa.Column("remittance_info", sa.String(1000), nullable=True),
        sa.Column("end_to_end_id", sa.String(70), nullable=True),
        sa.Column("match_status", sa.String(16), nullable=False, server_default="unmatched"),
        sa.Column("raw", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("account_id", "external_id", name="bank_transactions_account_ext"),
    )
    op.create_table(
        "payment_matches",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "firm_id", sa.Uuid(), sa.ForeignKey("firms.id", ondelete="CASCADE"), nullable=False, index=True
        ),
        sa.Column(
            "transaction_id",
            sa.Uuid(),
            sa.ForeignKey("bank_transactions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("score", sa.Numeric(4, 3), nullable=False),
        sa.Column("method", sa.String(8), nullable=False, server_default="auto"),
        sa.Column("status", sa.String(16), nullable=False, server_default="suggested"),
        sa.Column("reasons", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("decided_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "bank_feeds",
        sa.Column("firm_id", sa.Uuid(), sa.ForeignKey("firms.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("provider", sa.String(16), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("config_enc", sa.LargeBinary(), nullable=True),
        sa.Column("settings", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "vida_reports",
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
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("format_version", sa.String(16), nullable=False, server_default="vida-drr-v0"),
        sa.Column("document_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("totals", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("storage_key", sa.String(512), nullable=False, server_default=""),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_ref", sa.String(200), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    for table in NEW_TABLES:
        for stmt in policy_sql(table):
            op.execute(stmt)
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {APP_ROLE}")
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}")


def downgrade() -> None:
    for table in reversed(NEW_TABLES):
        op.drop_table(table)
    op.drop_column("documents", "paid_at")
    op.drop_column("documents", "paid_amount")
    op.drop_column("documents", "payment_status")
    op.drop_index("ix_client_companies_tax_number", table_name="client_companies")
    op.drop_index("ix_client_companies_peppol_id", table_name="client_companies")
    op.drop_column("client_companies", "tax_number")
    op.drop_column("client_companies", "peppol_id")
