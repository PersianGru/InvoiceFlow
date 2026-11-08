"""dashboard: assignment, comments, inbox indexes

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-04
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.db.rls import policy_sql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

APP_ROLE = "invoiceflow_app"


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column(
            "assigned_to_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
    )
    op.add_column("documents", sa.Column("reviewed_by_user_id", sa.Uuid(), nullable=True))
    op.add_column("documents", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_documents_firm_assigned", "documents", ["firm_id", "assigned_to_user_id"])
    op.create_index("ix_documents_firm_issue_date", "documents", ["firm_id", "issue_date"])
    op.create_index("ix_documents_firm_supplier", "documents", ["firm_id", "supplier_name"])

    op.create_table(
        "document_comments",
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
        sa.Column("author_user_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False, server_default="note"),  # note | question | answer
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("visible_to_client", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    for stmt in policy_sql("document_comments"):
        op.execute(stmt)
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON document_comments TO {APP_ROLE}")
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}")
    _ = postgresql


def downgrade() -> None:
    op.drop_table("document_comments")
    for idx in ("ix_documents_firm_supplier", "ix_documents_firm_issue_date", "ix_documents_firm_assigned"):
        op.drop_index(idx, table_name="documents")
    for col in ("reviewed_at", "reviewed_by_user_id", "assigned_to_user_id"):
        op.drop_column("documents", col)
