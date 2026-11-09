from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, new_uuid


class DocumentChannel(StrEnum):
    upload = "upload"
    email = "email"
    whatsapp = "whatsapp"
    telegram = "telegram"
    api = "api"
    peppol = "peppol"
    ksef = "ksef"
    pdp = "pdp"  # French certified platform (PDP)


class DocumentStatus(StrEnum):
    received = "received"
    processing = "processing"
    needs_review = "needs_review"
    needs_info = "needs_info"
    approved = "approved"
    rejected = "rejected"
    quarantined = "quarantined"
    failed = "failed"


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firms.id", ondelete="CASCADE"), index=True)
    channel_bot_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)  # D: the bot it came through
    client_company_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("client_companies.id", ondelete="SET NULL"), nullable=True, index=True
    )
    channel: Mapped[DocumentChannel] = mapped_column(
        Enum(DocumentChannel, name="document_channel", native_enum=False, length=16)
    )
    sender_identity: Mapped[str] = mapped_column(String(320), default="")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    original_filename: Mapped[str] = mapped_column(String(255), default="")
    mime_type: Mapped[str] = mapped_column(String(127), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    storage_key: Mapped[str] = mapped_column(String(512))
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, name="document_status", native_enum=False, length=16),
        default=DocumentStatus.received,
    )
    uploaded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    extracted: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    retention_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processing_restricted: Mapped[bool] = mapped_column(Boolean, default=False)  # GDPR "restrict processing"
    erasure_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retention_rule: Mapped[str | None] = mapped_column(String(64), nullable=True)  # e.g. DE:10y:2026.11
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # trash
    deleted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    delete_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    purge_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # summary columns filled by the pipeline (inbox filters, duplicate detection)
    document_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    direction: Mapped[str | None] = mapped_column(String(16), nullable=True)
    invoice_number: Mapped[str | None] = mapped_column(String(128), nullable=True)
    supplier_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    supplier_vat_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    issue_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    gross_total: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    review_status: Mapped[str] = mapped_column(String(16), default="pending")
    min_confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    phash: Mapped[str | None] = mapped_column(String(16), nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    structured_format: Mapped[str | None] = mapped_column(String(16), nullable=True)
    language: Mapped[str | None] = mapped_column(String(8), nullable=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    assigned_to_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # payment (bank reconciliation): unpaid | partial | paid
    payment_status: Mapped[str] = mapped_column(String(16), default="unpaid")
    paid_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    paid_at: Mapped[date | None] = mapped_column(Date, nullable=True)
