"""Settings kept in the database instead of the environment.

A row belongs to a firm or to the platform row (``firms.kind = 'platform'``). Plain fields are JSON;
secret fields are sealed together with the owning row's data key and never leave the API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, LargeBinary, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, new_uuid


class ConfigEntry(Base):
    __tablename__ = "config_entries"
    __table_args__ = (UniqueConstraint("firm_id", "key", name="config_entries_firm_key"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firms.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(64))
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    secrets_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="dashboard")  # dashboard | environment
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
