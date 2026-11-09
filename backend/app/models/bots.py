"""Telegram bots and WhatsApp numbers per platform, firm or client company, the
company a multi-company sender chose, and files held until that choice is made."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, LargeBinary, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, new_uuid


class ChannelBot(Base):
    """A Telegram bot or a WhatsApp number. Owner: the platform row, a firm, or (``client_company_id``)
    one client company (dedicated: everything it receives belongs to that client company)."""

    __tablename__ = "channel_bots"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firms.id", ondelete="CASCADE"), index=True)
    client_company_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("client_companies.id", ondelete="CASCADE"), nullable=True, index=True
    )
    channel: Mapped[str] = mapped_column(String(16))  # telegram | whatsapp
    display_name: Mapped[str] = mapped_column(String(200), default="")
    public: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # username, phone_number_id …
    secrets_enc: Mapped[bytes] = mapped_column(LargeBinary)
    accept_from: Mapped[str] = mapped_column(String(16), default="anyone")  # anyone | paired
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SenderSelection(Base):
    """The client company a person paired to several chose last (``/company``)."""

    __tablename__ = "sender_selections"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firms.id", ondelete="CASCADE"), index=True)
    channel: Mapped[str] = mapped_column(String(16))
    identity: Mapped[str] = mapped_column(String(320), index=True)
    client_company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("client_companies.id", ondelete="CASCADE")
    )
    set_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class HeldMessage(Base):
    """Files from a multi-company sender waiting for the answer to "which company?" (24 h)."""

    __tablename__ = "held_messages"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    channel: Mapped[str] = mapped_column(String(16))
    identity: Mapped[str] = mapped_column(String(320), index=True)
    bot_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    reply_to: Mapped[str] = mapped_column(String(320), default="")
    options: Mapped[list[str]] = mapped_column(JSONB, default=list)  # client company ids, in order
    files: Mapped[list[dict[str, str]]] = mapped_column(JSONB, default=list)  # {name, key, mime}
    text: Mapped[str] = mapped_column(String(2000), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
