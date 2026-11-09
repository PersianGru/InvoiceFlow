from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, LargeBinary, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, new_uuid


class Role(StrEnum):
    super_admin = "super_admin"
    firm_owner = "firm_owner"
    accountant = "accountant"
    junior_accountant = "junior_accountant"
    client_admin = "client_admin"
    client_user = "client_user"
    api_key = "api_key"  # a public-API credential acting for the firm (never a users row)


FIRM_STAFF_ROLES = frozenset({Role.firm_owner, Role.accountant, Role.junior_accountant})
CLIENT_ROLES = frozenset({Role.client_admin, Role.client_user})


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    firm_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("firms.id", ondelete="CASCADE"), nullable=True
    )
    client_company_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("client_companies.id", ondelete="CASCADE"), nullable=True
    )
    email: Mapped[str] = mapped_column(String(320), unique=True)
    display_name: Mapped[str] = mapped_column(String(200), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(Enum(Role, name="user_role", native_enum=False, length=32))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    locale: Mapped[str] = mapped_column(String(8), default="en")
    totp_secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    last_failed_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # one-time recovery codes (SHA-256 each), deactivation and deletion
    recovery_codes: Mapped[list[str]] = mapped_column(JSONB, default=list)
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    password_set_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def has_password(self) -> bool:
        return bool(self.password_hash) and not self.password_hash.startswith("!")

    @property
    def status(self) -> str:
        if self.deleted_at is not None:
            return "deleted"
        if not self.is_active:
            return "deactivated"
        if not self.has_password:
            return "invited"
        return "active"


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    firm_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("firms.id", ondelete="CASCADE"), nullable=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    mfa_passed: Mapped[bool] = mapped_column(Boolean, default=False)
    # a super admin working inside a firm; None = platform view
    acting_firm_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("firms.id", ondelete="SET NULL"), nullable=True
    )
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
