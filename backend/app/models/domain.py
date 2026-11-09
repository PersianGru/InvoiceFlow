"""Inbound email domains: the platform's verified root domain, each firm's platform
subdomain under it and each firm's own verified domain."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, new_uuid

DOMAIN_KINDS = ("root", "platform_sub", "custom")


class InboundDomain(Base):
    __tablename__ = "inbound_domains"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firms.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # root | platform_sub | custom
    domain: Mapped[str] = mapped_column(String(253))
    label: Mapped[str | None] = mapped_column(String(63), nullable=True)  # platform_sub: the part before root
    verification_token: Mapped[str] = mapped_column(String(64))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | verified | warning
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_check_error: Mapped[str | None] = mapped_column(String(200), nullable=True)
    mx_hosts: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    grace_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    @property
    def usable(self) -> bool:
        """Receives mail now: verified (or warned after a lost record) and not retired."""
        return self.retired_at is None and self.status in ("verified", "warning")
