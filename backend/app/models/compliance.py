"""Tables: per-firm SSO configuration and the short-lived SSO login records."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, LargeBinary, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, new_uuid


class FirmSso(Base):
    """OIDC settings of a firm. The client secret is encrypted with the firm's data key."""

    __tablename__ = "firm_sso"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firms.id", ondelete="CASCADE"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    issuer: Mapped[str] = mapped_column(String(500), default="")
    client_id: Mapped[str] = mapped_column(String(300), default="")
    client_secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    allowed_domains: Mapped[list[str]] = mapped_column(JSONB, default=list)
    provisioning: Mapped[str] = mapped_column(String(16), default="invited_only")  # invited_only | auto
    default_role: Mapped[str] = mapped_column(String(32), default="junior_accountant")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SsoLogin(Base):
    """One started SSO sign-in: state, nonce and PKCE verifier live here, never in the browser."""

    __tablename__ = "sso_logins"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firms.id", ondelete="CASCADE"), index=True)
    state: Mapped[str] = mapped_column(String(64), unique=True)
    nonce: Mapped[str] = mapped_column(String(64))
    code_verifier: Mapped[str] = mapped_column(String(128))
    redirect_to: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
