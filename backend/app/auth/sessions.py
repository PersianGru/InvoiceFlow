"""Server-side sessions: a random token in an HttpOnly cookie (or a bearer header for API clients);
only its SHA-256 is stored."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User, UserSession

COOKIE_NAME = "ivf_session"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def create_session(session: AsyncSession, user: User, ttl_seconds: int, *, mfa_passed: bool) -> str:
    token = secrets.token_urlsafe(32)
    row = UserSession(
        firm_id=user.firm_id,
        user_id=user.id,
        token_hash=_hash(token),
        expires_at=datetime.now(UTC) + timedelta(seconds=ttl_seconds),
        mfa_passed=mfa_passed,
    )
    session.add(row)
    await session.flush()
    return token


async def resolve_session(session: AsyncSession, token: str) -> tuple[User, UserSession] | None:
    """Caller must have the platform bypass set (the firm is not known before the lookup)."""
    if not token or len(token) > 128:
        return None
    row = (
        await session.execute(select(UserSession).where(UserSession.token_hash == _hash(token)))
    ).scalar_one_or_none()
    if row is None or row.revoked_at is not None or row.expires_at <= datetime.now(UTC):
        return None
    user = await session.get(User, row.user_id)
    if user is None or not user.is_active:
        return None
    return user, row


async def revoke_session(session: AsyncSession, token: str) -> None:
    row = (
        await session.execute(select(UserSession).where(UserSession.token_hash == _hash(token)))
    ).scalar_one_or_none()
    if row is not None:
        row.revoked_at = datetime.now(UTC)
