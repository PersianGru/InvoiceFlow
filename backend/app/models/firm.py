"""The accounting firm: the tenant and the licensed unit."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, LargeBinary, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, new_uuid


class Firm(Base):
    __tablename__ = "firms"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(200))
    country: Mapped[str] = mapped_column(String(2), default="DE")
    locale: Mapped[str] = mapped_column(String(8), default="en")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    ai_routing_policy: Mapped[str] = mapped_column(String(32), default="local_only")
    retention_days: Mapped[int] = mapped_column(Integer, default=3650)
    confidence_threshold: Mapped[Decimal] = mapped_column(Numeric(4, 3), default=Decimal("0.850"))
    auto_approve: Mapped[bool] = mapped_column(Boolean, default=False)
    cloud_ai_opt_in: Mapped[bool] = mapped_column(Boolean, default=False)
    cloud_ai_monthly_cap: Mapped[int] = mapped_column(Integer, default=0)  # 0 = unlimited
    base_currency: Mapped[str] = mapped_column(String(3), default="EUR")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Per-tenant encryption
    data_key_wrapped: Mapped[bytes] = mapped_column(LargeBinary)

    # Sealed installation id (spec §7.1): made once, never changed
    instance_id: Mapped[str] = mapped_column(String(38), unique=True)
    instance_fp: Mapped[str] = mapped_column(String(8))
    instance_fp_source: Mapped[str] = mapped_column(String(16), default="random")  # machine-id | random

    # License bookkeeping; key and token encrypted with the firm's data key
    license_key_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    license_token_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    license_use: Mapped[str | None] = mapped_column(String(8), nullable=True)  # lease | offline
    license_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # sub
    activation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # act
    key_hint: Mapped[str | None] = mapped_column(String(5), nullable=True)  # kh
    license_state: Mapped[str] = mapped_column(String(16), default="unlicensed")
    license_state_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_seen: Mapped[int] = mapped_column(
        BigInteger, default=0
    )  # largest unix time ever seen (clock rollback)
    last_refresh_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_validate_call_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    next_refresh_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    refresh_failures: Mapped[int] = mapped_column(Integer, default=0)
    backoff_until: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    blocked_until: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )  # instance_blocked daily retry
    reactivation_attempted_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    hub_message: Mapped[str | None] = mapped_column(String(500), nullable=True)  # last hub error, for the UI
    hub_error: Mapped[str | None] = mapped_column(String(64), nullable=True)
    shown_message_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    revoked_by_list_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    def __repr__(self) -> str:  # never includes secrets
        return f"<Firm {self.id} state={self.license_state} kh={self.key_hint}>"
