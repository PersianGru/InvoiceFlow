"""License bookkeeping tables beside the columns on ``firms``."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LicenseEvent(Base):
    """Every license state change and hub answer per firm (decisions only, never key or token)."""

    __tablename__ = "license_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firms.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(
        String(32)
    )  # activate | validate | deactivate | offline | revocation | state
    outcome: Mapped[str] = mapped_column(String(64))  # ok | <hub error code> | network_error | ...
    state_before: Mapped[str | None] = mapped_column(String(16), nullable=True)
    state_after: Mapped[str | None] = mapped_column(String(16), nullable=True)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RevocationListCache(Base):
    """The last verified revocation list (spec §10), one row for the whole platform."""

    __tablename__ = "revocation_list_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    jws: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[int] = mapped_column(BigInteger)
    list_exp: Mapped[int] = mapped_column(BigInteger)
