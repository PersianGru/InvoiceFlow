"""Tables: per-firm connector configuration and the push run log."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, LargeBinary, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class FirmConnector(Base):
    """A connector the firm set up. Secret fields live in ``config_enc`` (AES-GCM with the firm's
    data key); non-secret settings in ``settings``."""

    __tablename__ = "firm_connectors"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firms.id", ondelete="CASCADE"), primary_key=True)
    key: Mapped[str] = mapped_column(String(32), primary_key=True)  # datev | lexoffice | …
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_push: Mapped[bool] = mapped_column(Boolean, default=False)
    config_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_test_message: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ClientConnectorCredential(Base):
    """Per-client credentials of a connector whose receiving side is scoped to the client company
    (KSeF: one token per NIP). Secrets encrypted with the firm's data key; the cursor is the
    receive position."""

    __tablename__ = "client_connector_credentials"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firms.id", ondelete="CASCADE"), primary_key=True)
    client_company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("client_companies.id", ondelete="CASCADE"), primary_key=True
    )
    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    config_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    cursor: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_pull_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConnectorRun(Base):
    __tablename__ = "connector_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firms.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(32))
    document_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    trigger: Mapped[str] = mapped_column(String(16), default="manual")  # manual | auto | receive
    status: Mapped[str] = mapped_column(String(16))  # ok | error | skipped
    external_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    message: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
