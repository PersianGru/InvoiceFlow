"""Trash requests: a bulk trash of more than 5 documents waits for a firm owner."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, new_uuid


class TrashRequest(Base):
    __tablename__ = "trash_requests"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firms.id", ondelete="CASCADE"), index=True)
    requested_by_user_id: Mapped[uuid.UUID] = mapped_column()
    reason: Mapped[str] = mapped_column(Text)
    document_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    filter: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|executed|rejected|expired
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    self_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    executed_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
