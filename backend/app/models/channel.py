"""Tables: sender → client mappings (with pairing codes) and the channel message log."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, new_uuid

CHANNELS = ("email", "telegram", "whatsapp")


class SenderMapping(Base):
    """A known sender on a channel, bound to a client company (and optionally a client user).
    ``identity`` is NULL while a pairing code is waiting to be used."""

    __tablename__ = "sender_mappings"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firms.id", ondelete="CASCADE"), index=True)
    client_company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("client_companies.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    channel: Mapped[str] = mapped_column(String(16))  # email | telegram | whatsapp
    identity: Mapped[str | None] = mapped_column(String(320), nullable=True)  # normalised
    display_name: Mapped[str] = mapped_column(String(200), default="")
    pairing_code: Mapped[str | None] = mapped_column(String(16), unique=True, nullable=True)
    pairing_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # off while the bound user is deactivated
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # "person" (a sender) or "chat" (a Telegram group or channel: the chat decides the
    # client company); the bot the pairing came through (None = the platform bot)
    scope: Mapped[str] = mapped_column(String(8), default="person")
    bot_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    accept_from: Mapped[str] = mapped_column(String(16), default="anyone")  # chats: anyone | paired


class ChannelMessage(Base):
    """Every inbound and outbound channel message (short text only, never attachments)."""

    __tablename__ = "channel_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    firm_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("firms.id", ondelete="CASCADE"), nullable=True, index=True
    )
    channel: Mapped[str] = mapped_column(String(16))
    direction: Mapped[str] = mapped_column(String(3))  # in | out
    sender_identity: Mapped[str] = mapped_column(String(320), default="")
    external_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    document_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    kind: Mapped[str] = mapped_column(
        String(24)
    )  # document | receipt | paused | unknown | question | answer | status | help | pairing | error
    body: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
