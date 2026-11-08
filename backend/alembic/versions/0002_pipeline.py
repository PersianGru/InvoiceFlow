"""pipeline: extractions, document events, supplier profiles, ai call log, fx rates, vat checks

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-04
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.db.rls import append_only_policy_sql, policy_sql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

APP_ROLE = "invoiceflow_app"


def upgrade() -> None:
    # firm-level pipeline settings
    op.add_column(
        "firms", sa.Column("confidence_threshold", sa.Numeric(4, 3), nullable=False, server_default="0.850")
    )
    op.add_column("firms", sa.Column("auto_approve", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column(
        "firms", sa.Column("cloud_ai_opt_in", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.add_column("firms", sa.Column("base_currency", sa.String(3), nullable=False, server_default="EUR"))

    # document summary columns (indexed for inbox filters and duplicate detection)
    for col in (
        sa.Column("document_type", sa.String(32), nullable=True),
        sa.Column("direction", sa.String(16), nullable=True),
        sa.Column("invoice_number", sa.String(128), nullable=True),
        sa.Column("supplier_name", sa.String(200), nullable=True),
        sa.Column("supplier_vat_id", sa.String(32), nullable=True),
        sa.Column("issue_date", sa.Date(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("gross_total", sa.Numeric(18, 2), nullable=True),
        sa.Column("review_status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("min_confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("phash", sa.String(16), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("structured_format", sa.String(16), nullable=True),
        sa.Column("language", sa.String(8), nullable=True),
        sa.Column("error", sa.String(500), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
    ):
        op.add_column("documents", col)
    op.create_index("ix_documents_firm_status", "documents", ["firm_id", "status"])
    op.create_index("ix_documents_firm_dedupe", "documents", ["firm_id", "supplier_vat_id", "invoice_number"])
    op.create_index("ix_documents_firm_phash", "documents", ["firm_id", "phash"])

    op.create_table(
        "extractions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "firm_id", sa.Uuid(), sa.ForeignKey("firms.id", ondelete="CASCADE"), nullable=False, index=True
        ),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(120), nullable=True),
        sa.Column("prompt_version", sa.String(32), nullable=True),
        sa.Column("invoice", postgresql.JSONB(), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "document_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "firm_id", sa.Uuid(), sa.ForeignKey("firms.id", ondelete="CASCADE"), nullable=False, index=True
        ),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("step", sa.String(48), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("detail", postgresql.JSONB(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "supplier_profiles",
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
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("name_normalized", sa.String(200), nullable=False, index=True),
        sa.Column("vat_id", sa.String(32), nullable=True, index=True),
        sa.Column("ibans", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("address", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("country", sa.String(2), nullable=True),
        sa.Column("field_rules", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("examples", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("stats", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("document_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("correction_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("default_gl_account", sa.String(32), nullable=True),
        sa.Column("default_cost_category", sa.String(64), nullable=True),
        sa.Column("default_vat_code", sa.String(16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "ai_call_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "firm_id", sa.Uuid(), sa.ForeignKey("firms.id", ondelete="CASCADE"), nullable=False, index=True
        ),
        sa.Column("document_id", sa.Uuid(), nullable=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(120), nullable=True),
        sa.Column("purpose", sa.String(64), nullable=False),
        sa.Column("data_categories", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("external", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("outcome", sa.String(32), nullable=False, server_default="ok"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "fx_rates",
        sa.Column("rate_date", sa.Date(), primary_key=True),
        sa.Column("currency", sa.String(3), primary_key=True),
        sa.Column("rate", sa.Numeric(18, 6), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "vat_checks",
        sa.Column("vat_id", sa.String(32), primary_key=True),
        sa.Column("valid", sa.Boolean(), nullable=True),
        sa.Column("name", sa.String(200), nullable=True),
        sa.Column("address", sa.String(400), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False, server_default="vies"),
    )

    for stmt in policy_sql("extractions") + policy_sql("supplier_profiles"):
        op.execute(stmt)
    for stmt in append_only_policy_sql("document_events") + append_only_policy_sql("ai_call_log"):
        op.execute(stmt)
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON extractions, supplier_profiles, fx_rates, vat_checks TO {APP_ROLE}"
    )
    op.execute(f"GRANT SELECT, INSERT ON document_events, ai_call_log TO {APP_ROLE}")
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}")


def downgrade() -> None:
    for t in ("vat_checks", "fx_rates", "ai_call_log", "supplier_profiles", "document_events", "extractions"):
        op.drop_table(t)
    for idx in ("ix_documents_firm_phash", "ix_documents_firm_dedupe", "ix_documents_firm_status"):
        op.drop_index(idx, table_name="documents")
    for col in (
        "document_type",
        "direction",
        "invoice_number",
        "supplier_name",
        "supplier_vat_id",
        "issue_date",
        "currency",
        "gross_total",
        "review_status",
        "min_confidence",
        "phash",
        "page_count",
        "structured_format",
        "language",
        "error",
        "processed_at",
    ):
        op.drop_column("documents", col)
    for col in ("confidence_threshold", "auto_approve", "cloud_ai_opt_in", "base_currency"):
        op.drop_column("firms", col)
