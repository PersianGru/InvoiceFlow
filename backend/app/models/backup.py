"""Backups, restores and recovery keys."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, LargeBinary, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, new_uuid


class Backup(Base):
    __tablename__ = "backups"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    # the firm (or the platform row) the backup belongs to; kept when the firm itself is erased
    owner_id: Mapped[uuid.UUID] = mapped_column(index=True)
    kind: Mapped[str] = mapped_column(String(16))  # firm | platform
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|running|done|failed
    trigger: Mapped[str] = mapped_column(String(16), default="manual")  # manual|schedule|pre_restore
    target: Mapped[str] = mapped_column(String(16), default="storage")  # storage | s3
    object_key: Mapped[str] = mapped_column(String(512), default="")
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    schema_revision: Mapped[str] = mapped_column(String(32), default="")
    app_version: Mapped[str] = mapped_column(String(32), default="")
    counts: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    password_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    reveals_used: Mapped[int] = mapped_column(Integer, default=0)
    recovery_key_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RecoveryKey(Base):
    """The public half of a firm's (or the platform's) age recovery key; the private half is shown once."""

    __tablename__ = "recovery_keys"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firms.id", ondelete="CASCADE"), index=True)
    public_key: Mapped[str] = mapped_column(String(100))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Restore(Base):
    __tablename__ = "restores"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    owner_id: Mapped[uuid.UUID] = mapped_column(index=True)
    kind: Mapped[str] = mapped_column(String(16))  # firm | platform
    backup_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    upload_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    mode: Mapped[str] = mapped_column(String(16), default="replace")  # new | replace
    reason: Mapped[str] = mapped_column(Text, default="")
    secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)  # password for the job
    pre_backup_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    counts: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
