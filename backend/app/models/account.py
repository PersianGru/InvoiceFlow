"""Account tables: one-time links (invite, password reset), deleted-user tombstones and the
platform's first-run state."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, LargeBinary, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, new_uuid

LINK_KINDS = ("invite", "reset")


class UserLink(Base):
    """A one-time link (invite or password reset). Only the SHA-256 of the token is stored."""

    __tablename__ = "user_links"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    firm_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("firms.id", ondelete="CASCADE"), nullable=True, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserTombstone(Base):
    """Who a deleted user was, sealed with the firm's data key; readable by the firm owner only."""

    __tablename__ = "user_tombstones"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firms.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(index=True)  # the placeholder row keeps this id
    label: Mapped[str] = mapped_column(String(64))  # "Deleted user #a1b2"
    identity_enc: Mapped[bytes] = mapped_column(LargeBinary)  # {"email", "display_name", "role"}
    reason: Mapped[str] = mapped_column(Text, default="")
    deleted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    erase_after: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PlatformState(Base):
    """Singleton row (id = 1): the first-run setup nonce until a super admin exists."""

    __tablename__ = "platform_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    setup_nonce: Mapped[str | None] = mapped_column(String(64), nullable=True)
    setup_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    platform_firm_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
